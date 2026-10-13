"""Controller checks with an in-memory shortcut registrar; no native hooks."""
import threading

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from ctrlcctrlv.app import Controller
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.ui_state import UiState


class ShortcutPlatform:
    def __init__(self):
        self.registrations = []
        self.active_bindings = {}
        self.callback = None
        self.stop_count = 0

    def permissions(self):
        return {"screen": True, "accessibility": True, "input_monitoring": True}

    def start_hotkeys(self, callback, bindings):
        self.callback = callback
        self.active_bindings = dict(bindings)
        self.registrations.append((callback, dict(bindings)))
        return True

    def stop_hotkeys(self):
        self.stop_count += 1
        self.active_bindings = {}
        self.callback = None

    def press_capture_chord(self, widget):
        # Registered global chords are intercepted before they reach the editor.
        if self.active_bindings.get("capture") == "ctrl+alt+4":
            self.callback("capture")
        else:
            QTest.keyClick(widget, Qt.Key.Key_4,
                           Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    Settings(capture_hotkey="ctrl+alt+4", typing_hotkey="ctrl+alt+v",
             cancel_hotkey="ctrl+alt+esc").save(tmp_path)
    adapter = ShortcutPlatform()
    c = Controller(app, tmp_path, platform_adapter=adapter, native=False)
    yield c
    c.shutting_down = True
    c.pool.waitForDone(5000)
    c.app.processEvents()
    adapter.stop_hotkeys()
    c.tray.hide()
    for timer in (c.watchdog, c.idle_timer, c.permissions_timer, c.capture_timer,
                  c.typing_timer, c.preview.syntax_timer):
        timer.stop()
    for widget in (c.window, c.preview, c.indicator, c.diagnostics_window):
        widget.close()
        widget.deleteLater()
    c.deleteLater()


def action_log(controller, monkeypatch):
    actions = []
    monkeypatch.setattr(controller, "capture", lambda: actions.append("capture"))
    monkeypatch.setattr(controller, "type_clipboard", lambda: actions.append("type"))
    monkeypatch.setattr(controller, "cancel", lambda: actions.append("cancel"))
    return actions


def test_existing_registered_chord_reaches_recorder_and_restores_after_event(controller, monkeypatch):
    c = controller
    adapter = c.platform
    actions = action_log(c, monkeypatch)
    field = c.window.capture_hotkey
    field.start_recording()
    assert not adapter.active_bindings
    assert adapter.stop_count == 1
    assert not c.hotkeys_registered
    adapter.press_capture_chord(field)
    assert not field.recording
    assert field.text() == "ctrl+alt+4"
    assert actions == []
    assert not adapter.active_bindings  # Resume is deferred until the chord has finished.
    c.app.processEvents()
    assert c.hotkeys_registered
    assert adapter.active_bindings["capture"] == "ctrl+alt+4"
    adapter.press_capture_chord(field)
    assert actions == ["capture"]


def test_stale_platform_callbacks_remain_ignored_after_registration_resumes(controller, monkeypatch):
    c = controller
    actions = action_log(c, monkeypatch)
    old_callback = c.platform.callback
    field = c.window.capture_hotkey
    field.start_recording()
    field.finish_recording()
    c.app.processEvents()
    new_callback = c.platform.callback
    # Native callbacks can already be queued on another thread when unregistered.
    worker = threading.Thread(target=lambda: [old_callback(action) for action in ("capture", "type", "cancel")])
    worker.start()
    worker.join()
    c.app.processEvents()
    assert actions == []
    for action in ("capture", "type", "cancel"):
        new_callback(action)
    assert actions == ["capture", "type", "cancel"]


@pytest.mark.parametrize("end", ["escape", "focus_out"])
def test_escape_and_focus_loss_restore_saved_bindings_without_edit(controller, end):
    c = controller
    field = c.window.typing_hotkey
    old_text = field.text()
    before = dict(c.platform.active_bindings)
    field.start_recording()
    if end == "escape":
        QTest.keyClick(field, Qt.Key.Key_Escape)
    else:
        c.app.sendEvent(field, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))
    assert not field.recording
    c.app.processEvents()
    assert field.text() == old_text
    assert c.platform.active_bindings == before
    assert c.hotkeys_registered


def test_resume_waits_until_every_recorder_has_finished(controller):
    c = controller
    first, second = c.window.capture_hotkey, c.window.typing_hotkey
    first.start_recording()
    second.start_recording()
    first.finish_recording()
    c.app.processEvents()
    assert second.recording
    assert not c.platform.active_bindings
    second.finish_recording()
    c.app.processEvents()
    assert c.hotkeys_registered
    assert len(c.platform.registrations) == 2


def test_new_chord_is_registered_only_after_settings_are_saved(controller):
    c = controller
    field = c.window.capture_hotkey
    field.start_recording()
    QTest.keyClick(field, Qt.Key.Key_9,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    c.app.processEvents()
    assert field.text() == "ctrl+alt+9"
    assert c.platform.active_bindings["capture"] == "ctrl+alt+4"
    assert Settings.load(c.directory).capture_hotkey == "ctrl+alt+4"
    assert c.save(announce=False)
    assert c.platform.active_bindings["capture"] == "ctrl+alt+9"
    assert Settings.load(c.directory).capture_hotkey == "ctrl+alt+9"


def test_shutdown_does_not_resume_deferred_registration(controller):
    c = controller
    c.window.capture_hotkey.start_recording()
    c.window.capture_hotkey.finish_recording()
    c.shutting_down = True
    c.app.processEvents()
    assert not c.platform.active_bindings
    assert len(c.platform.registrations) == 1


def test_history_xlsx_create_error_preserves_last_successful_directory(controller, tmp_path, monkeypatch):
    c = controller
    identifier = c.history.add("ocr", "Name,Qty\nPaper,001\n")
    c.ui_state.set(last_save_directory=str(tmp_path))
    destination = tmp_path / "missing-directory" / "saved.xlsx"

    def choose_xlsx(dialog):
        dialog.selectNameFilter("XLSX (*.xlsx)")
        dialog.filterSelected.emit(dialog.selectedNameFilter())
        dialog.selectFile(str(destination))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QFileDialog, "exec", choose_xlsx)
    c.save_history(identifier)
    assert "Не удалось сохранить" in c.window.status.text()
    assert c.ui_state.get("last_save_directory") == str(tmp_path)
    assert UiState(c.directory).get("last_save_directory") == str(tmp_path)
    assert not destination.exists()
    assert c.history.get(identifier).text == "Name,Qty\nPaper,001\n"
