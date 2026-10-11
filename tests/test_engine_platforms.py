import hashlib
import os
import threading
import zipfile

import pytest

from ctrlcctrlv.catalog import ENGINE, WINDOWS_ENGINE, WINDOWS_CUDART, Artifact, Model, engine_artifacts
from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.runtime import LocalRuntime, extract_engine


def test_catalog_selects_same_pinned_version_for_both_platforms():
    assert engine_artifacts("Darwin", "arm64") == (ENGINE,)
    assert engine_artifacts("Windows", "AMD64") == (WINDOWS_ENGINE, WINDOWS_CUDART)
    assert engine_artifacts("Windows", "x86_64") == (WINDOWS_ENGINE, WINDOWS_CUDART)
    with pytest.raises(ValueError):
        engine_artifacts("Linux", "x86_64")


def test_relative_runtime_directory_cannot_break_dll_search_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runtime = LocalRuntime(__import__("pathlib").Path("data"))
    assert runtime.directory == tmp_path / "data"
    assert runtime.engine_directory.is_absolute()


@pytest.mark.parametrize("name", ["../escape.dll", "/escape.dll", "C:/escape.dll", "a/../../escape.dll",
                                  "..\\escape.dll", "server.exe:payload", "a. /file.dll"])
def test_zip_rejects_paths_outside_staging(tmp_path, name):
    archive = tmp_path / "engine.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, b"test")
    staging = tmp_path / "stage"
    staging.mkdir()
    with pytest.raises(OperationError, match="путь"):
        extract_engine(archive, staging, threading.Event())
    assert not list(staging.rglob("*.dll"))


def test_zip_rejects_symlinks_and_cancelled_install(tmp_path):
    archive = tmp_path / "engine.zip"
    with zipfile.ZipFile(archive, "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "../outside")
    with pytest.raises(OperationError):
        extract_engine(archive, tmp_path, threading.Event())
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        extract_engine(archive, tmp_path, cancel)


def test_windows_install_merges_engine_and_cuda_before_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr("ctrlcctrlv.runtime.platform.system", lambda: "Windows")
    monkeypatch.setattr("ctrlcctrlv.runtime.platform.machine", lambda: "AMD64")
    engine = Artifact("engine.zip", "url", "hash", 1)
    cuda = Artifact("cuda.zip", "url", "hash", 1)
    weight = Artifact("weight.gguf", "url", hashlib.sha256(b"gguf").hexdigest(), 4)
    monkeypatch.setattr("ctrlcctrlv.runtime.engine_artifacts", lambda *args: (engine, cuda))
    monkeypatch.setitem(__import__("ctrlcctrlv.runtime", fromlist=["MODELS"]).MODELS, "fake",
                        Model("fake", "test", (weight,), "url"))
    def download(artifact, destination, *args):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if artifact.filename.endswith("zip"):
            with zipfile.ZipFile(destination, "w") as z:
                z.writestr("llama-server.exe" if artifact is engine else "cudart64_12.dll", b"binary")
        else:
            destination.write_bytes(b"gguf")
    monkeypatch.setattr("ctrlcctrlv.runtime.download", download)
    runtime = LocalRuntime(tmp_path)
    runtime.install("fake", threading.Event(), lambda *args: None)
    assert runtime.binary.name == "llama-server.exe"
    assert (runtime.binary.parent / "cudart64_12.dll").exists()
    assert runtime.installed("fake")
    (runtime.binary.parent / "cudart64_12.dll").unlink()
    assert runtime.binary is None
    runtime.install("fake", threading.Event(), lambda *args: None)
    assert runtime.binary is not None
    runtime.delete("fake")
    assert not runtime.installed("fake")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL")
def test_windows_key_is_owner_restricted_before_secret_is_written(tmp_path):
    import subprocess
    from ctrlcctrlv.security import protect_keyfile
    key = tmp_path / "session-key"
    with key.open("w") as stream:
        protect_keyfile(key, stream)
        stream.write("synthetic")
    # SID form is stable across Windows UI languages.
    result = subprocess.run(["icacls", str(key)], capture_output=True, text=True, check=True)
    assert "(I)" not in result.stdout
    assert key.read_text() == "synthetic"
