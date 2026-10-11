import json
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.app import Controller
from ctrlcctrlv.diagnostics import collect_diagnostics, signature_info


def test_report_whitelists_fields_and_never_collects_secrets_or_paths(tmp_path, monkeypatch):
    runtime = SimpleNamespace(process=None, model_id="private screenshot text", ready=False,
        key="private-key", endpoint="private-endpoint", directory=tmp_path,
        installed=lambda identifier: identifier == "qwen3-vl-4b")
    monkeypatch.setattr("ctrlcctrlv.diagnostics.system_memory", lambda: 48 * 1024**3)
    report = collect_diagnostics(tmp_path, runtime, runtime,
        {"screen": True, "accessibility": False, "input_monitoring": "private", "extra": "private"},
        "local", "qwen3-vl-4b", False, "idle", threading.Event())
    parsed = json.loads(report)
    assert parsed["permissions"] == {"screen": True, "accessibility": False, "input_monitoring": None}
    assert parsed["ocr_runtime"]["model"] is None
    assert parsed["ocr_runtime"]["resident_memory_bytes"] is None
    assert parsed["installed_models"]["qwen3-vl-4b"]
    assert str(tmp_path) not in report
    assert "private" not in report
    assert "endpoint" not in report
    assert "key" not in parsed


def test_signature_report_identifies_build_without_paths_or_certificate_subject(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("ctrlcctrlv.diagnostics.platform.system", lambda: "Darwin")
    def command(args, **kwargs):
        assert args[:3] == ["codesign", "-d", "--verbose=4"]
        return SimpleNamespace(returncode=0, stdout="", stderr=
            "Executable=/private/person/app\nAuthority=private person\nCDHash=" + "a" * 40)
    monkeypatch.setattr("ctrlcctrlv.diagnostics.subprocess.run", command)
    result = signature_info()
    assert result == {"packaged": True, "signature": "certificate", "code_hash": "a" * 40}
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("cancelled", [False, True])
def test_gui_diagnostics_is_explicit_and_cancellable(tmp_path, monkeypatch, cancelled):
    app = QApplication.instance() or QApplication([])
    c = Controller(app, tmp_path, native=False)
    entered, release = threading.Event(), threading.Event()
    def report(*args):
        entered.set()
        release.wait(1)
        return '{"schema": 1}'
    monkeypatch.setattr("ctrlcctrlv.diagnostics.collect_diagnostics", report)
    app.clipboard().setText("user text")
    def wait(condition):
        deadline = time.monotonic() + 2
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
        app.processEvents()
        assert condition()
    try:
        c.window.diagnostics_button.click()
        wait(entered.is_set)
        assert c.busy()
        assert not c.window.capture_button.isEnabled()
        assert not c.window.diagnostics_button.isEnabled()
        assert app.clipboard().text() == "user text"
        if cancelled:
            c.cancel()
        release.set()
        wait(lambda: c.diagnostics_cancel is None and c.pool.activeThreadCount() == 0)
        assert not c.busy()
        assert c.diagnostics_window.isVisible() != cancelled
        assert app.clipboard().text() == "user text"
        if not cancelled:
            c.diagnostics_window.copy_button.click()
            assert app.clipboard().text() == '{"schema": 1}'
    finally:
        release.set()
        c.watchdog.stop()
        c.idle_timer.stop()
        c.permissions_timer.stop()
        c.preview.syntax_timer.stop()
        c.pool.waitForDone()
        c.tray.hide()
        c.diagnostics_window.close()
        c.window.deleteLater()
        c.preview.deleteLater()
        c.deleteLater()
