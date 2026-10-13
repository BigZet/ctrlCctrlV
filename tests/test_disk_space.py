"""Storage preflights and cancellation must preserve recoverable install data."""
import hashlib
import threading
from types import SimpleNamespace
import zipfile

import httpx
import pytest

from ctrlcctrlv.catalog import Artifact
from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.runtime import download, extract_engine


RESERVE = 64 * 1024 * 1024
CONTENTS = b"0123456789"
OLD_CONTENTS = b"old installed content"


def model_artifact():
    return Artifact("model.gguf", "https://example.invalid/model",
                    hashlib.sha256(CONTENTS).hexdigest(), len(CONTENTS))


def disk_space(free):
    return SimpleNamespace(total=1024 * 1024 * 1024,
                           used=1024 * 1024 * 1024 - free, free=free)


@pytest.mark.parametrize("partial_bytes", [b"", b"0123"])
def test_insufficient_download_space_preserves_existing_and_partial_files(tmp_path, monkeypatch, partial_bytes):
    destination = tmp_path / "model.gguf"
    part = destination.with_suffix(".gguf.part")
    destination.write_bytes(OLD_CONTENTS)
    part.write_bytes(partial_bytes)
    free = RESERVE + len(CONTENTS) - len(partial_bytes) - 1
    monkeypatch.setattr("ctrlcctrlv.runtime.shutil.disk_usage", lambda _: disk_space(free))
    transport = httpx.MockTransport(lambda _: pytest.fail("Low space must stop before a request"))

    with pytest.raises(OperationError, match="Недостаточно места"):
        download(model_artifact(), destination, threading.Event(), lambda *args: None, transport)

    assert destination.read_bytes() == OLD_CONTENTS
    assert part.read_bytes() == partial_bytes


def test_ignored_range_rechecks_space_before_truncating_partial_file(tmp_path, monkeypatch):
    destination = tmp_path / "model.gguf"
    part = destination.with_suffix(".gguf.part")
    destination.write_bytes(OLD_CONTENTS)
    part.write_bytes(b"0123")
    free = [RESERVE + 6]
    monkeypatch.setattr("ctrlcctrlv.runtime.shutil.disk_usage", lambda _: disk_space(free[0]))

    def reply(request):
        assert request.headers["Range"] == "bytes=4-"
        # Another writer consumes space while the request is in flight.
        free[0] = RESERVE + 5
        return httpx.Response(200, content=CONTENTS)

    with pytest.raises(OperationError, match="Недостаточно места"):
        download(model_artifact(), destination, threading.Event(), lambda *args: None,
                 httpx.MockTransport(reply))

    assert destination.read_bytes() == OLD_CONTENTS
    assert part.read_bytes() == b"0123"


def test_ignored_range_can_reclaim_partial_space_at_exact_free_space_limit(tmp_path, monkeypatch):
    destination = tmp_path / "model.gguf"
    part = destination.with_suffix(".gguf.part")
    destination.write_bytes(OLD_CONTENTS)
    part.write_bytes(b"0123")
    monkeypatch.setattr("ctrlcctrlv.runtime.shutil.disk_usage", lambda _: disk_space(RESERVE + 6))

    def reply(request):
        assert request.headers["Range"] == "bytes=4-"
        return httpx.Response(200, content=CONTENTS)

    download(model_artifact(), destination, threading.Event(), lambda *args: None,
             httpx.MockTransport(reply))

    assert destination.read_bytes() == CONTENTS
    assert not part.exists()


@pytest.mark.parametrize("partial_bytes", [b"0123", b"oversized partial"])
def test_precancelled_low_space_download_preserves_all_files(tmp_path, monkeypatch, partial_bytes):
    destination = tmp_path / "model.gguf"
    part = destination.with_suffix(".gguf.part")
    destination.write_bytes(OLD_CONTENTS)
    part.write_bytes(partial_bytes)
    monkeypatch.setattr("ctrlcctrlv.runtime.shutil.disk_usage", lambda _: disk_space(0))
    cancel = threading.Event()
    cancel.set()
    transport = httpx.MockTransport(lambda _: pytest.fail("Cancelled download must not request data"))

    with pytest.raises(Cancelled):
        download(model_artifact(), destination, cancel, lambda *args: None, transport)

    assert destination.read_bytes() == OLD_CONTENTS
    assert part.read_bytes() == partial_bytes


@pytest.mark.parametrize("status", [200, 206])
def test_cancelled_resume_response_preserves_partial_before_opening_for_write(tmp_path, monkeypatch, status):
    destination = tmp_path / "model.gguf"
    part = destination.with_suffix(".gguf.part")
    destination.write_bytes(OLD_CONTENTS)
    part.write_bytes(b"0123")
    monkeypatch.setattr("ctrlcctrlv.runtime.shutil.disk_usage", lambda _: disk_space(RESERVE + 10))
    cancel = threading.Event()

    def reply(request):
        assert request.headers["Range"] == "bytes=4-"
        cancel.set()
        if status == 206:
            return httpx.Response(206, content=b"456789", headers={"Content-Range": "bytes 4-9/10"})
        return httpx.Response(200, content=CONTENTS)

    with pytest.raises(Cancelled):
        download(model_artifact(), destination, cancel, lambda *args: None, httpx.MockTransport(reply))

    assert destination.read_bytes() == OLD_CONTENTS
    assert part.read_bytes() == b"0123"


def test_zip_preflight_uses_uncompressed_size_and_preserves_existing_files(tmp_path, monkeypatch):
    archive = tmp_path / "engine.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("llama-server.exe", b"x" * 4096)
        package.writestr("engine.dll", b"y" * 2048)
    archive_bytes = archive.read_bytes()
    staging = tmp_path / "staging"
    staging.mkdir()
    old = staging / "llama-server.exe"
    old.write_bytes(OLD_CONTENTS)
    monkeypatch.setattr("ctrlcctrlv.runtime.shutil.disk_usage", lambda _: disk_space(RESERVE + 6143))

    with pytest.raises(OperationError, match="Недостаточно места"):
        extract_engine(archive, staging, threading.Event())

    assert archive.read_bytes() == archive_bytes
    assert old.read_bytes() == OLD_CONTENTS
    assert not (staging / "engine.dll").exists()


def test_zip_extracts_when_free_space_exactly_meets_limit(tmp_path, monkeypatch):
    archive = tmp_path / "engine.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("llama-server.exe", b"engine")
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr("ctrlcctrlv.runtime.shutil.disk_usage", lambda _: disk_space(RESERVE + 6))

    extract_engine(archive, staging, threading.Event())

    assert (staging / "llama-server.exe").read_bytes() == b"engine"


def test_precancelled_zip_extraction_preserves_files_despite_low_space(tmp_path, monkeypatch):
    archive = tmp_path / "engine.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("llama-server.exe", b"replacement")
    staging = tmp_path / "staging"
    staging.mkdir()
    old = staging / "llama-server.exe"
    old.write_bytes(OLD_CONTENTS)
    monkeypatch.setattr("ctrlcctrlv.runtime.shutil.disk_usage", lambda _: disk_space(0))
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(Cancelled):
        extract_engine(archive, staging, cancel)

    assert old.read_bytes() == OLD_CONTENTS
