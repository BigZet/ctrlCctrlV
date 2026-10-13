"""Integration contracts for new UI actions without network or native clipboard access."""
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QObject, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.app import Controller
from ctrlcctrlv.core import Operation
from ctrlcctrlv.history import EntryRef
from ctrlcctrlv.ocr import OCRResult
from ctrlcctrlv.capture_region import CaptureRegion, describe_screen


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


def finish(c):
    until = time.monotonic() + 3
    while (c.operation is not None or c.pool.activeThreadCount()) and time.monotonic() < until:
        c.app.processEvents()
        time.sleep(.002)
    c.app.processEvents()
    assert c.operation is None and c.pool.activeThreadCount() == 0


@pytest.mark.parametrize('show_preview', [True, False])
def test_primary_ocr_keeps_raw_only_for_preview_and_does_not_invoke_review(controller, monkeypatch, show_preview):
    c = controller
    c.settings.show_preview = show_preview
    calls, shown = [], []
    raw = 'python\nx = 1\n'
    def recognize_result(*args, **kwargs):
        calls.append('primary')
        return OCRResult('x = 1\n', raw)
    provider = SimpleNamespace(model='test', local=True, recognize_result=recognize_result,
                               recognize=lambda *a, **kw: pytest.fail('Detailed API should be used once'))
    c.provider = lambda: provider
    monkeypatch.setattr(c.preview, 'set_raw_response', shown.append, raising=False)
    monkeypatch.setattr(c.review_runtime, 'start', lambda *a, **kw: pytest.fail('No automatic reviewer'))
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    c.recognize(image, Operation())
    finish(c)
    assert calls == ['primary']
    assert c.app.clipboard().text() == 'x = 1\n'
    assert shown[-1] == (raw if show_preview else None)
    assert [entry.text for entry in c.history.entries()] == ['x = 1\n']
    assert raw.encode() not in c.history.path.read_bytes()
    assert raw not in str(c.ui_state.values)


def test_late_primary_result_does_not_replace_raw_or_clipboard(controller, monkeypatch):
    c = controller
    shown = []
    monkeypatch.setattr(c.preview, 'set_raw_response', shown.append, raising=False)
    c.app.clipboard().setText('unchanged')
    old = Operation()
    old.cancel()
    c.operation = Operation()
    c.ocr_complete(old, QImage(), SimpleNamespace(model='test'), OCRResult('new', 'raw'))
    assert c.app.clipboard().text() == 'unchanged'
    assert shown == []


def select_history(c, identifier):
    c.refresh_history()
    page = c.window.history_page
    for row in range(page.entries.count()):
        item = page.entries.item(row)
        data = item.data(256)
        if getattr(data, 'id', None) == identifier:
            page.entries.setCurrentRow(row)
            return
    # The controller API accepts an id without requiring the UI list to be visible.


def test_controller_history_undo_restores_pin_without_overwriting_reused_id(controller):
    c = controller
    identifier = c.history.add('ocr', 'original')
    entry = c.history.get(identifier)
    assert hasattr(c, 'pin_history')
    select_history(c, identifier)
    c.pin_history(identifier, True)
    assert c.ui_state.get('history_pins') == [{'id': identifier, 'created': entry.created}]
    c.delete_history(identifier)
    replacement_id = c.history.add('ocr', 'replacement')
    assert replacement_id == identifier
    c.undo_history_delete()
    restored = [e for e in c.history.entries() if e.text == 'original']
    assert len(restored) == 1 and restored[0].id != identifier
    assert c.history.get(replacement_id).text == 'replacement'
    assert c.ui_state.get('history_pins') == [{'id': restored[0].id, 'created': entry.created}]
    assert c.history_undo.peek() is None


def test_controller_failed_history_restore_keeps_undo_available(controller, monkeypatch):
    c = controller
    identifier = c.history.add('ocr', 'original')
    assert hasattr(c, 'history_undo')
    c.delete_history(identifier)
    restore = c.history.restore
    monkeypatch.setattr(c.history, 'restore', lambda e: (_ for _ in ()).throw(OSError('disk full')))
    c.undo_history_delete()
    assert c.history_undo.peek()[0].text == 'original'
    monkeypatch.setattr(c.history, 'restore', restore)
    c.undo_history_delete()
    assert c.history_undo.peek() is None
    assert c.history.get(identifier).text == 'original'


def test_download_error_is_local_and_retry_is_explicit(controller, monkeypatch):
    c = controller
    queued, installs = [], []
    identifier = next(iter(c.models))
    monkeypatch.setattr(c, 'run_job', lambda task, success=None, error=None, progress=None: queued.append((task, success, error)))
    monkeypatch.setattr(c.runtime, 'installed', lambda _: False)
    monkeypatch.setattr(c.runtime, 'install', lambda *a: installs.append(a))
    c.download_models([identifier])
    assert len(queued) == 1
    queued[0][2]('Network failed')
    assert c.window.model_retry_button.isVisibleTo(c.window)
    assert 'Network failed' in c.window.model_error.text()
    assert installs == [] and len(queued) == 1
    c.window.model_retry_button.click()
    assert len(queued) == 2


class TestScreen(QObject):
    __test__ = False

    def __init__(self):
        super().__init__()
        self.ratio = 1.5
        self.color = QColor('#123456')
        self.grabs = 0

    def geometry(self): return QRect(-200, 0, 200, 160)
    def devicePixelRatio(self): return self.ratio
    def name(self): return 'Duplicate display name'
    def manufacturer(self): return ''
    def model(self): return ''
    def serialNumber(self): return ''

    def grabWindow(self, _):
        self.grabs += 1
        pixmap = QPixmap(round(200 * self.ratio), round(160 * self.ratio))
        pixmap.fill(self.color)
        pixmap.setDevicePixelRatio(self.ratio)
        return pixmap


def test_repeat_region_takes_fresh_pixels_instead_of_retry_image(controller, monkeypatch):
    c = controller
    screen = TestScreen()
    descriptor = describe_screen(screen)
    c.last_capture_region = CaptureRegion(descriptor, (20, 30, 40, 50), (descriptor,))
    c.capture_pending = True
    c.repeat_pending = True
    captured = []
    monkeypatch.setattr(c.app, 'screens', lambda: [screen])
    monkeypatch.setattr(c, 'recognize', lambda image, operation: captured.append(image))
    assert hasattr(c, 'repeat_capture')
    c.show_selection()
    assert screen.grabs == 1 and len(captured) == 1
    assert captured[0].size().width() == 60 and captured[0].height() == 75
    assert captured[0].pixelColor(0, 0) == screen.color
    assert captured[0].devicePixelRatio() == 1


def test_repeat_region_topology_change_opens_new_selection_without_ocr(controller, monkeypatch):
    c = controller
    screen = TestScreen()
    descriptor = describe_screen(screen)
    c.last_capture_region = CaptureRegion(descriptor, (20, 30, 40, 50), (descriptor,))
    screen.ratio = 2
    c.capture_pending = True
    c.repeat_pending = True
    captured = []
    monkeypatch.setattr(c.app, 'screens', lambda: [screen])
    monkeypatch.setattr(c, 'recognize', lambda *args: captured.append(args))
    assert hasattr(c, 'repeat_capture')
    c.show_selection()
    assert captured == []
    assert len(c.overlays) == 1
    assert c.last_capture_region is None


def test_repeat_capture_obeys_draft_guard_before_hiding_or_starting_timer(controller, monkeypatch):
    c = controller
    screen = TestScreen()
    descriptor = describe_screen(screen)
    c.last_capture_region = CaptureRegion(descriptor, (20, 30, 40, 50), (descriptor,))
    monkeypatch.setattr(c.preview, 'confirm_replace_result', lambda: False)
    c.window.show()
    assert hasattr(c, 'repeat_capture')
    c.repeat_capture()
    assert c.window.isVisible()
    assert not c.capture_timer.isActive()
    assert not c.capture_pending


def test_cancel_pending_repeat_preserves_region_and_clears_pending_flag(controller):
    c = controller
    screen = TestScreen()
    descriptor = describe_screen(screen)
    region = CaptureRegion(descriptor, (20, 30, 40, 50), (descriptor,))
    c.last_capture_region = region
    c.capture_pending = c.repeat_pending = True
    c.capture_timer.start()
    c.cancel()
    assert not c.capture_pending and not c.repeat_pending
    assert not c.capture_timer.isActive()
    assert c.last_capture_region == region


@pytest.mark.parametrize('accept', [True, False])
def test_repeat_adjustment_waits_for_enter_or_preserves_region_on_escape(controller, monkeypatch, accept):
    c = controller
    screen = TestScreen()
    descriptor = describe_screen(screen)
    region = CaptureRegion(descriptor, (20, 30, 40, 50), (descriptor,))
    c.last_capture_region = region
    c.ui_state.set(capture_adjust=True)
    c.capture_pending = c.repeat_pending = True
    captured = []
    monkeypatch.setattr(c.app, 'screens', lambda: [screen])
    monkeypatch.setattr(c, 'recognize', lambda image, operation: captured.append(image))
    c.show_selection()
    assert not captured and len(c.overlays) == 1
    overlay = c.overlays[0]
    assert overlay.selection() == QRect(*region.rect)
    QTest.keyClick(overlay, Qt.Key.Key_Return if accept else Qt.Key.Key_Escape)
    assert len(captured) == int(accept)
    assert not c.overlays and c.last_capture_region == region
    if accept:
        assert captured[0].size().width() == 60 and captured[0].height() == 75


@pytest.mark.parametrize('change', ['pixel_dpr', 'during_grab'])
def test_fresh_capture_does_not_offer_a_snapshot_with_changed_screen(controller, monkeypatch, change):
    c = controller
    screen = TestScreen()
    original = screen.grabWindow
    def changed_grab(window):
        if change == 'during_grab':
            screen.ratio = 2
        pixmap = original(window)
        if change == 'pixel_dpr':
            pixmap.setDevicePixelRatio(2)
        return pixmap
    monkeypatch.setattr(screen, 'grabWindow', changed_grab)
    monkeypatch.setattr(c.app, 'screens', lambda: [screen])
    monkeypatch.setattr(c, 'recognize', lambda *args: pytest.fail('Cannot OCR a stale screenshot'))
    c.capture_pending = True
    c.show_selection()
    assert not c.overlays and not c.capture_pending
    assert c.window.operation_error.isVisibleTo(c.window)
    assert 'Экран' in c.window.operation_error_details.toPlainText()


def test_selected_region_rechecks_pixmap_dpr_before_publishing(controller, monkeypatch):
    c = controller
    screen = TestScreen()
    monkeypatch.setattr(c.app, 'screens', lambda: [screen])
    monkeypatch.setattr(c, 'recognize', lambda *args: pytest.fail('Cannot OCR a stale screenshot'))
    c.capture_pending = True
    c.show_selection()
    overlay = c.overlays[0]
    overlay.origin, overlay.position = QRect(20, 30, 40, 50).topLeft(), QRect(20, 30, 40, 50).bottomRight()
    overlay.snapshot.setDevicePixelRatio(2)
    overlay.selected.emit(QImage(80, 100, QImage.Format.Format_RGB32))
    assert c.last_capture_region is None
    assert not c.overlays and c.capture_pending and c.capture_timer.isActive()


@pytest.mark.parametrize('action', ['save', 'type', 'edit'])
def test_late_connection_read_cannot_replace_newer_key_state(controller, monkeypatch, action):
    c = controller
    jobs = []
    monkeypatch.setattr(c, 'run_job', lambda fn, success=None, error=None, **kwargs: jobs.append((success, error)))
    assert hasattr(c, 'load_connection_state')
    c.load_connection_state()
    if action == 'save':
        c.window.key.setText('synthetic-key')
        monkeypatch.setattr(c.credentials, 'write', lambda value: None)
        assert c.save()
        jobs[-1][0](False)
        assert c.window.connection_state.text() == 'Подключено'
    else:
        c.window.set_connection_state(True)
        c.window.change_key.click()
        if action == 'type':
            c.window.key.setText('synthetic-new-key')
        jobs[-1][0](True)
        assert not c.window.key.isHidden()
        assert c.window.key.text() == ('synthetic-new-key' if action == 'type' else '')


def test_history_refresh_error_survives_successful_mutation_and_undo_timer(controller, monkeypatch):
    c = controller
    identifier = c.history.add('ocr', 'retained recovery')
    monkeypatch.setattr(c.history, 'visible_entries', lambda *a: (_ for _ in ()).throw(OSError('unavailable')))
    c.delete_history(identifier)
    assert 'Не удалось прочитать историю' in c.window.history_page.history_status.text()
    c.expire_history_undo()
    assert 'Не удалось прочитать историю' in c.window.history_page.history_status.text()
    c.undo_history_delete()
    assert c.history.get(identifier).text == 'retained recovery'
    assert 'Не удалось прочитать историю' in c.window.history_page.history_status.text()
