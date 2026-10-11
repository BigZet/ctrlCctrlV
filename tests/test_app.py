import threading
import time

import pytest
from PySide6.QtGui import QImage
from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.app import Controller
from ctrlcctrlv.core import Operation
from ctrlcctrlv.settings import Settings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, condition, timeout=2):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    app.processEvents()
    assert condition()


class Provider:
    model = "test"

    def recognize(self, png, operation):
        assert png.startswith(b"\x89PNG")
        return '    print("Ж")\n\n'


@pytest.mark.parametrize("preview_enabled", [True, False])
def test_full_ui_flow_publishes_literal_result_on_main_thread(app, tmp_path, preview_enabled):
    c = Controller(app, tmp_path, native=False)
    c.window.show_preview.setChecked(preview_enabled)
    if not preview_enabled:
        assert Settings.load(tmp_path).show_preview is False
        c.preview.show()  # A previously open result must not linger in direct-copy mode.
    c.provider = lambda: Provider()
    original_complete = c.ocr_complete
    def complete(*args):
        assert QThread.currentThread() == app.thread()
        original_complete(*args)
    c.ocr_complete = complete
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    c.recognize(image, Operation())
    wait(app, lambda: c.operation is None and c.pool.activeThreadCount() == 0)
    assert app.clipboard().text() == '    print("Ж")\n\n'
    if preview_enabled:
        assert c.preview.text.toPlainText() == app.clipboard().text()
    assert c.preview.isVisible() == preview_enabled
    c.preview.hide()
    c.tray.hide()
    c.watchdog.stop()
    c.idle_timer.stop()
    c.permissions_timer.stop()
    c.pool.waitForDone()
    c.window.deleteLater()
    c.preview.deleteLater()
    c.deleteLater()
    app.sendPostedEvents()


def test_cancel_ignores_late_success_and_error(app, tmp_path):
    release = threading.Event()

    class SlowProvider(Provider):
        def recognize(self, png, operation):
            release.wait(1)
            return "late result"

    c = Controller(app, tmp_path, native=False)
    c.provider = lambda: SlowProvider()
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    app.clipboard().setText("old")
    c.recognize(image, Operation())
    old_operation = c.operation
    c.cancel()
    assert c.busy()
    assert not c.window.capture_button.isEnabled()
    c.recognize(image, Operation())
    assert c.ocr_worker_operation is old_operation
    release.set()
    wait(app, lambda: c.pool.activeThreadCount() == 0)
    assert not c.busy()
    assert c.window.capture_button.isEnabled()
    assert app.clipboard().text() == "old"
    c.ocr_error(old_operation, "late error")
    assert c.window.status.text() == "Распознавание отменено · буфер не изменён"
    c.tray.hide()
    c.watchdog.stop()
    c.idle_timer.stop()
    c.permissions_timer.stop()
    c.pool.waitForDone()
    c.window.deleteLater()
    c.preview.deleteLater()
    c.deleteLater()
    app.sendPostedEvents()


def test_cancel_during_capture_delay_prevents_overlay(app, tmp_path):
    c = Controller(app, tmp_path, native=False)
    c.capture_pending = True
    c.cancel()
    c.show_selection()
    assert c.overlays == []
    c.tray.hide()
    c.watchdog.stop()
    c.idle_timer.stop()
    c.permissions_timer.stop()
    c.deleteLater()
    c.window.deleteLater()
    c.preview.deleteLater()
    app.sendPostedEvents()


def test_expired_result_does_not_overwrite_clipboard(app, tmp_path):
    c = Controller(app, tmp_path, native=False)
    op = Operation(budget=4, started=time.monotonic() - 4.1)
    c.operation = op
    app.clipboard().setText("keep this")
    c.ocr_complete(op, QImage(), Provider(), "late")
    assert app.clipboard().text() == "keep this"
    assert "4 секунды" in c.window.status.text()
    c.tray.hide()
    c.watchdog.stop()
    c.idle_timer.stop()
    c.permissions_timer.stop()
    c.window.deleteLater()
    c.preview.deleteLater()
    c.deleteLater()
    app.sendPostedEvents()


def test_application_activation_restores_window_without_interrupting_capture(app, tmp_path):
    c = Controller(app, tmp_path, native=False)
    try:
        c.window.hide()
        c.application_activated(Qt.ApplicationState.ApplicationInactive)
        assert not c.window.isVisible()
        c.application_activated(Qt.ApplicationState.ApplicationActive)
        assert c.window.isVisible()
        c.window.hide()
        c.capture_pending = True
        c.application_activated(Qt.ApplicationState.ApplicationActive)
        assert not c.window.isVisible()
        c.capture_pending = False
        (tmp_path / "activate.request").touch()
        c.refresh()
        wait(app, c.window.isVisible)
        assert not (tmp_path / "activate.request").exists()
        c.window.hide()
        c.shutting_down = True
        c.application_activated(Qt.ApplicationState.ApplicationActive)
        assert not c.window.isVisible()
    finally:
        c.tray.hide()
        c.watchdog.stop()
        c.idle_timer.stop()
        c.permissions_timer.stop()
        c.window.deleteLater()
        c.preview.deleteLater()
        c.deleteLater()
        app.sendPostedEvents()


def dispose(c, app):
    c.tray.hide()
    c.watchdog.stop()
    c.idle_timer.stop()
    c.permissions_timer.stop()
    c.typing_timer.stop()
    c.capture_timer.stop()
    c.pool.waitForDone()
    c.window.deleteLater()
    c.preview.deleteLater()
    c.deleteLater()
    app.sendPostedEvents()


@pytest.mark.parametrize("action", ["stop_model", "delete_model"])
def test_model_maintenance_does_not_block_ui(app, tmp_path, action):
    c = Controller(app, tmp_path, native=False)
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        assert QThread.currentThread() != app.thread()
        entered.set()
        release.wait(1)
    c.runtime.stop = slow
    c.runtime.delete = slow
    try:
        getattr(c, action)()
        wait(app, entered.is_set)
        assert c.busy()
        assert not c.window.capture_button.isEnabled()
        release.set()
        wait(app, lambda: c.model_cancel is None and c.pool.activeThreadCount() == 0)
        assert not c.busy()
        assert c.window.capture_button.isEnabled()
    finally:
        release.set()
        dispose(c, app)


def test_progress_does_not_flood_ui_and_keeps_final_update(app):
    from ctrlcctrlv.app import Worker
    from PySide6.QtCore import QObject
    parent = QObject()
    seen = []
    def task(report):
        for index in range(10000):
            report("Download", index, 9999)
    worker = Worker(task, parent)
    worker.signals.progress.connect(lambda *args: seen.append(args))
    worker.run()
    assert seen[0] == ("Download", 0, 9999)
    assert seen[-1] == ("Download", 9999, 9999)
    assert len(seen) < 10


def test_encoding_is_off_main_thread_and_controls_follow_operation(app, tmp_path):
    c = Controller(app, tmp_path, native=False)
    c.settings.show_preview = False
    c.provider = lambda: Provider()
    held = threading.Event()
    entered = threading.Event()
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    class SlowImage:
        def copy(self):
            return image.copy()
        def save(self, buffer, format):
            assert QThread.currentThread() != app.thread()
            entered.set()
            held.wait(1)
            return image.save(buffer, format)
    try:
        c.recognize(SlowImage(), Operation())
        wait(app, entered.is_set)
        assert not c.window.capture_button.isEnabled()
        assert not c.window.provider.isEnabled()
        assert not c.window.cancel_button.isHidden()
        held.set()
        wait(app, lambda: c.operation is None and c.pool.activeThreadCount() == 0)
        assert c.window.capture_button.isEnabled()
        assert c.window.provider.isEnabled()
        assert c.window.cancel_button.isHidden()
    finally:
        held.set()
        dispose(c, app)


def test_gui_typing_keeps_original_text_and_countdown_can_be_cancelled(app, tmp_path):
    from types import SimpleNamespace
    c = Controller(app, tmp_path, native=False)
    c.platform = SimpleNamespace(permissions=lambda: {
        "accessibility": True, "screen": False, "input_monitoring": False})
    typed = []
    c.start_typing = typed.append
    try:
        c.window.navigation.setCurrentRow(1)
        assert not c.window.typing_button.isHidden()
        assert c.window.capture_button.isHidden()
        app.clipboard().setText("original")
        c.schedule_typing()
        assert c.typing_pending
        assert c.typing_timer.isActive()
        app.clipboard().setText("changed during countdown")
        c.finish_typing_countdown()
        assert typed == ["original"]
        c.schedule_typing()
        c.cancel()
        assert not c.typing_timer.isActive()
        c.finish_typing_countdown()
        assert typed == ["original"]
        assert c.pending_typing_text is None
    finally:
        dispose(c, app)


def test_engine_crash_immediately_offers_preparation_without_native_adapter(app, tmp_path):
    from types import SimpleNamespace
    c = Controller(app, tmp_path, native=False)
    c.runtime.installed = lambda model: True
    c.runtime.engine_directory.mkdir(parents=True)
    (c.runtime.engine_directory / "llama-server").touch()
    c.runtime.ready = True
    c.runtime.model_id = c.window.local_model.currentData()
    c.runtime.process = SimpleNamespace(poll=lambda: 1)
    try:
        c.refresh()
        assert not c.runtime.ready
        assert not c.window.prepare_button.isHidden()
        assert "завершился" in c.window.status.text()
        assert "подготовьте" in c.window.local_info.text()
    finally:
        c.runtime.process = None
        dispose(c, app)


def test_installed_model_with_missing_engine_has_gui_repair_action(app, tmp_path):
    c = Controller(app, tmp_path, native=False)
    c.runtime.installed = lambda model: True
    try:
        c.refresh()
        assert not c.window.install_button.isHidden()
        assert c.window.install_button.text() == "Восстановить движок"
        assert c.window.prepare_button.isHidden()
        assert "отсутствует" in c.window.local_info.text()
    finally:
        dispose(c, app)


def test_failed_ocr_retry_uses_failed_image_and_current_settings(app, tmp_path):
    from ctrlcctrlv.core import OperationError
    c = Controller(app, tmp_path, native=False)
    failed_image = QImage(32, 32, QImage.Format.Format_RGB32)
    failed_image.fill(0xff123456)
    older_image = QImage(32, 32, QImage.Format.Format_RGB32)
    older_image.fill(0xffffffff)
    c.preview.display(older_image, "previous result", 0.1, "old model")
    c.provider = lambda: (_ for _ in ()).throw(OperationError("Модель не готова"))
    try:
        assert c.window.retry_button.isHidden()
        app.clipboard().setText("keep clipboard")
        c.recognize(failed_image, Operation())
        assert not c.window.retry_button.isHidden()
        assert c.window.retry_button.isEnabled()
        assert app.clipboard().text() == "keep clipboard"
        assert c.preview.text.toPlainText() == "previous result"
        c.window.show_preview.setChecked(False)
        c.provider = lambda: Provider()
        c.window.retry_button.click()
        assert c.window.retry_button.isHidden()
        wait(app, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
        assert app.clipboard().text() == '    print("Ж")\n\n'
        assert c.last_image.pixel(0, 0) == failed_image.pixel(0, 0)
        assert not c.preview.isVisible()
    finally:
        dispose(c, app)


def test_deadline_retry_waits_for_original_worker_to_release(app, tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []

    class HeldProvider(Provider):
        def recognize(self, png, operation):
            calls.append(operation)
            entered.set()
            release.wait(2)
            return "late result"

    c = Controller(app, tmp_path, native=False)
    c.provider = lambda: HeldProvider()
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    try:
        app.clipboard().setText("unchanged")
        operation = Operation(budget=0.1)
        c.recognize(image, operation)
        wait(app, entered.is_set)
        wait(app, lambda: c.operation is None)
        assert not c.window.retry_button.isHidden()
        assert not c.window.retry_button.isEnabled()
        c.retry_failed_ocr()
        assert len(calls) == 1
        assert c.ocr_worker_operation is operation
        release.set()
        wait(app, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
        assert c.window.retry_button.isEnabled()
        assert app.clipboard().text() == "unchanged"
        c.provider = lambda: Provider()
        c.window.retry_button.click()
        wait(app, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
        assert not c.window.retry_button.isVisible()
        assert app.clipboard().text() == '    print("Ж")\n\n'
    finally:
        release.set()
        dispose(c, app)


@pytest.mark.parametrize("show_preview", [True, False])
def test_slow_ocr_continues_after_target_and_copies_complete_result(app, tmp_path, show_preview):
    entered, release = threading.Event(), threading.Event()

    class HeldProvider(Provider):
        def recognize(self, png, operation):
            entered.set()
            release.wait(2)
            operation.check()
            return '    полный результат\n\n'

    c = Controller(app, tmp_path, native=False)
    c.settings.show_preview = show_preview
    c.provider = lambda: HeldProvider()
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    try:
        app.clipboard().setText("previous")
        operation = Operation(started=time.monotonic() - 5)
        c.recognize(image, operation)
        wait(app, entered.is_set)
        c.check_deadline()
        assert c.operation is operation
        assert not operation.cancelled.is_set()
        assert c.busy()
        assert "цель 4 с" in c.window.status.text()
        assert c.window.cancel_button.isVisibleTo(c.window)
        assert app.clipboard().text() == "previous"
        assert c.window.retry_button.isHidden()
        release.set()
        wait(app, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
        assert app.clipboard().text() == '    полный результат\n\n'
        assert "дольше цели 4 с" in c.window.status.text()
        assert c.preview.isVisible() is show_preview
        if show_preview:
            assert c.preview.text.toPlainText() == app.clipboard().text()
            assert "дольше цели 4 с" in c.preview.status.text()
    finally:
        release.set()
        dispose(c, app)


def test_cancel_network_wait_releases_ui_and_allows_next_capture(app, tmp_path):
    import asyncio
    import httpx
    from ctrlcctrlv.ocr import OCRProvider

    entered, closed = threading.Event(), threading.Event()

    async def request(req):
        entered.set()
        try:
            await asyncio.sleep(3600)
        finally:
            closed.set()

    class WaitingProvider(OCRProvider):
        def recognize(self, png, operation):
            return super().recognize(png, operation, httpx.MockTransport(request))

    c = Controller(app, tmp_path, native=False)
    c.provider = lambda: WaitingProvider("http://127.0.0.1/ocr", "test", "key", local=True)
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    try:
        app.clipboard().setText("previous")
        c.recognize(image, Operation())
        wait(app, entered.is_set)
        c.cancel()
        wait(app, lambda: not c.busy() and c.pool.activeThreadCount() == 0, timeout=1)
        assert closed.is_set()
        assert c.window.capture_button.isEnabled()
        assert c.window.status.text() == "Распознавание отменено · буфер не изменён"
        assert app.clipboard().text() == "previous"
        c.provider = lambda: Provider()
        c.recognize(image, Operation())
        wait(app, lambda: not c.busy() and c.pool.activeThreadCount() == 0)
        assert app.clipboard().text() == '    print("Ж")\n\n'
    finally:
        c.cancel()
        dispose(c, app)


def test_settings_file_failure_keeps_previous_state_and_never_writes_key(app, tmp_path, monkeypatch):
    from types import SimpleNamespace
    c = Controller(app, tmp_path, native=False)
    c.settings.save(tmp_path)
    original = (tmp_path / "settings.json").read_bytes()
    writes = []
    c.credentials = SimpleNamespace(write=writes.append)
    c.window.key.setText("synthetic-secret")
    c.window.remote_model.setText("another/model")

    def fail(settings, directory):
        raise OSError("sensitive path and secret")

    monkeypatch.setattr(Settings, "save", fail)
    try:
        assert not c.save()
        assert writes == []
        assert c.window.key.text() == "synthetic-secret"
        assert c.settings.remote_model != "another/model"
        assert (tmp_path / "settings.json").read_bytes() == original
        assert "Не удалось записать настройки" in c.window.status.text()
        assert "sensitive" not in c.window.status.text()
        assert "synthetic-secret" not in c.window.status.text()
        assert c.window.capture_button.isEnabled()
    finally:
        dispose(c, app)


def test_key_failure_preserves_field_and_reports_saved_settings_then_retries(app, tmp_path):
    from types import SimpleNamespace
    c = Controller(app, tmp_path, native=False)
    c.window.key.setText("synthetic-secret")
    c.window.remote_model.setText("another/model")

    def fail(value):
        raise RuntimeError("private keychain error: " + value)

    c.credentials = SimpleNamespace(write=fail)
    try:
        assert not c.save()
        assert c.settings.remote_model == "another/model"
        assert Settings.load(tmp_path).remote_model == "another/model"
        assert c.window.key.text() == "synthetic-secret"
        assert "Настройки сохранены, но API-ключ не сохранён" in c.window.status.text()
        assert "synthetic-secret" not in c.window.status.text()
        assert "private" not in c.window.status.text()
        assert "synthetic-secret" not in (tmp_path / "settings.json").read_text()
        writes = []
        c.credentials = SimpleNamespace(write=writes.append)
        assert c.save()
        assert writes == ["synthetic-secret"]
        assert not c.window.key.text()
        assert c.window.status.text() == "Настройки и API-ключ сохранены"
    finally:
        dispose(c, app)


def test_hotkey_registration_failure_does_not_misreport_settings_write(app, tmp_path):
    c = Controller(app, tmp_path, native=False)
    c.window.capture_hotkey.setText("ctrl+cmd+5")

    def fail():
        raise RuntimeError("private native error")

    c.retry_hotkeys = fail
    try:
        assert not c.save()
        assert Settings.load(tmp_path).capture_hotkey == "ctrl+cmd+5"
        assert c.settings.capture_hotkey == "ctrl+cmd+5"
        assert "Настройки сохранены, но горячие клавиши не подключены" in c.window.status.text()
        assert "private" not in c.window.status.text()
    finally:
        dispose(c, app)
