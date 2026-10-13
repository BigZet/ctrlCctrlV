"""Offline integration checks for history, shortcuts and model-operation ownership."""
import csv
import io
from datetime import datetime
from pathlib import Path
import threading
import zipfile

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from ctrlcctrlv.app import Controller
from ctrlcctrlv.core import OperationError


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    controller = Controller(app, tmp_path, native=False)
    yield controller
    controller.cancel()
    controller.pool.waitForDone(5000)
    app.processEvents()
    controller.tray.hide()
    for timer in (controller.watchdog, controller.idle_timer, controller.permissions_timer,
                  controller.capture_timer, controller.typing_timer, controller.preview.syntax_timer):
        timer.stop()
    for widget in (controller.window, controller.preview, controller.indicator, controller.diagnostics_window):
        widget.close()
        widget.deleteLater()
    controller.deleteLater()


def test_history_editor_has_no_source_or_refinement_and_copy_preserves_original(controller):
    c = controller
    original = 'original transcription\n'
    identifier = c.history.add('ocr', original, 'old model')
    c.app.clipboard().setText('unrelated clipboard')
    c.open_history(identifier)
    assert c.preview.isVisible()
    assert c.preview.source_image is None and c.last_image is None
    assert not c.preview.image_panel.isVisible()
    assert not c.preview.review_button.isVisible()
    assert not c.preview.refine_model.isVisible()
    assert c.preview.copied_text is None
    assert c.app.clipboard().text() == 'unrelated clipboard'
    c.preview.replace_text('edited transcription\n')
    c.preview.copy_button.click()
    assert c.app.clipboard().text() == 'edited transcription\n'
    assert c.history.get(identifier).text == original
    assert [(entry.kind, entry.text) for entry in c.history.entries()] == [
        ('edit', 'edited transcription\n'), ('ocr', original)]
    assert not c.preview.isVisible()


@pytest.mark.parametrize('kind', ['txt', 'csv', 'xlsx'])
def test_history_save_uses_selected_format_and_records_successful_directory(controller, monkeypatch, tmp_path, kind):
    c = controller
    original = 'literal history text\n' if kind == 'txt' else 'Name,Qty\nPaper,001\n'
    identifier = c.history.add('ocr', original)
    folder = tmp_path / 'export'
    folder.mkdir()
    target = folder / ('saved.' + kind)
    c.app.clipboard().setText('clipboard stays')
    def accept(dialog):
        assert dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave
        assert datetime.now().strftime('%Y-%m-%d') in Path(dialog.selectedFiles()[0]).name
        if kind != 'txt':
            assert dialog.nameFilters() == ['CSV (*.csv)', 'XLSX (*.xlsx)']
            dialog.selectNameFilter(kind.upper() + ' (*.' + kind + ')')
            dialog.filterSelected.emit(dialog.selectedNameFilter())
            assert dialog.defaultSuffix() == kind
        dialog.selectFile(str(target))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QFileDialog, 'exec', accept)
    c.save_history(identifier)
    assert target.is_file()
    if kind == 'txt':
        assert target.read_bytes() == original.encode('utf-8')
    elif kind == 'csv':
        assert list(csv.reader(io.StringIO(target.read_text(encoding='utf-8-sig')))) == [['Name', 'Qty'], ['Paper', '001']]
    else:
        with zipfile.ZipFile(target) as archive:
            assert b'>001<' in archive.read('xl/sharedStrings.xml')
    assert c.ui_state.get('last_save_directory') == str(folder)
    assert c.history.get(identifier).text == original
    assert c.app.clipboard().text() == 'clipboard stays'


@pytest.mark.parametrize('table', [False, True])
def test_history_save_cancel_and_failure_keep_last_successful_directory(controller, monkeypatch, tmp_path, table):
    c = controller
    identifier = c.history.add('ocr', 'Name,Qty\nPaper,001\n' if table else 'plain text')
    c.ui_state.set(last_save_directory=str(tmp_path))
    monkeypatch.setattr(QFileDialog, 'exec', lambda _: QDialog.DialogCode.Rejected)
    c.save_history(identifier)
    assert c.ui_state.get('last_save_directory') == str(tmp_path)
    def fail_destination(dialog):
        dialog.selectFile(str(tmp_path / 'missing-directory' / ('saved.csv' if table else 'saved.txt')))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QFileDialog, 'exec', fail_destination)
    c.save_history(identifier)
    assert 'Не удалось сохранить' in c.window.status.text()
    assert c.ui_state.get('last_save_directory') == str(tmp_path)
    assert not (tmp_path / 'missing-directory').exists()


@pytest.mark.parametrize('field', ['capture_hotkey', 'typing_hotkey', 'cancel_hotkey'])
def test_recording_any_hotkey_blocks_global_capture_type_and_cancel(controller, monkeypatch, field):
    c = controller
    # External actions are the boundary: none may escape while recording a chord.
    actions = []
    monkeypatch.setattr(c, 'capture', lambda: actions.append('capture'))
    monkeypatch.setattr(c, 'type_clipboard', lambda: actions.append('type'))
    monkeypatch.setattr(c, 'cancel', lambda: actions.append('cancel'))
    edit = getattr(c.window, field)
    edit.start_recording()
    for action in ('capture', 'type', 'cancel'):
        c.handle_hotkey(action)
    assert actions == []
    edit.finish_recording()
    c.app.processEvents()
    for action in ('capture', 'type', 'cancel'):
        c.handle_hotkey(action)
    assert actions == ['capture', 'type', 'cancel']


def test_tray_stop_tracks_operation_immediately(controller):
    c = controller
    assert not c.tray_stop_action.isEnabled()
    c.model_cancel = threading.Event()
    c.status('Загрузка моделей…')
    assert c.tray_stop_action.isEnabled()
    c.model_cancel = None
    c.status('Загрузка завершена')
    assert not c.tray_stop_action.isEnabled()
    actions = c.tray_menu.actions()
    assert actions[-2].isSeparator()
    assert '\t' in c.tray_actions['capture'].text()


@pytest.fixture
def queued_controller(controller, monkeypatch):
    jobs = []
    monkeypatch.setattr(controller.runtime, 'installed', lambda _: False)
    monkeypatch.setattr(controller, 'run_job', lambda task, success=None, error=None, progress=None:
                        jobs.append((task, success, error, progress)))
    return controller, jobs


@pytest.mark.parametrize('finishes_first', [False, True])
def test_cancelling_first_download_prevents_second_install(queued_controller, monkeypatch, finishes_first):
    c, jobs = queued_controller
    identifiers = list(c.models)[:2]
    installed = []
    def install(identifier, cancel, progress):
        installed.append(identifier)
        progress('weights.gguf', 5, 10)
        cancel.set()
        if not finishes_first:
            raise OperationError('cancelled first download')
    monkeypatch.setattr(c.runtime, 'install', install)
    c.download_models(identifiers)
    task, _, failed, progress = jobs[0]
    with pytest.raises(OperationError, match='Загружено ' + ('1' if finishes_first else '0') + '/2') as error:
        task(progress)
    failed(str(error.value))
    assert installed == identifiers[:1]
    assert c.model_cancel is None
    for identifier in identifiers:
        assert c.window.model_status_widgets[identifier][0].text() == 'Не скачана'


def test_completed_queue_ignores_old_progress_and_completion_during_new_queue(queued_controller, monkeypatch):
    c, jobs = queued_controller
    identifier = next(iter(c.models))
    monkeypatch.setattr(c.runtime, 'install', lambda *args: None)
    c.download_models([identifier])
    task, complete_old, _, progress_old = jobs[0]
    complete_old(task(progress_old))
    c.download_models([identifier])
    token = c.model_cancel
    _, _, _, progress_new = jobs[1]
    progress_new((identifier, 'current weights.gguf'), 10, 100)
    label = c.window.model_status_widgets[identifier][0]
    assert label.text() == '10%'
    progress_old((identifier, 'stale weights.gguf'), 90, 100)
    progress_old((identifier, '__complete__'), 1, 1)
    complete_old('stale completion')
    assert label.text() == '10%'
    assert c.model_cancel is token
    assert c.window.status.text() != 'stale completion'
