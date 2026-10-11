import hashlib
import threading

import httpx
import pytest

from ctrlcctrlv.catalog import Artifact
from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.runtime import LocalRuntime, download


def artifact(content, digest=None):
    return Artifact("model.gguf", "https://example.invalid/model", digest or hashlib.sha256(content).hexdigest(), len(content))


def test_resume_validates_range_and_hash(tmp_path):
    contents = b"0123456789"
    destination = tmp_path / "model.gguf"
    destination.with_suffix(".gguf.part").write_bytes(contents[:4])

    def request(req):
        assert req.headers["Range"] == "bytes=4-"
        return httpx.Response(206, content=contents[4:], headers={"Content-Range": "bytes 4-9/10"})

    download(artifact(contents), destination, threading.Event(), lambda *args: None, httpx.MockTransport(request))
    assert destination.read_bytes() == contents
    assert not destination.with_suffix(".gguf.part").exists()


def test_server_ignores_range_download_restarts(tmp_path):
    contents = b"0123456789"
    destination = tmp_path / "model.gguf"
    destination.with_suffix(".gguf.part").write_bytes(b"0123")
    download(artifact(contents), destination, threading.Event(), lambda *args: None,
        httpx.MockTransport(lambda req: httpx.Response(200, content=contents)))
    assert destination.read_bytes() == contents


def test_corrupt_file_never_becomes_installed(tmp_path):
    destination = tmp_path / "model.gguf"
    with pytest.raises(OperationError, match="Контрольная сумма"):
        download(artifact(b"correct"), destination, threading.Event(), lambda *args: None,
            httpx.MockTransport(lambda req: httpx.Response(200, content=b"corrupt")))
    assert not destination.exists()
    assert not destination.with_suffix(".gguf.part").exists()


def test_cancelled_download_is_not_published(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        download(artifact(b"data"), tmp_path / "model.gguf", cancel, lambda *args: None,
            httpx.MockTransport(lambda req: pytest.fail("must not contact server")))


def test_wrong_range_rejected(tmp_path):
    destination = tmp_path / "model.gguf"
    destination.with_suffix(".gguf.part").write_bytes(b"01")
    with pytest.raises(OperationError, match="диапазон"):
        download(artifact(b"012345"), destination, threading.Event(), lambda *args: None,
            httpx.MockTransport(lambda req: httpx.Response(206, content=b"2345", headers={"Content-Range": "bytes 0-3/6"})))
    assert not destination.exists()


class ImmediateRetry(threading.Event):
    def wait(self, timeout=None):
        return self.is_set()


def test_network_disconnect_retries_from_bytes_actually_written(tmp_path):
    contents = b"a" * (256 * 1024) + b"remaining"
    calls = []
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield contents[:256 * 1024]
            raise httpx.ReadError("network disconnected")
    def request(req):
        calls.append(req.headers.get("Range"))
        if len(calls) == 1:
            return httpx.Response(200, stream=Interrupted())
        return httpx.Response(206, content=contents[256 * 1024:],
            headers={"Content-Range": f"bytes {256 * 1024}-{len(contents)-1}/{len(contents)}"})
    destination = tmp_path / "model.gguf"
    download(artifact(contents), destination, ImmediateRetry(), lambda *args: None, httpx.MockTransport(request))
    assert calls == [None, f"bytes={256 * 1024}-"]
    assert destination.read_bytes() == contents


def test_temporary_http_failure_retries_but_permanent_error_does_not(tmp_path):
    calls = []
    def request(req):
        calls.append(req)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, content=b"data")
    destination = tmp_path / "model.gguf"
    download(artifact(b"data"), destination, ImmediateRetry(), lambda *args: None, httpx.MockTransport(request))
    assert len(calls) == 2
    calls.clear()
    def forbidden(req):
        calls.append(req)
        return httpx.Response(403)
    with pytest.raises(OperationError, match="403"):
        download(artifact(b"more"), destination, ImmediateRetry(), lambda *args: None, httpx.MockTransport(forbidden))
    assert len(calls) == 1


def test_retry_exhaustion_preserves_partial_file_and_cancel_during_backoff(tmp_path):
    destination = tmp_path / "model.gguf"
    part = destination.with_suffix(".gguf.part")
    part.write_bytes(b"da")
    calls = []
    def offline(req):
        calls.append(req)
        raise httpx.ConnectError("offline")
    with pytest.raises(OperationError, match="часть сохранена"):
        download(artifact(b"data"), destination, ImmediateRetry(), lambda *args: None, httpx.MockTransport(offline))
    assert len(calls) == 4
    assert part.read_bytes() == b"da"
    assert not destination.exists()
    cancel = ImmediateRetry()
    calls.clear()
    def progress(label, *_):
        if label.startswith("Повтор"):
            cancel.set()
    with pytest.raises(Cancelled):
        download(artifact(b"data"), destination, cancel, progress, httpx.MockTransport(offline))
    assert len(calls) == 1
    assert part.read_bytes() == b"da"


@pytest.mark.parametrize("content_range", ["bytes 2-3/7", "bytes 2-4/4", "bytes 2-3/*", "bytes 2-3/4 extra"])
def test_range_total_and_end_must_match_catalog(tmp_path, content_range):
    destination = tmp_path / "model.gguf"
    destination.with_suffix(".gguf.part").write_bytes(b"da")
    with pytest.raises(OperationError, match="диапазон"):
        download(artifact(b"data"), destination, ImmediateRetry(), lambda *args: None,
            httpx.MockTransport(lambda req: httpx.Response(206, content=b"ta", headers={"Content-Range": content_range})))


@pytest.mark.parametrize("slots", [0, 9, True, 1.5, "4"])
def test_invalid_slot_count_cannot_start_unbounded_runtime(tmp_path, slots):
    with pytest.raises(ValueError, match="slots"):
        LocalRuntime(tmp_path, slots=slots)
