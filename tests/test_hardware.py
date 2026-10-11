import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.catalog import MODELS, WINDOWS_CPU_ENGINE, engine_artifacts
from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.hardware import GIB, estimate_memory, inspect_hardware, require_capacity, memory_capacity
from ctrlcctrlv.runtime import LocalRuntime
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.ui import SettingsWindow


@pytest.fixture(autouse=True)
def isolate_driver_memory(monkeypatch):
    monkeypatch.setattr("ctrlcctrlv.windows.nvidia_memory_capacity", lambda: [])


def test_cpu_selection_uses_cpu_package_and_preserves_model_settings(tmp_path, monkeypatch):
    monkeypatch.setattr("ctrlcctrlv.runtime.platform.system", lambda: "Windows")
    assert engine_artifacts("Windows", "AMD64", "cpu") == (WINDOWS_CPU_ENGINE,)
    settings = Settings(acceleration="cpu")
    settings.save(tmp_path)
    assert Settings.load(tmp_path) == settings
    runtime = LocalRuntime(tmp_path, acceleration="cpu")
    assert runtime.engine_directory.name.endswith("-cpu")


@pytest.mark.parametrize("value", [None, True, 3, "auto", "cuda"])
def test_invalid_acceleration_cannot_enable_an_implicit_fallback(value):
    with pytest.raises(ValueError):
        Settings(acceleration=value).validate()
    with pytest.raises(ValueError):
        LocalRuntime(Path("data"), acceleration=value)


def test_cpu_probe_does_not_execute_gpu_discovery(monkeypatch):
    monkeypatch.setattr("ctrlcctrlv.hardware.memory_capacity", lambda: (64 * GIB, 32 * GIB))
    monkeypatch.setattr("ctrlcctrlv.hardware.subprocess.run", lambda *a, **k: pytest.fail("CPU must not require a GPU"))
    report = inspect_hardware(None, "cpu", threading.Event())
    assert report["devices"] == []
    assert require_capacity(report, MODELS["lightonocr-3-0.8b"]) > GIB


def test_gpu_probe_reads_free_capacity_from_pinned_engine(monkeypatch):
    monkeypatch.setattr("ctrlcctrlv.hardware.memory_capacity", lambda: (64 * GIB, 32 * GIB))
    def run(command, **kwargs):
        assert command == [str(Path("llama-server.exe")), "--list-devices"]
        return SimpleNamespace(returncode=0, stdout="Available devices:\n  CUDA0: NVIDIA GeForce RTX 3080 (10239 MiB, 9071 MiB free)\n", stderr="")
    monkeypatch.setattr("ctrlcctrlv.hardware.subprocess.run", run)
    report = inspect_hardware(Path("llama-server.exe"), "gpu", threading.Event())
    assert report["devices"][0]["available_bytes"] == 9071 * 1024 ** 2
    require_capacity(report, MODELS["lightonocr-3-0.8b"])


@pytest.mark.parametrize("output,code", [("Available devices:\n", 0), ("driver unavailable", 1)])
def test_missing_driver_offers_cpu_without_changing_requested_mode(monkeypatch, output, code):
    monkeypatch.setattr("ctrlcctrlv.hardware.memory_capacity", lambda: (64 * GIB, 32 * GIB))
    monkeypatch.setattr("ctrlcctrlv.hardware.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=code, stdout=output, stderr=""))
    with pytest.raises(OperationError, match="CPU"):
        inspect_hardware(Path("llama-server.exe"), "gpu", threading.Event())


@pytest.mark.parametrize("acceleration", ["gpu", "cpu"])
def test_insufficient_memory_is_actionable_before_loading(acceleration):
    report = {"acceleration": acceleration, "physical_memory_bytes": 64 * GIB,
              "available_memory_bytes": GIB, "devices": [{"available_bytes": GIB}]}
    with pytest.raises(OperationError, match="меньшую модель"):
        require_capacity(report, MODELS["qwen3-vl-8b"])


def test_cancelled_discovery_does_not_contact_engine(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr("ctrlcctrlv.hardware.subprocess.run", lambda *a, **k: pytest.fail("cancelled"))
    with pytest.raises(Cancelled):
        inspect_hardware(Path("llama-server.exe"), "gpu", cancel)


def test_cpu_widget_saves_explicit_acceleration_and_keeps_cloud_choice():
    app = QApplication.instance() or QApplication([])
    settings = Settings()
    window = SettingsWindow(settings)
    try:
        window.acceleration.setCurrentIndex(window.acceleration.findData("cpu"))
        window.provider.setCurrentIndex(window.provider.findData("openrouter"))
        window.apply_to(settings)
        assert settings.acceleration == "cpu"
        assert settings.provider == "openrouter"
    finally:
        window.close()
        window.deleteLater()


def test_mac_capacity_uses_native_page_size_and_reclaimable_pages(monkeypatch):
    monkeypatch.setattr("ctrlcctrlv.hardware.sys.platform", "darwin")
    monkeypatch.setattr("ctrlcctrlv.diagnostics.system_memory", lambda: 48 * GIB)
    output = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 10000.\nPages inactive: 20000.\nPages speculative: 3000.\nPages wired down: 50000.\n"
    def run(command, **kwargs):
        assert command == ["/usr/bin/vm_stat"]
        return SimpleNamespace(stdout=output)
    monkeypatch.setattr("ctrlcctrlv.hardware.subprocess.run", run)
    assert memory_capacity() == (48 * GIB, 33000 * 16384)


def test_unknown_mac_available_memory_is_reported_as_unknown(monkeypatch):
    monkeypatch.setattr("ctrlcctrlv.hardware.sys.platform", "darwin")
    monkeypatch.setattr("ctrlcctrlv.diagnostics.system_memory", lambda: 48 * GIB)
    monkeypatch.setattr("ctrlcctrlv.hardware.subprocess.run", lambda *a, **k: SimpleNamespace(stdout="unrecognized format"))
    assert memory_capacity() == (48 * GIB, None)


def test_windows_gpu_uses_driver_wide_free_memory_instead_of_wddm_budget(monkeypatch):
    monkeypatch.setattr("ctrlcctrlv.hardware.sys.platform", "win32")
    monkeypatch.setattr("ctrlcctrlv.hardware.memory_capacity", lambda: (64 * GIB, 32 * GIB))
    monkeypatch.setattr("ctrlcctrlv.hardware.subprocess.run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="CUDA0: NVIDIA GeForce RTX 3080 (10239 MiB, 9071 MiB free)", stderr=""))
    monkeypatch.setattr("ctrlcctrlv.windows.nvidia_memory_capacity", lambda: [
        {"name": "NVIDIA GeForce RTX 3080", "available_bytes": 2 * GIB, "total_bytes": 10 * GIB}])
    report = inspect_hardware(Path("llama-server.exe"), "gpu", threading.Event())
    assert report["devices"][0]["available_bytes"] == 2 * GIB
    assert report["devices"][0]["memory_source"] == "CUDA / NVML"
    with pytest.raises(OperationError, match="Недостаточно"):
        require_capacity(report, MODELS["qwen3-vl-4b"])


def test_identical_cards_do_not_overestimate_memory_by_ordinal(monkeypatch):
    monkeypatch.setattr("ctrlcctrlv.hardware.sys.platform", "win32")
    monkeypatch.setattr("ctrlcctrlv.hardware.memory_capacity", lambda: (64 * GIB, 32 * GIB))
    monkeypatch.setattr("ctrlcctrlv.hardware.subprocess.run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="CUDA0: GPU (10240 MiB, 9000 MiB free)", stderr=""))
    monkeypatch.setattr("ctrlcctrlv.windows.nvidia_memory_capacity", lambda: [
        {"name": "GPU", "available_bytes": 8 * GIB}, {"name": "GPU", "available_bytes": GIB}])
    report = inspect_hardware(Path("llama-server.exe"), "gpu", threading.Event())
    assert report["devices"][0]["available_bytes"] == GIB
