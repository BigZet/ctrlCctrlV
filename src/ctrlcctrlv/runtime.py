"""Verified, resumable installs and an owned loopback-only inference process."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time

import httpx

from .catalog import Artifact, ENGINE, ENGINE_VERSION, MODELS
from .core import Cancelled, OperationError


def check_cancel(cancel: threading.Event):
    if cancel.is_set():
        raise Cancelled("Операция отменена")


def file_hash(path: Path, cancel: threading.Event) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            check_cancel(cancel)
            digest.update(chunk)
    check_cancel(cancel)
    return digest.hexdigest()


def download(artifact: Artifact, destination: Path, cancel: threading.Event, progress, transport=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    if destination.exists() and destination.stat().st_size == artifact.size:
        if file_hash(destination, cancel) == artifact.sha256:
            progress(artifact.filename, artifact.size, artifact.size)
            return
    offset = part.stat().st_size if part.exists() else 0
    if offset > artifact.size:
        part.unlink()
        offset = 0
    needed = max(0, artifact.size - offset) + 64 * 1024 * 1024
    if shutil.disk_usage(destination.parent).free < needed:
        raise OperationError("Недостаточно места для загрузки модели")
    check_cancel(cancel)
    if offset < artifact.size:
        with httpx.Client(follow_redirects=True, timeout=30, transport=transport) as client:
            for attempt in range(4):
                check_cancel(cancel)
                offset = part.stat().st_size if part.exists() else 0
                if offset == artifact.size:
                    break
                headers = {"Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                retry_delay = min(0.5 * 2 ** attempt, 4)
                try:
                    with client.stream("GET", artifact.url, headers=headers) as response:
                        if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                            retry_after = response.headers.get("Retry-After", "")
                            if retry_after.isdecimal():
                                retry_delay = min(max(float(retry_after), retry_delay), 10)
                            raise httpx.TransportError("Temporary download failure")
                        if response.status_code not in (200, 206):
                            raise OperationError(f"Ошибка загрузки HTTP {response.status_code}")
                        if response.status_code == 206:
                            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                            if not match or int(match[1]) != offset or int(match[3]) != artifact.size or int(match[2]) != artifact.size - 1:
                                raise OperationError("Сервер вернул неверный диапазон загрузки")
                        else:
                            # A server may ignore Range. Recheck space before replacing the
                            # partial file, since a full response needs more free space.
                            if shutil.disk_usage(destination.parent).free + offset < artifact.size + 64 * 1024 * 1024:
                                raise OperationError("Недостаточно места для загрузки модели")
                            offset = 0
                        with part.open("ab" if offset else "wb") as stream:
                            progress(artifact.filename, offset, artifact.size)
                            for chunk in response.iter_bytes(256 * 1024):
                                check_cancel(cancel)
                                if offset + len(chunk) > artifact.size:
                                    raise OperationError("Размер файла не совпадает с каталогом")
                                stream.write(chunk)
                                offset += len(chunk)
                                progress(artifact.filename, offset, artifact.size)
                    if offset != artifact.size:
                        raise httpx.TransportError("Incomplete download")
                    break
                except httpx.HTTPError:
                    check_cancel(cancel)
                    if attempt == 3:
                        raise OperationError("Соединение прервано. Загруженная часть сохранена; повторите загрузку.") from None
                    progress(f"Повтор подключения {attempt + 1}/3 · загруженная часть сохранена", offset, artifact.size)
                    if cancel.wait(retry_delay):
                        check_cancel(cancel)
    if not part.exists() or part.stat().st_size != artifact.size:
        raise OperationError("Загрузка не завершена; её можно возобновить")
    progress("Проверка SHA256: " + artifact.filename, artifact.size, artifact.size)
    if file_hash(part, cancel) != artifact.sha256:
        part.unlink()
        raise OperationError("Контрольная сумма не совпала. Файл удалён; повторите загрузку.")
    check_cancel(cancel)
    part.replace(destination)


class LocalRuntime:
    def __init__(self, directory: Path, session="ocr", *, slots=1):
        if session not in {"ocr", "review"}:
            raise ValueError("Unknown runtime session")
        if not isinstance(slots, int) or isinstance(slots, bool) or not 1 <= slots <= 8:
            raise ValueError("Runtime slots must be between 1 and 8")
        self.slots = slots
        self.key_filename = "session-key" if session == "ocr" else "session-key-review"
        self.directory = directory
        self.process = None
        self.model_id = None
        self.endpoint = None
        self.key = None
        self.ready = False
        self._lock = threading.RLock()

    @property
    def engine_directory(self):
        return self.directory / "runtime" / ENGINE_VERSION

    @property
    def binary(self):
        candidates = list(self.engine_directory.rglob("llama-server")) if self.engine_directory.exists() else []
        return candidates[0] if candidates else None

    def installed(self, model_id):
        model = MODELS[model_id]
        directory = self.directory / "models" / model_id
        try:
            receipt = json.loads((directory / "verified.json").read_text())
            return all(
                (directory / f.filename).stat().st_size == f.size
                and receipt[f.filename] == {"sha256": f.sha256, "size": f.size,
                    "mtime_ns": (directory / f.filename).stat().st_mtime_ns}
                for f in model.files
            )
        except (OSError, KeyError, TypeError, ValueError):
            return False

    def install(self, model_id, cancel, progress):
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise OperationError("Эта сборка локального движка предназначена для Mac ARM")
        if not self.binary:
            archive = self.directory / "downloads" / ENGINE.filename
            download(ENGINE, archive, cancel, progress)
            progress("Установка движка", 0, 1)
            # Extract to staging and apply Python's data filter (including link checks).
            self.engine_directory.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.engine_directory.parent) as staging:
                with tarfile.open(archive) as tar:
                    tar.extractall(staging, filter="data")
                check_cancel(cancel)
                shutil.rmtree(self.engine_directory, ignore_errors=True)
                shutil.copytree(staging, self.engine_directory)
            if not self.binary:
                raise OperationError("В пакете движка нет llama-server")
            self.binary.chmod(self.binary.stat().st_mode | 0o100)
        model = MODELS[model_id]
        directory = self.directory / "models" / model_id
        for artifact in model.files:
            download(artifact, directory / artifact.filename, cancel, progress)
        check_cancel(cancel)
        receipt = {f.filename: {"sha256": f.sha256, "size": f.size,
            "mtime_ns": (directory / f.filename).stat().st_mtime_ns} for f in model.files}
        temporary = directory / "verified.json.tmp"
        temporary.write_text(json.dumps(receipt))
        temporary.replace(directory / "verified.json")

    def start(self, model_id, cancel, progress):
        check_cancel(cancel)
        self.stop()
        if not self.installed(model_id) or not self.binary:
            raise OperationError("Сначала установите выбранную модель через настройки")
        model = MODELS[model_id]
        directory = self.directory / "models" / model_id
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        key = secrets.token_urlsafe(32)
        keyfile = self.directory / "runtime" / self.key_filename
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        command = [str(self.binary), "-m", str(directory / model.files[0].filename),
            "--mmproj", str(directory / model.files[1].filename),
            "--host", "127.0.0.1", "--port", str(port), "--api-key-file", str(keyfile),
            "-c", str(8192 * self.slots), "-ngl", "99", "--parallel", str(self.slots), "--no-webui"]
        endpoint = f"http://127.0.0.1:{port}"
        try:
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(key)
            with self._lock:
                check_cancel(cancel)
                self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                process = self.process
            progress("Загрузка модели в память…", 0, 1)
            deadline = time.monotonic() + 180
            with httpx.Client(trust_env=False, timeout=2) as client:
                while time.monotonic() < deadline:
                    check_cancel(cancel)
                    if process.poll() is not None:
                        raise OperationError(f"Движок завершился при загрузке (код {process.returncode})")
                    try:
                        response = client.get(endpoint + "/health", headers={"Authorization": "Bearer " + key})
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    cancel.wait(0.25)
                else:
                    raise OperationError("Модель не загрузилась за 180 секунд")
                progress("Прогрев модели…", 0, 1)
                # Warm both text and vision with a small generated PNG before enabling OCR.
                from PySide6.QtCore import QBuffer, QIODevice
                from PySide6.QtGui import QImage
                from .core import Operation
                from .ocr import OCRProvider
                image = QImage(96, 96, QImage.Format.Format_RGB32)
                image.fill(0xffffffff)
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                image.save(buffer, "PNG")
                warm = Operation(budget=120, cancelled=cancel)
                OCRProvider(endpoint + "/v1/chat/completions", model_id, key, 128, True, model.mode).recognize(bytes(buffer.data()), warm)
            with self._lock:
                check_cancel(cancel)
                if self.process is not process or process.poll() is not None:
                    raise Cancelled("Подготовка модели отменена")
                self.model_id, self.endpoint, self.key, self.ready = model_id, endpoint, key, True
        except OSError:
            self.stop()
            raise OperationError("Не удалось запустить движок. Проверьте установку локальной модели.") from None
        except BaseException:
            self.stop()
            raise

    def stop(self):
        with self._lock:
            self.ready = False
            process = self.process
            self.model_id = self.endpoint = self.key = None
            try:
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                raise OperationError("Не удалось завершить движок. Повторите выгрузку модели.") from None
            finally:
                # Keep the owned handle if termination failed so another stop can
                # retry. Never lose track of a running child or delete its key.
                if process is None or process.poll() is not None:
                    self.process = None
                    try:
                        (self.directory / "runtime" / self.key_filename).unlink(missing_ok=True)
                    except OSError:
                        pass

    def delete(self, model_id):
        if self.model_id == model_id:
            self.stop()
        shutil.rmtree(self.directory / "models" / model_id, ignore_errors=True)
