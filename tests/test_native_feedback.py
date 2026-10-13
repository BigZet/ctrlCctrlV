"""Native-found feedback regressions; offscreen, no hotkeys, network or inference."""
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.app import Controller
from ctrlcctrlv.syntax import SyntaxHint


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    c = Controller(app, tmp_path, native=False)
    yield c
    c.cancel()
    c.pool.waitForDone(5000)
    app.processEvents()
    for obj in (c, c.window, c.preview, c.indicator, c.diagnostics_window):
        for timer in obj.findChildren(QTimer):
            timer.stop()
    c.tray.hide()
    for obj in (c.window, c.preview, c.indicator, c.diagnostics_window):
        obj.hide()
        obj.deleteLater()
    c.deleteLater()


def prepare_callbacks(c, monkeypatch, identifier=None):
    jobs = []
    monkeypatch.setattr(c, 'run_job', lambda *args: jobs.append(args))
    c.model_job(start=True, identifier=identifier)
    return jobs[0]


def mark_ready(c, identifier=None, acceleration=None):
    class OwnedProcess:
        stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True

        def wait(self, timeout):
            return 0

    c.runtime.ready = True
    c.runtime.process = OwnedProcess()
    c.runtime.model_id = identifier or c.settings.local_model
    c.runtime.loaded_acceleration = acceleration or c.settings.acceleration


def test_successful_preparation_clears_only_recovered_capture_preflight(controller, monkeypatch):
    c = controller
    c.begin_capture()
    assert c.window.operation_error_details.toPlainText() == 'Подготовьте локальную модель'
    c.window.operation_error_toggle.setChecked(True)
    callbacks = prepare_callbacks(c, monkeypatch)
    mark_ready(c)
    callbacks[1]('Модель готова к OCR')
    assert c.window.operation_error.isHidden()
    assert c.window.operation_error_details.toPlainText() == ''
    assert not c.window.operation_error_toggle.isChecked()
    assert c.runtime.ready


@pytest.mark.parametrize('case', ['other-model', 'other-acceleration', 'failed', 'cancelled', 'unrelated-ocr'])
def test_preparation_preserves_unresolved_or_unrelated_capture_error(controller, monkeypatch, case):
    c = controller
    c.begin_capture()
    identifier = 'qwen3-vl-4b' if case == 'other-model' else None
    message = 'Подготовьте локальную модель'
    snapshot = None
    if case == 'unrelated-ocr':
        message = 'OCR timed out; retry the saved snapshot'
        c.window.show_operation_error(message)
        snapshot = c.retry_image = QImage(12, 12, QImage.Format.Format_RGB32)
        c.window.retry_button.show()
    callbacks = prepare_callbacks(c, monkeypatch, identifier)
    mark_ready(c, identifier, 'cpu' if case == 'other-acceleration' else None)
    if case == 'cancelled':
        c.model_cancel.set()
    callbacks[2]('preparation failed') if case == 'failed' else callbacks[1]('Модель готова к OCR')
    assert not c.window.operation_error.isHidden()
    assert c.window.operation_error_details.toPlainText() == message
    if snapshot is not None:
        assert c.retry_image is snapshot
        assert not c.window.retry_button.isHidden()


def test_review_failure_details_are_accessible_without_resizing_editor(controller, monkeypatch):
    c = controller
    c.preview.display(QImage(200, 100, QImage.Format.Format_RGB32), 'original', .1, 'model label')
    c.preview.resize(840, 620)
    c.app.processEvents()
    before = c.preview.text.geometry()
    c.review_runtime.installed = lambda identifier: True
    jobs = []
    monkeypatch.setattr(c, 'run_job', lambda *args: jobs.append(args))
    c.review_text()
    message = ('Недостаточно памяти GPU: доступно 1.0 ГБ, предварительная оценка для модели 4.9 ГБ. '
               'Выберите меньшую модель, CPU или OpenRouter.\nПолная подробность: Ж 😀')
    jobs[0][2](message)
    c.app.processEvents()
    assert c.preview.status.text() == 'Уточнение не завершено'
    assert c.preview.status.toolTip() == message
    assert c.preview.status.accessibleDescription() == message
    details = c.preview.review_error_button
    assert not details.isHidden()
    assert details.accessibleName() == 'Полные подробности ошибки уточнения'
    assert details.toolTip() == message
    details.click()
    c.app.processEvents()
    assert c.preview.review_error_dialog.isVisible()
    assert c.preview.review_error_text.isReadOnly()
    assert c.preview.review_error_text.toPlainText() == message
    assert c.preview.text.geometry() == before
    assert c.preview.text.toPlainText() == 'original'
    c.preview.review_error_dialog.close()
    c.review_text()
    assert details.isHidden()
    assert c.preview.review_error_text.toPlainText() == ''
    assert c.preview.status.toolTip() == 'model label'


def test_new_preview_clears_old_review_failure_details(controller):
    c = controller
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    c.preview.display(image, 'old', .1, 'old model')
    c.preview.show_review_error('long old error')
    c.preview.display(image, 'new', .2, 'new model')
    assert c.preview.review_error_button.isHidden()
    assert c.preview.review_error_text.toPlainText() == ''
    assert c.preview.status.toolTip() == 'new model'
    assert c.preview.status.accessibleDescription() != 'long old error'


def test_new_preview_does_not_restore_old_review_error_through_syntax_feedback(controller):
    c = controller
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    c.preview.display(image, '{bad', .1, 'old model')
    c.preview.show_review_error('old review failure')
    c.preview.show_syntax_hint(c.preview.syntax_revision, SyntaxHint('JSON', 1, 'invalid JSON'))
    c.preview.display(image, 'new', .2, 'new model')
    c.preview.show_syntax_hint(c.preview.syntax_revision, None)
    assert c.preview.status.property('role') == 'muted'
    assert c.preview.status.toolTip() == 'new model'
    assert c.preview.review_error_button.isHidden()
