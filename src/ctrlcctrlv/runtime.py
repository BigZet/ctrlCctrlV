"""Verified, resumable installs and an owned loopback-only inference process."""
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
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
import zipfile

import httpx

from .catalog import Artifact, ENGINE_VERSION, MODELS, engine_artifacts
from .core import Cancelled, OperationError
from .security import protect_keyfile


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
    check_cancel(cancel)
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
            attempt = 0
            while True:
                check_cancel(cancel)
                offset = part.stat().st_size if part.exists() else 0
                if offset == artifact.size:
                    break
                headers = {"Accept-Encoding": "identity"}
                range_end = artifact.size - 1
                # Bounded ranges allow large packages through buffering proxies
                # without waiting for the entire archive before its first byte.
                if artifact.size > 32 * 1024 * 1024:
                    range_end = min(artifact.size - 1, offset + 16 * 1024 * 1024 - 1)
                    headers["Range"] = f"bytes={offset}-{range_end}"
                elif offset:
                    headers["Range"] = f"bytes={offset}-"
                retry_delay = min(0.5 * 2 ** attempt, 4)
                try:
                    with client.stream("GET", artifact.url, headers=headers) as response:
                        # A cancellation while waiting for headers must not discard
                        # resumable bytes when the server ignores Range.
                        check_cancel(cancel)
                        if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                            retry_after = response.headers.get("Retry-After", "")
                            if retry_after.isdecimal():
                                retry_delay = min(max(float(retry_after), retry_delay), 10)
                            raise httpx.TransportError("Temporary download failure")
                        if response.status_code not in (200, 206):
                            raise OperationError(f"Ошибка загрузки HTTP {response.status_code}")
                        if response.status_code == 206:
                            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                            if not match or int(match[1]) != offset or int(match[3]) != artifact.size or int(match[2]) != range_end:
                                raise OperationError("Сервер вернул неверный диапазон загрузки")
                        else:
                            # A server may ignore Range. Recheck space before replacing the
                            # partial file, since a full response needs more free space.
                            if shutil.disk_usage(destination.parent).free + offset < artifact.size + 64 * 1024 * 1024:
                                raise OperationError("Недостаточно места для загрузки модели")
                            offset = 0
                            range_end = artifact.size - 1
                        with part.open("ab" if offset else "wb") as stream:
                            progress(artifact.filename, offset, artifact.size)
                            for chunk in response.iter_bytes(256 * 1024):
                                check_cancel(cancel)
                                if offset + len(chunk) > range_end + 1:
                                    raise OperationError("Размер файла не совпадает с каталогом")
                                stream.write(chunk)
                                offset += len(chunk)
                                progress(artifact.filename, offset, artifact.size)
                    if offset != range_end + 1:
                        raise httpx.TransportError("Incomplete download")
                    attempt = 0
                    if offset == artifact.size:
                        break
                except httpx.HTTPError:
                    check_cancel(cancel)
                    if attempt == 3:
                        raise OperationError("Соединение прервано. Загруженная часть сохранена; повторите загрузку.") from None
                    progress(f"Повтор подключения {attempt + 1}/3 · загруженная часть сохранена", offset, artifact.size)
                    if cancel.wait(retry_delay):
                        check_cancel(cancel)
                    attempt += 1
    if not part.exists() or part.stat().st_size != artifact.size:
        raise OperationError("Загрузка не завершена; её можно возобновить")
    progress("Проверка SHA256: " + artifact.filename, artifact.size, artifact.size)
    if file_hash(part, cancel) != artifact.sha256:
        part.unlink()
        raise OperationError("Контрольная сумма не совпала. Файл удалён; повторите загрузку.")
    check_cancel(cancel)
    part.replace(destination)


def extract_engine(archive, destination, cancel):
    """Extract verified packages inside staging, with traversal/link checks."""
    check_cancel(cancel)
    if archive.suffix != ".zip":
        with tarfile.open(archive) as tar:
            for member in tar:
                check_cancel(cancel)
                tar.extract(member, destination, filter="data")
        return
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        total = sum(info.file_size for info in members)
        if shutil.disk_usage(destination).free < total + 64 * 1024 * 1024:
            raise OperationError("Недостаточно места для распаковки движка")
        for info in members:
            check_cancel(cancel)
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if (path.is_absolute() or not path.parts or any(
                    p in {"..", "."} or ":" in p or p.rstrip(" .") != p for p in path.parts)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise OperationError("Небезопасный путь в архиве движка")
            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise OperationError("Небезопасный путь в архиве движка")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as source, target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    check_cancel(cancel)
                    output.write(chunk)


class LocalRuntime:
    def __init__(self, directory: Path, session="ocr", *, slots=1, acceleration="gpu", models=None):
        self.models = models if models is not None else MODELS
        if session not in {"ocr", "review"}:
            raise ValueError("Unknown runtime session")
        if not isinstance(slots, int) or isinstance(slots, bool) or not 1 <= slots <= 8:
            raise ValueError("Runtime slots must be between 1 and 8")
        self.slots = slots
        if acceleration not in {"gpu", "cpu"}:
            raise ValueError("Unknown acceleration")
        self.acceleration = acceleration
        self.loaded_acceleration = None
        self.hardware = None
        self.key_filename = "session-key" if session == "ocr" else "session-key-review"
        self.directory = Path(directory).resolve()
        self.process = None
        self.model_id = None
        self.endpoint = None
        self.key = None
        self.ready = False
        self._lock = threading.RLock()

    @property
    def engine_directory(self):
        suffix = ("-windows-cuda" if self.acceleration == "gpu" else "-windows-cpu") if platform.system() == "Windows" else ""
        return self.directory / "runtime" / (ENGINE_VERSION + suffix)

    @property
    def binary(self):
        name = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
        candidates = sorted(self.engine_directory.rglob(name)) if self.engine_directory.exists() else []
        receipt = self.engine_directory / "verified-engine.json"
        if receipt.exists():
            try:
                with receipt.open("rb") as stream:
                    encoded = stream.read(524289)
                if len(encoded) > 524288:
                    return None
                files = json.loads(encoded)
                if not isinstance(files, dict) or not files:
                    return None
                for filename, record in files.items():
                    path = self.engine_directory / filename
                    if not path.resolve().is_relative_to(self.engine_directory.resolve()):
                        return None
                    stat = path.stat()
                    if record != {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}:
                        return None
            except (OSError, ValueError, TypeError):
                return None
        return candidates[0] if candidates else None

    def installed(self, model_id):
        model = self.models[model_id]
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
        if model_id not in self.models:
            raise OperationError("Неизвестная модель")
        try:
            artifacts = engine_artifacts(platform.system(), platform.machine(), self.acceleration)
        except ValueError as error:
            raise OperationError(str(error)) from None
        if not self.binary:
            archives = []
            for artifact in artifacts:
                archive = self.directory / "downloads" / artifact.filename
                download(artifact, archive, cancel, progress)
                archives.append(archive)
            self.engine_directory.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.engine_directory.parent) as staging:
                for archive in archives:
                    progress("Установка движка", 0, 1)
                    extract_engine(archive, Path(staging), cancel)
                check_cancel(cancel)
                name = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
                if not list(Path(staging).rglob(name)):
                    raise OperationError("В пакете движка нет " + name)
                receipt = {p.relative_to(staging).as_posix(): {"size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
                           for p in Path(staging).rglob("*") if p.is_file()}
                (Path(staging) / "verified-engine.json").write_text(json.dumps(receipt), encoding="utf-8")
                shutil.rmtree(self.engine_directory, ignore_errors=True)
                Path(staging).replace(self.engine_directory)
            if not self.binary:
                raise OperationError("В пакете движка нет llama-server")
            if platform.system() != "Windows":
                self.binary.chmod(self.binary.stat().st_mode | 0o100)
        model = self.models[model_id]
        directory = self.directory / "models" / model_id
        for artifact in model.files:
            download(artifact, directory / artifact.filename, cancel, progress)
        check_cancel(cancel)
        receipt = {f.filename: {"sha256": f.sha256, "size": f.size,
            "mtime_ns": (directory / f.filename).stat().st_mtime_ns} for f in model.files}
        temporary = directory / "verified.json.tmp"
        temporary.write_text(json.dumps(receipt))
        temporary.replace(directory / "verified.json")

    def preflight(self, model_id, cancel):
        from .hardware import inspect_hardware, require_capacity
        self.hardware = inspect_hardware(self.binary, self.acceleration, cancel)
        require_capacity(self.hardware, self.models[model_id], self.slots)
        return self.hardware

    def start(self, model_id, cancel, progress):
        check_cancel(cancel)
        self.stop()
        if not self.installed(model_id) or not self.binary:
            raise OperationError("Сначала установите выбранную модель через настройки")
        progress("Проверка оборудования и доступной памяти…", 0, 1)
        self.preflight(model_id, cancel)
        model = self.models[model_id]
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
            "-c", str(8192 * self.slots), "-ngl", "99" if self.acceleration == "gpu" else "0",
            "--parallel", str(self.slots), "--no-webui"]
        if self.acceleration == "cpu":
            command.append("--no-mmproj-offload")
        endpoint = f"http://127.0.0.1:{port}"
        try:
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as stream:
                protect_keyfile(keyfile, stream)
                stream.write(key)
            with self._lock:
                check_cancel(cancel)
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
                self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                cwd=self.binary.parent, **options)
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
                self.loaded_acceleration = self.acceleration
        except OSError:
            self.stop()
            raise OperationError("Не удалось запустить движок. Проверьте установку локальной модели.") from None
        except BaseException:
            self.stop()
            raise

    def stop(self):
        with self._lock:
            self.ready = False
            self.loaded_acceleration = None
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
        if model_id not in self.models:
            raise OperationError("Неизвестная модель")
        if self.model_id == model_id:
            self.stop()
        directory = self.directory / "models" / model_id
        if directory.exists():
            try:
                shutil.rmtree(directory)
            except OSError:
                raise OperationError("Не удалось удалить модель. Выгрузите её и повторите удаление.") from None
