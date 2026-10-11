from pathlib import Path
import subprocess
import sys
import threading

import pytest

from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.runtime import LocalRuntime


def fake_installed(monkeypatch):
    monkeypatch.setattr(LocalRuntime, "installed", lambda self, model: True)
    # Python cannot import the fake GGUF path passed as -m, so this portable
    # owned child exits nonzero without loading a model or user source code.
    monkeypatch.setattr(LocalRuntime, "binary", property(lambda self: Path(sys.executable)))


def test_actual_crashing_child_is_reaped_and_key_removed(tmp_path, monkeypatch):
    fake_installed(monkeypatch)
    runtime = LocalRuntime(tmp_path)
    with pytest.raises(OperationError, match="Движок завершился"):
        runtime.start("lightonocr-3-0.8b", threading.Event(), lambda *args: None)
    assert runtime.process is None
    assert not runtime.ready
    assert runtime.key is None
    assert not (tmp_path / "runtime/session-key").exists()


def test_spawn_failure_removes_key_and_restricts_its_permissions(tmp_path, monkeypatch):
    fake_installed(monkeypatch)
    key = tmp_path / "runtime/session-key"
    key.parent.mkdir()
    key.write_text("old")
    key.chmod(0o644)
    def failed_spawn(*args, **kwargs):
        assert key.stat().st_mode & 0o777 == 0o600
        raise OSError("Cannot spawn")
    monkeypatch.setattr(subprocess, "Popen", failed_spawn)
    runtime = LocalRuntime(tmp_path)
    with pytest.raises(OperationError, match="Не удалось запустить"):
        runtime.start("lightonocr-3-0.8b", threading.Event(), lambda *args: None)
    assert not key.exists()
    assert runtime.process is None


def test_pre_cancelled_start_preserves_existing_model(tmp_path):
    runtime = LocalRuntime(tmp_path)
    runtime.ready = True
    runtime.model_id = "qwen3-vl-4b"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        runtime.start("lightonocr-3-0.8b", cancel, lambda *args: None)
    assert runtime.ready
    assert runtime.model_id == "qwen3-vl-4b"


def test_cancel_between_key_creation_and_spawn_leaves_no_key(tmp_path, monkeypatch):
    fake_installed(monkeypatch)
    cancel = threading.Event()
    def key_created(*args):
        cancel.set()
        return "test-key"
    monkeypatch.setattr("ctrlcctrlv.runtime.secrets.token_urlsafe", key_created)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Cancelled preparation must not spawn"))
    runtime = LocalRuntime(tmp_path)
    with pytest.raises(Cancelled):
        runtime.start("lightonocr-3-0.8b", cancel, lambda *args: None)
    assert runtime.process is None
    assert not (tmp_path / "runtime/session-key").exists()


class OwnedProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = self.killed = self.waits = 0
        self.fail_all_waits = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1

    def wait(self, timeout):
        self.waits += 1
        if self.fail_all_waits or self.waits == 1:
            raise subprocess.TimeoutExpired("owned-child", timeout)
        self.returncode = -9 if self.killed else -15
        return self.returncode


def test_force_kill_and_retry_keep_process_ownership(tmp_path):
    runtime = LocalRuntime(tmp_path)
    key = tmp_path / "runtime/session-key"
    key.parent.mkdir()
    key.write_text("private")
    process = runtime.process = OwnedProcess()
    process.fail_all_waits = True
    runtime.ready = True
    with pytest.raises(OperationError, match="Повторите выгрузку"):
        runtime.stop()
    assert runtime.process is process
    assert not runtime.ready
    assert key.exists()
    process.fail_all_waits = False
    runtime.stop()
    assert process.terminated == 2
    assert process.killed == 1
    assert runtime.process is None
    assert not key.exists()
