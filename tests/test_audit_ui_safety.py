"""Offline regression checks for draft loss, model selection and history errors."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from ctrlcctrlv.app import Controller
from ctrlcctrlv.model_picker import ModelChooser
from ctrlcctrlv.ui import Preview


@pytest.fixture
def controller(tmp_path, monkeypatch):
    app = QApplication.instance()
    instance = Controller(app, tmp_path, native=False)
    # Keep real controller transitions, but never dispatch inference or runtime work.
    monkeypatch.setattr(instance, "provider", lambda: SimpleNamespace(model="test"))
    monkeypatch.setattr(instance, "run_job", lambda *args, **kwargs: None)
    yield instance
    for timer in instance.findChildren(QTimer):
        timer.stop()
    instance.cancel()
    instance.pool.waitForDone(5000)
    instance.tray.hide()
    for widget in (instance.window, instance.preview, instance.indicator, instance.diagnostics_window):
        widget.close()
        widget.deleteLater()
    instance.deleteLater()


def prepare_draft(controller, table=False):
    original = "Name,Qty\nPaper,1\n" if table else "original transcription\n"
    image = QImage(20, 20, QImage.Format.Format_RGB32)
    controller.preview.display(image, original, .1, "test")
    controller.retry_image = image.copy()
    controller.app.clipboard().setText("existing clipboard")
    if table:
        controller.preview.table.editItem(controller.preview.table.item(1, 1))
        controller.preview.table.findChild(QLineEdit).setText("00123")
        return "Name,Qty\nPaper,00123\n"
    controller.preview.replace_text("unsaved draft\n")
    return "unsaved draft\n"


@pytest.mark.parametrize("action", ["capture", "retry_failed_ocr", "quit"])
@pytest.mark.parametrize("table", [False, True])
def test_cancel_preserves_draft_and_operation_before_capture_retry_or_quit(controller, monkeypatch, action, table):
    draft = prepare_draft(controller, table)
    retry_image = controller.retry_image
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    getattr(controller, action)()
    assert not controller.capture_pending
    assert not controller.capture_timer.isActive()
    assert controller.operation is None
    assert controller.retry_image is retry_image
    assert not controller.shutting_down
    assert controller.preview.isVisible()
    assert controller.preview.text.toPlainText() == draft
    assert controller.app.clipboard().text() == "existing clipboard"


@pytest.mark.parametrize("action", ["capture", "retry_failed_ocr", "quit"])
def test_cancelled_file_save_does_not_start_capture_retry_or_shutdown(controller, monkeypatch, action):
    prepare_draft(controller)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(controller.preview, "choose_text_path", lambda _: None)
    getattr(controller, action)()
    assert not controller.capture_pending
    assert controller.operation is None
    assert not controller.shutting_down
    assert controller.preview.text.toPlainText() == "unsaved draft\n"


@pytest.mark.parametrize("action", ["capture", "retry_failed_ocr", "quit"])
def test_discard_allows_capture_retry_or_shutdown(controller, monkeypatch, action):
    prepare_draft(controller)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    getattr(controller, action)()
    assert (controller.capture_pending if action == "capture" else
            controller.operation is not None if action == "retry_failed_ocr" else controller.shutting_down)


def test_forced_diagnostic_shutdown_bypasses_synthetic_draft(controller, monkeypatch):
    prepare_draft(controller)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("Diagnostic shutdown must not prompt"))
    controller.quit(force=True)
    assert controller.shutting_down


def test_tray_quit_never_treats_triggered_boolean_as_force(controller, monkeypatch):
    prepare_draft(controller)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    action = controller.tray_actions["quit"]
    action.setCheckable(True)
    action.trigger()
    assert not controller.shutting_down


@pytest.fixture
def picker_window():
    window = Preview()
    window.set_refine_models([
        ("local", "installed", "Installed model"),
        ("local", "missing", "A long model name describing its special-purpose OCR and image understanding capabilities with a particular architecture and model revision from its author"),
        ("openrouter", "example/vision", "A very long model name with a revision for image understanding", "$0.15 / $0.60"),
    ])
    window.refine_model.set_installed({"installed"})
    window.show()
    yield window
    for chooser in window.findChildren(ModelChooser):
        chooser.close()
    window.syntax_timer.stop()
    window.close()
    window.deleteLater()


@pytest.mark.parametrize("missing", [False, True])
def test_minimum_model_chooser_shows_two_complete_rows_and_actions(picker_window, missing):
    picker_window.refine_model._favorites = {"local:missing"}
    chooser = picker_window.refine_model.create_chooser()
    chooser.resize(640, 420)
    chooser.show()
    chooser.table.selectRow(1 if missing else 0)
    QApplication.instance().processEvents()
    assert chooser.width() == 640 and chooser.height() == 420
    assert chooser.table.viewport().height() >= chooser.table.rowHeight(0) + chooser.table.rowHeight(1)
    header = chooser.table.horizontalHeader()
    assert header.height() >= header.sizeHint().height()
    for control in (chooser.choose, chooser.favorite, chooser.install):
        if control.isVisible():
            assert chooser.rect().contains(control.geometry())
            assert control.width() >= control.sizeHint().width()


@pytest.mark.parametrize("key", [Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_Home,
                                 Qt.Key.Key_End, Qt.Key.Key_PageDown, Qt.Key.Key_M])
def test_keyboard_opens_picker_without_selecting_uninstalled_model(picker_window, key):
    picker = picker_window.refine_model
    picker.setFocus()
    QApplication.instance().processEvents()
    QTest.keyClick(picker, key)
    QApplication.instance().processEvents()
    assert picker.currentData() == ("local", "installed")
    chooser = next((dialog for dialog in picker_window.findChildren(ModelChooser) if dialog.isVisible()), None)
    assert chooser is not None
    if key == Qt.Key.Key_M:
        assert chooser.search.text().lower() == "m"


@pytest.mark.parametrize("operation", ["write", "read"])
def test_history_failure_is_visible_then_clears_after_success(controller, monkeypatch, operation):
    controller.window.navigation.setCurrentRow(3)
    controller.window.show()
    method = "add" if operation == "write" else "entries"
    original = getattr(controller.history, method)
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(controller.history, method, fail)
    if operation == "write":
        controller.record_history("ocr", "recognized text")
    else:
        controller.refresh_history()
    status = controller.window.history_page.history_status
    assert status.isVisible()
    assert "Не удалось" in status.text()
    monkeypatch.setattr(controller.history, method, original)
    controller.record_history("ocr", "saved after recovery")
    assert "Не удалось" not in status.text()


def test_missing_dispatch_target_cannot_type_into_a_later_window(controller, monkeypatch):
    events, queued = [], []
    platform = SimpleNamespace(
        target=None,
        active_pid=lambda: 123456,
        permissions=lambda: {"accessibility": True},
        modifiers_pressed=lambda: False,
        character=events.append,
    )
    platform.active_target = lambda: platform.target
    controller.platform = platform
    controller.settings.character_delay = .001
    monkeypatch.setattr(controller, "run_job", lambda task, success, *args: queued.append((task, success)))
    controller.start_typing("abc")
    platform.target = (123456, "a later window")
    for task, complete in queued:
        complete(task(lambda *args: None))
    assert events == []
    assert controller.typing_cancel is None
    assert not controller.tray_stop_action.isEnabled()
    assert "окно" in controller.window.status.text()

    # A subsequent explicit command may target the newly identified window.
    queued.clear()
    controller.start_typing("abc")
    for task, complete in queued:
        complete(task(lambda *args: None))
    assert events == ["a", "b", "c"]
