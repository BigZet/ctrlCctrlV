import argparse
from contextlib import nullcontext
import json
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
import uuid

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QLockFile, QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QImage
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .catalog import MODELS
from .core import Operation, OperationError
from .ocr import OCRProvider
from .platforms import active_target, create_platform
from .runtime import LocalRuntime
from .settings import Credentials, Settings, data_directory
from .ui import DiagnosticsDialog, Preview, SelectionOverlay, SettingsWindow, SetupWizard


def diagnostic_print(message):
    # PyInstaller's windowed Windows bootloader sets stdout/stderr to None.
    if sys.stdout is not None:
        try:
            print(message, flush=True)
        except UnicodeEncodeError:
            # A redirected Windows console may use a Western code page. A log
            # encoding failure must not interrupt diagnostic shutdown / cleanup.
            print(message.encode("ascii", errors="backslashreplace").decode("ascii"), flush=True)


class WorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)
    progress = Signal(str, int, int)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function, parent):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals(parent)

    def run(self):
        try:
            last = [None, 0.0]
            def report(label, done, total):
                now = time.monotonic()
                if label != last[0] or now - last[1] >= 0.1 or (total > 1 and done >= total):
                    last[:] = [label, now]
                    self.signals.progress.emit(label, done, total)
            self.signals.success.emit(self.function(report))
        except Exception as error:
            # Never include raw HTTP bodies or request payloads in error messages.
            message = str(error) if isinstance(error, (OperationError, OSError, ValueError)) else f"Ошибка операции: {type(error).__name__}"
            self.signals.error.emit(message)
        finally:
            self.signals.finished.emit()


class Hotkeys(QObject):
    action = Signal(str)


class Controller(QObject):
    def __init__(self, app, directory=None, platform_adapter=None, native=True):
        super().__init__()
        self.app = app
        self.directory = directory or data_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.first_run = not (self.directory / "settings.json").exists()
        self.setup_wizard = None
        self.settings = Settings.load(self.directory)
        self.credentials = Credentials()
        self.runtime = LocalRuntime(self.directory, acceleration=self.settings.acceleration)
        self.review_runtime = LocalRuntime(self.directory, session="review", acceleration=self.settings.acceleration)
        self.review_cancel = None
        self.diagnostics_cancel = None
        self.platform = platform_adapter
        if native and self.platform is None:
            self.platform = create_platform()
        self.window = SettingsWindow(self.settings)
        self.preview = Preview()
        self.diagnostics_window = DiagnosticsDialog(self.window)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.jobs = {}
        self.operation = None
        self.ocr_worker_operation = None
        self.model_cancel = self.typing_cancel = None
        self.overlays = []
        self.capture_pending = False
        self.capture_timer = QTimer(self)
        self.capture_timer.setSingleShot(True)
        self.capture_timer.setInterval(200)
        self.capture_timer.timeout.connect(self.show_selection)
        self.typing_pending = False
        self.pending_typing_text = None
        self.typing_timer = QTimer(self)
        self.typing_timer.setSingleShot(True)
        self.typing_timer.setInterval(3000)
        self.typing_timer.timeout.connect(self.finish_typing_countdown)
        self.shutting_down = False
        self.last_image = None
        self.retry_image = None
        self.ocr_image = None
        self.ocr_elapsed_second = 0
        self.last_activity = time.monotonic()
        self.hotkeys = Hotkeys()
        self.permission_snapshot = None
        self.hotkeys.action.connect(self.handle_hotkey)
        self.window.save_requested.connect(self.save)
        self.window.capture_requested.connect(self.capture)
        self.window.retry_requested.connect(self.retry_failed_ocr)
        self.window.typing_requested.connect(self.schedule_typing)
        self.window.install_requested.connect(self.install)
        self.window.start_requested.connect(self.start_model)
        self.window.stop_requested.connect(self.stop_model)
        self.window.delete_requested.connect(self.delete_model)
        self.window.permissions_requested.connect(self.request_permissions)
        self.window.permission_settings_requested.connect(self.open_permission_settings)
        self.window.cancel_requested.connect(self.cancel)
        self.window.preview_option_changed.connect(self.set_preview_option)
        self.window.diagnostics_requested.connect(self.show_diagnostics)
        self.window.local_model.currentIndexChanged.connect(self.refresh)
        self.window.acceleration.currentIndexChanged.connect(self.refresh)
        self.window.hardware_requested.connect(self.check_hardware)
        self.preview.copy_requested.connect(self.copy_edited)
        self.preview.repeat_requested.connect(self.repeat)
        self.preview.review_requested.connect(self.review_text)
        self.preview.review_cancel_requested.connect(self.cancel)
        self.preview.accept_review_requested.connect(self.accept_review)
        self.preview.review_invalidated.connect(self.review_edited)
        self.preview.syntax_requested.connect(self.check_syntax)
        self._create_tray()
        self.app.applicationStateChanged.connect(self.application_activated)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(25)
        self.watchdog.timeout.connect(self.check_deadline)
        self.watchdog.start()
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(10_000)
        self.idle_timer.timeout.connect(self.check_idle)
        self.idle_timer.start()
        self.permissions_timer = QTimer(self)
        self.permissions_timer.setInterval(3000)
        self.permissions_timer.timeout.connect(self.refresh)
        self.permissions_timer.start()
        self.register_hotkeys()
        self.refresh()

    def _create_tray(self):
        assets = Path(__file__).parent / "assets"
        icon = QIcon(str(assets / "app.png"))
        self.app.setWindowIcon(icon)
        self.window.setWindowIcon(icon)
        self.preview.setWindowIcon(icon)
        menu_icon = QIcon(str(assets / "menu.png"))
        menu_icon.setIsMask(True)
        self.tray = QSystemTrayIcon(menu_icon, self)
        self.tray.setToolTip("ctrlCctrlV")
        self.tray_menu = menu = QMenu()
        for label, handler in (("Выделить область → OCR", self.capture), ("Напечатать буфер", self.type_clipboard),
                               ("Остановить", self.cancel), ("Настройки", self.show_settings), ("Выход", self.quit)):
            action = QAction(label, menu)
            action.triggered.connect(handler)
            menu.addAction(action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_settings() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def run_job(self, function, success=None, error=None, progress=None):
        identifier = uuid.uuid4().hex
        worker = Worker(function, self)
        self.jobs[identifier] = worker
        if success:
            worker.signals.success.connect(success, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(error or self.status, Qt.ConnectionType.QueuedConnection)
        if progress:
            worker.signals.progress.connect(progress, Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(lambda: QTimer.singleShot(0, lambda: self.jobs.pop(identifier, None)), Qt.ConnectionType.QueuedConnection)
        self.pool.start(worker)

    def status(self, text):
        self.window.status.setText(text)
        self.tray.setToolTip("ctrlCctrlV · " + text[:120])
        self.window.set_busy(self.busy())
        self.preview.repeat_button.setEnabled(not self.busy())
        self.preview.review_button.setEnabled(not self.busy() and self.preview.source_image is not None)

    def refresh(self):
        activation = self.directory / "activate.request"
        if activation.exists():
            activation.unlink(missing_ok=True)
            QTimer.singleShot(0, self.reopen_window)
        self.window.set_busy(self.busy())
        if self.runtime.ready and (not self.runtime.process or self.runtime.process.poll() is not None):
            self.runtime.ready = False
            self.status("Локальный движок завершился; подготовьте модель заново")
        identifier = self.window.local_model.currentData()
        model = MODELS[identifier]
        installed = self.runtime.installed(identifier)
        acceleration = self.window.acceleration.currentData()
        engine_available = LocalRuntime(self.directory, acceleration=acceleration).binary is not None
        ready = (self.runtime.ready and self.runtime.model_id == identifier and
                 (self.runtime.loaded_acceleration or self.runtime.acceleration) == acceleration)
        state = ("Готово к распознаванию" if ready else
            "Движок отсутствует · восстановите установку" if installed and not engine_available else
            "Установлена · подготовьте модель" if installed else "Модель ещё не установлена")
        if installed:
            state += f" · файлы модели {sum(f.size for f in model.files) / 1_000_000_000:.1f} ГБ"
        self.window.local_info.setText(state)
        self.window.local_info.setToolTip(f"Лицензия: {model.license}. Качество и скорость проверяйте на своих примерах.")
        self.window.install_button.setText("Восстановить движок" if installed and not engine_available else "Установить")
        self.window.install_button.setVisible(not installed or not engine_available)
        self.window.prepare_button.setVisible(installed and engine_available and not ready)
        self.window.install_button.setEnabled(not self.busy())
        self.window.prepare_button.setEnabled(not self.busy())
        self.window.provider_pages.update_current_size()
        if self.platform:
            values = self.platform.permissions()
            self.window.show_permissions(values)
            if values != self.permission_snapshot:
                self.permission_snapshot = values.copy()
                if values["input_monitoring"] and not self.hotkeys_registered:
                    self.retry_hotkeys()
                elif not values["input_monitoring"] and self.hotkeys_registered:
                    self.platform.stop_hotkeys()
                    self.hotkeys_registered = False

    def register_hotkeys(self):
        self.bindings = {"capture": self.settings.capture_hotkey, "type": self.settings.typing_hotkey,
            "cancel": self.settings.cancel_hotkey}
        self.hotkeys_registered = bool(self.platform and self.platform.permissions()["input_monitoring"]
            and self.platform.start_hotkeys(self.hotkeys.action.emit, self.bindings))
        if not self.hotkeys_registered:
            self.status(getattr(self.platform, "last_hotkey_error", "") or
                        "Глобальные клавиши недоступны. Операции доступны из меню; проверьте разрешения.")

    def handle_hotkey(self, action):
        if self.setup_wizard and self.setup_wizard.isVisible():
            return
        if action == "capture":
            self.capture()
        elif action == "type":
            self.type_clipboard()
        else:
            self.cancel()

    def application_activated(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self.reopen_window()

    def reopen_window(self):
        if self.shutting_down or self.overlays or self.capture_pending:
            return
        if self.setup_wizard and self.setup_wizard.isVisible():
            self.setup_wizard.raise_()
            self.setup_wizard.activateWindow()
            return
        if self.typing_pending or self.typing_cancel:
            self.cancel()
        if self.preview.isVisible() and not self.operation:
            self.preview.raise_()
            self.preview.activateWindow()
        else:
            self.show_settings()

    def show_settings(self):
        self.refresh()
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def show_startup(self):
        if not self.first_run:
            self.show_settings()
            return
        self.setup_wizard = SetupWizard(self.window)
        self.setup_wizard.accepted.connect(self.complete_setup)
        self.setup_wizard.rejected.connect(self.show_settings)
        self.setup_wizard.open()

    def complete_setup(self):
        self.window.provider.setCurrentIndex(self.window.provider.findData(self.setup_wizard.provider.currentData()))
        self.window.acceleration.setCurrentIndex(self.window.acceleration.findData(self.setup_wizard.acceleration.currentData()))
        if not self.save(announce=False):
            self.show_settings()
            return
        self.first_run = False
        self.show_settings()
        if self.settings.provider == "openrouter":
            self.window.key.setFocus()
        self.status("Введите API-ключ и сохраните настройки" if self.settings.provider == "openrouter" else
                    "Установите движок и модель, затем нажмите «Подготовить»")

    def save(self, announce=True):
        try:
            updated = replace(self.settings)
            self.window.apply_to(updated)
            value = self.window.key.text().strip()
            updated.save(self.directory)
        except ValueError as error:
            self.status(str(error))
            return False
        except Exception:
            self.status("Не удалось записать настройки. Проверьте свободное место и права доступа.")
            return False
        self.settings = updated
        new_bindings = {"capture": updated.capture_hotkey, "type": updated.typing_hotkey, "cancel": updated.cancel_hotkey}
        if new_bindings != self.bindings:
            try:
                self.retry_hotkeys()
            except Exception:
                self.status("Настройки сохранены, но горячие клавиши не подключены. Проверьте разрешения в разделе «Система».")
                return False
        if value:
            try:
                self.credentials.write(value)
            except Exception:
                self.status("Настройки сохранены, но API-ключ не сохранён. Повторите попытку; введённый ключ оставлен в поле.")
                self.window.navigation.setCurrentRow(0)
                self.window.key.setFocus()
                return False
            self.window.key.clear()
            self.window.key.setPlaceholderText("API-ключ сохранён в системном хранилище; введите новый для замены")
        self.window.mark_saved()
        if announce:
            self.status("Настройки и API-ключ сохранены" if value else "Настройки сохранены")
        return True

    def set_preview_option(self, enabled):
        updated = replace(self.settings, show_preview=enabled)
        try:
            updated.save(self.directory)
            self.settings = updated
        except (OSError, ValueError):
            self.window.show_preview.blockSignals(True)
            self.window.show_preview.setChecked(self.settings.show_preview)
            self.window.show_preview.blockSignals(False)
            self.status("Не удалось сохранить настройку предпросмотра")

    def request_permissions(self):
        if self.platform:
            if not all(self.platform.permissions().values()):
                self.platform.request_permissions()
                QTimer.singleShot(2000, self.retry_hotkeys)
            self.refresh()

    def open_permission_settings(self, permission):
        if self.platform:
            try:
                self.platform.open_permission_settings(permission)
            except OperationError as exc:
                self.status(str(exc))
            self.refresh()

    def retry_hotkeys(self):
        if self.platform:
            self.platform.stop_hotkeys()
            self.register_hotkeys()
        self.refresh()

    def busy(self):
        return bool(self.operation or self.ocr_worker_operation or self.typing_cancel or self.model_cancel or self.overlays or self.capture_pending or self.typing_pending or self.review_cancel or self.diagnostics_cancel)

    def provider(self):
        if self.settings.provider == "openrouter":
            key = self.credentials.read()
            if not key:
                raise OperationError("Введите и сохраните API-ключ OpenRouter в настройках")
            return OCRProvider("https://openrouter.ai/api/v1/chat/completions",
                self.settings.remote_model, key, self.settings.max_tokens, mode=self.settings.remote_mode)
        if (not self.runtime.ready or self.runtime.model_id != self.settings.local_model or
                (self.runtime.loaded_acceleration or self.runtime.acceleration) != self.settings.acceleration):
            raise OperationError("Локальная модель не готова. Нажмите «Подготовить» в настройках.")
        model = MODELS[self.settings.local_model]
        return OCRProvider(self.runtime.endpoint + "/v1/chat/completions",
            self.runtime.model_id, self.runtime.key, self.settings.max_tokens, True, model.mode)

    def capture(self):
        if self.busy():
            self.status("Сначала завершите или отмените текущую операцию")
            return
        if not self.save(announce=False):
            return
        try:
            self.provider()  # Do not capture if OCR is not configured/ready.
            if self.platform and not self.platform.permissions()["screen"]:
                raise OperationError("Разрешите захват экрана в разделе «Система»")
        except Exception as error:
            self.status(str(error))
            self.show_settings()
            return
        self.window.hide()
        self.preview.hide()
        self.capture_pending = True
        self.status("Выделите область · Esc для отмены")
        self.capture_timer.start()

    def show_selection(self):
        if not self.capture_pending:
            return
        self.capture_pending = False
        if self.busy():
            return
        screenshots = [(screen, screen.grabWindow(0)) for screen in self.app.screens()]
        if not screenshots or any(pixmap.isNull() for _, pixmap in screenshots):
            self.status("Не удалось получить снимок экрана. Проверьте доступ к экрану и активный сеанс.")
            self.show_settings()
            return
        for screen, snapshot in screenshots:
            overlay = SelectionOverlay(screen, snapshot)
            overlay.selected.connect(self.selected)
            overlay.cancelled.connect(self.selection_cancelled)
            self.overlays.append(overlay)
        for overlay in self.overlays:
            overlay.show()
        self.overlays[0].activateWindow()

    def selection_cancelled(self):
        self.close_selection()
        self.status("Выделение отменено")

    def close_selection(self):
        for overlay in self.overlays:
            overlay.close()
            overlay.deleteLater()
        self.overlays.clear()

    def selected(self, image):
        # t0 starts before PNG encoding and before any provider request.
        operation = Operation()
        self.close_selection()
        self.recognize(image, operation)

    def repeat(self):
        image = self.preview.source_image if self.preview.isVisible() else self.last_image
        if image is not None and not self.busy():
            self.recognize(image, Operation())

    def retry_failed_ocr(self):
        if self.retry_image is not None and not self.busy() and self.save(announce=False):
            self.recognize(self.retry_image, Operation())

    def recognize(self, image, operation):
        if self.busy():
            self.status("Дождитесь завершения текущей операции")
            return
        self.operation = operation
        self.ocr_image = image.copy()
        self.retry_image = None
        self.window.retry_button.hide()
        try:
            provider = self.provider()
            self.operation = operation
            self.last_image = self.ocr_image
            self.last_activity = time.monotonic()
            self.ocr_elapsed_second = 0
            self.status("Распознавание…")
            self.window.progress.setRange(0, 0)
            def task(progress):
                operation.check()
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.save(buffer, "PNG"):
                    raise OperationError("Не удалось подготовить изображение")
                png = bytes(buffer.data())
                operation.check()
                return provider.recognize(png, operation)
            self.ocr_worker_operation = operation
            self.run_job(task,
                lambda text: self.finish_ocr_job(operation, lambda: self.ocr_complete(operation, image, provider, text)),
                lambda message: self.finish_ocr_job(operation, lambda: self.ocr_error(operation, message)))
        except Exception as error:
            self.ocr_error(operation, str(error))

    def finish_ocr_job(self, operation, callback):
        if self.ocr_worker_operation is operation:
            self.ocr_worker_operation = None
        callback()
        # Cancelled/expired results are discarded, but controls stay disabled until
        # the outstanding request releases the server's single inference slot.
        if operation.cancelled.is_set() and self.retry_image is None and not self.busy():
            self.status("Распознавание отменено · буфер не изменён")
        else:
            self.status(self.window.status.text())

    def ocr_complete(self, operation, image, provider, text):
        if self.operation is not operation:
            return
        try:
            elapsed = operation.publish(text, self.app.clipboard().setText)
            self.operation = None
            self.status(f"Скопировано · {elapsed:.2f} с" + (" · дольше цели 4 с" if elapsed > 4 else ""))
            if self.settings.show_preview:
                self.preview.display(image, text, elapsed, provider.model)
            else:
                self.preview.hide()
            self.last_activity = time.monotonic()
        except OperationError as error:
            self.ocr_error(operation, str(error))

    def ocr_error(self, operation, message):
        if self.operation is not operation:
            return
        operation.cancel()
        self.operation = None
        self.retry_image = self.ocr_image
        self.window.retry_button.setVisible(self.retry_image is not None)
        self.status(message)
        self.window.show()

    def check_deadline(self):
        if self.operation:
            try:
                self.operation.check()
                elapsed_second = int(time.monotonic() - self.operation.started)
                if elapsed_second >= 4 and elapsed_second != self.ocr_elapsed_second:
                    self.ocr_elapsed_second = elapsed_second
                    self.status(f"Распознавание… {elapsed_second} с · цель 4 с")
            except OperationError as error:
                self.ocr_error(self.operation, str(error))

    def review_edited(self):
        if self.review_cancel:
            self.review_cancel.set()
            self.preview.status.setText("Текст изменён · повторите проверку после завершения отмены")

    def review_text(self):
        if self.busy() or self.preview.source_image is None or not self.save(announce=False):
            return
        from .review import ReviewProvider, local_review_session
        settings = replace(self.settings)
        image = self.preview.source_image.copy()
        original = self.preview.text.toPlainText()
        if not original.strip():
            self.preview.status.setText("Нет текста для проверки")
            return
        if settings.provider == "local" and not self.review_runtime.installed(settings.review_local_model):
            self.preview.status.setText("Для проверки установите " + MODELS[settings.review_local_model].label.split(" · ")[0] + " в настройках OCR")
            return
        cancel = self.review_cancel = threading.Event()
        self.preview.review_result = None
        self.preview.full_review_result = None
        self.preview.review_panel.hide()
        self.preview.set_review_busy(True)
        self.status("Проверка текста…")
        self.preview.status.setText("Подготовка проверки · исходный текст уже в буфере")
        restore_errors = []
        def task(progress):
            started = time.monotonic()
            session = (local_review_session(self.runtime, self.review_runtime, settings.review_local_model,
                       settings.acceleration, cancel, progress, restore_errors, lambda: self.shutting_down)
                       if settings.provider == "local" else nullcontext(None))
            try:
                with session as runtime:
                    if settings.provider == "local":
                        provider = OCRProvider(runtime.endpoint + "/v1/chat/completions",
                                               settings.review_local_model, runtime.key, settings.max_tokens, True)
                    else:
                        key = self.credentials.read()
                        if not key:
                            raise OperationError("Сохраните API-ключ OpenRouter в настройках")
                        provider = OCRProvider("https://openrouter.ai/api/v1/chat/completions",
                                               settings.review_remote_model, key, settings.max_tokens)
                    operation = Operation(budget=30, cancelled=cancel)
                    progress("Сверка текста со снимком…", 0, 1)
                    buffer = QBuffer()
                    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                    if not image.save(buffer, "PNG"):
                        raise OperationError("Не удалось подготовить изображение")
                    result = ReviewProvider(provider).inspect(bytes(buffer.data()), original, operation)
                result = replace(result, elapsed=time.monotonic() - started)
                result.prepare()
                if cancel.is_set():
                    raise OperationError("Проверка отменена")
                return result
            finally:
                if settings.provider != "local":
                    self.review_runtime.stop()
        def complete(result):
            if self.review_cancel is not cancel:
                return
            self.review_cancel = None
            self.preview.set_review_busy(False)
            self.status("Проверка завершена" if not cancel.is_set() else "Проверка отменена")
            if not cancel.is_set():
                self.preview.propose(result)
                if restore_errors:
                    self.preview.status.setText("Проверка завершена; OCR не восстановлена: " + restore_errors[0])
            self.refresh()
        def failed(message):
            if self.review_cancel is not cancel:
                return
            self.review_cancel = None
            self.preview.set_review_busy(False)
            self.status("Проверка отменена" if cancel.is_set() else "Проверка не завершена")
            if self.preview.text.toPlainText() == original:
                self.preview.status.setText("Проверка отменена · исходный текст сохранён" if cancel.is_set() else message)
        def progress(message, *_):
            if self.review_cancel is cancel and not cancel.is_set():
                self.preview.status.setText(message)
        self.run_job(task, complete, failed, progress)

    def accept_review(self, result):
        if result is None or self.busy() or self.preview.review_result is not result:
            return
        if self.preview.text.toPlainText() != result.original:
            self.preview.invalidate_review()
            return
        self.preview.replace_text(result.proposed)
        self.copy_edited(result.proposed)

    def copy_edited(self, text):
        self.app.clipboard().setText(text)
        self.preview.mark_copied(text)
        self.preview.status.setText("Исправленный текст скопирован")

    def check_syntax(self, text, language, revision):
        if self.shutting_down:
            return
        from .syntax import syntax_hint
        self.run_job(lambda progress: syntax_hint(text, language),
                     lambda hint: self.preview.show_syntax_hint(revision, hint),
                     lambda message: None)

    def show_diagnostics(self):
        if self.busy():
            return
        from .diagnostics import collect_diagnostics
        try:
            permissions = self.platform.permissions() if self.platform else {}
        except Exception:
            self.status("Не удалось прочитать разрешения для диагностики")
            return
        provider, model = self.settings.provider, self.settings.local_model
        registered = self.hotkeys_registered
        cancel = self.diagnostics_cancel = threading.Event()
        self.status("Подготовка диагностики…")
        def complete(report):
            if self.diagnostics_cancel is not cancel:
                return
            self.diagnostics_cancel = None
            self.status("Диагностика отменена" if cancel.is_set() else "Диагностика готова")
            if not cancel.is_set() and not self.shutting_down:
                self.diagnostics_window.display(report)
        def failed(message):
            if self.diagnostics_cancel is cancel:
                self.diagnostics_cancel = None
                self.status("Диагностика отменена" if cancel.is_set() else "Не удалось подготовить диагностику")
        self.run_job(lambda progress: collect_diagnostics(self.directory, self.runtime, self.review_runtime,
                     permissions, provider, model, registered, "idle", cancel), complete, failed)

    def model_progress(self, label, done, total):
        if label.endswith(".gguf") and total > 1:
            self.status(f"Загрузка модели · {done / total:.0%} · {done / 1e9:.2f} / {total / 1e9:.2f} ГБ")
        elif label.startswith("Проверка SHA256"):
            self.status("Проверка загруженных файлов…")
        else:
            self.status(label)
        self.window.progress.setToolTip(label)
        self.window.progress.show()
        self.window.progress.setRange(0, 100 if total > 1 else 0)
        if total > 1:
            self.window.progress.setValue(round(done / total * 100))

    def model_job(self, start=False):
        if self.busy():
            self.status("Сначала завершите или отмените текущую операцию")
            return
        if not self.save(announce=False):
            return
        cancelled = threading.Event()
        self.model_cancel = cancelled
        self.status("Подготовка модели…" if start else "Подготовка загрузки…")
        identifier = self.settings.local_model
        self.runtime.acceleration = self.settings.acceleration

        def task(progress):
            if start:
                self.runtime.start(identifier, cancelled, progress)
            else:
                self.runtime.install(identifier, cancelled, progress)
            return "Модель готова к OCR" if start else "Модель установлена. Нажмите «Подготовить»."

        def complete(message):
            if self.model_cancel is not cancelled:
                return
            self.model_cancel = None
            self.window.progress.hide()
            self.status(message)
            self.refresh()
            if self.runtime.hardware:
                from .hardware import describe_hardware
                self.window.hardware_info.setText(describe_hardware(self.runtime.hardware, MODELS[identifier]))
            self.last_activity = time.monotonic()

        self.run_job(task, complete, complete, self.model_progress)

    def install(self):
        self.model_job()

    def check_hardware(self):
        if self.busy() or not self.save(announce=False):
            return
        from .hardware import describe_hardware, inspect_hardware, require_capacity
        cancel = self.model_cancel = threading.Event()
        acceleration = self.settings.acceleration
        model = MODELS[self.settings.local_model]
        runtime = LocalRuntime(self.directory, acceleration=acceleration)
        self.status("Проверка оборудования…")
        def task(progress):
            report = inspect_hardware(runtime.binary, acceleration, cancel)
            description = describe_hardware(report, model)
            try:
                require_capacity(report, model)
            except OperationError as error:
                description += "\n" + str(error)
            return description
        def complete(description):
            if self.model_cancel is cancel:
                self.model_cancel = None
                if not cancel.is_set():
                    self.window.hardware_info.setText(description)
                self.status("Проверка оборудования отменена" if cancel.is_set() else "Оборудование проверено")
        def failed(message):
            if self.model_cancel is cancel:
                self.model_cancel = None
                self.window.hardware_info.setText(message)
                self.status("Проверка оборудования отменена" if cancel.is_set() else message)
        self.run_job(task, complete, failed)

    def start_model(self):
        self.model_job(start=True)

    def stop_model(self):
        if self.model_cancel:
            self.model_cancel.set()
            self.status("Отмена подготовки модели…")
            return
        if self.busy():
            self.status("Сначала остановите текущую операцию")
            return
        self.runtime_maintenance(self.runtime.stop, "Выгрузка модели…", "Модель выгружена")

    def delete_model(self):
        if self.busy():
            self.status("Сначала завершите или отмените операцию")
            return
        identifier = self.window.local_model.currentData()
        def delete():
            # Windows keeps mapped GGUF files locked while either owned server
            # is using them; release the reviewer before deleting shared files.
            if self.review_runtime.model_id == identifier:
                self.review_runtime.stop()
            self.runtime.delete(identifier)
        self.runtime_maintenance(delete, "Удаление модели…", "Модель удалена")

    def runtime_maintenance(self, action, pending_message, complete_message):
        token = self.model_cancel = threading.Event()
        self.status(pending_message)
        def complete(message):
            if self.model_cancel is token:
                self.model_cancel = None
                self.status(message)
                self.refresh()
        self.run_job(lambda progress: (action(), complete_message)[1], complete, complete)

    def check_idle(self):
        if not self.busy() and time.monotonic() - self.last_activity > self.settings.idle_minutes * 60:
            if self.runtime.ready or self.review_runtime.ready:
                def unload():
                    self.runtime.stop()
                    self.review_runtime.stop()
                self.runtime_maintenance(unload, "Выгрузка моделей…", "Модели выгружены после простоя")

    def schedule_typing(self):
        if self.busy() or not self.save(announce=False):
            return
        if not self.platform or not self.platform.permissions()["accessibility"]:
            self.status("Для печати предоставьте доступ в разделе «Система»")
            self.window.navigation.setCurrentRow(2)
            return
        if not self.app.clipboard().text():
            self.status("Буфер обмена пуст")
            return
        self.pending_typing_text = self.app.clipboard().text()
        self.typing_pending = True
        self.status("Выберите окно редактора · печать начнётся через 3 секунды")
        self.window.hide()
        self.preview.hide()
        self.typing_timer.start()

    def finish_typing_countdown(self):
        if not self.typing_pending:
            return
        self.typing_pending = False
        text, self.pending_typing_text = self.pending_typing_text, None
        self.start_typing(text)
        self.refresh()

    def type_clipboard(self):
        self.start_typing(self.app.clipboard().text())

    def start_typing(self, text):
        if self.busy():
            self.status("Сначала завершите или отмените текущую операцию")
            return
        if not self.platform:
            self.status("Печать доступна на macOS и Windows")
            return
        if self.platform.active_pid() == os.getpid():
            self.status("Поставьте курсор в целевой редактор и нажмите " + self.settings.typing_hotkey)
            return
        if not text:
            self.status("Буфер обмена пуст")
            return
        cancelled = threading.Event()
        self.typing_cancel = cancelled
        settings = replace(self.settings)
        target = active_target(self.platform)
        self.status("Печать…")
        from .typing import type_text

        def complete(message):
            if self.typing_cancel is cancelled:
                self.typing_cancel = None
                self.window.progress.hide()
                self.status(message)

        self.run_job(lambda progress: (type_text(self.platform, text, settings, cancelled,
            lambda done, total: progress("Печать…", done, total), expected_target=target), "Печать завершена")[1],
            complete, complete, self.model_progress)

    def cancel(self):
        self.capture_pending = False
        self.capture_timer.stop()
        self.typing_pending = False
        self.pending_typing_text = None
        self.typing_timer.stop()
        self.close_selection()
        if self.operation:
            operation, self.operation = self.operation, None
            operation.cancel()
        if self.model_cancel:
            self.model_cancel.set()
        if self.review_cancel:
            self.review_cancel.set()
        if self.diagnostics_cancel:
            self.diagnostics_cancel.set()
        if self.typing_cancel:
            self.typing_cancel.set()
        self.status("Остановка операции…")

    def quit(self):
        self.shutting_down = True
        self.preview.syntax_timer.stop()
        self.cancel()
        if self.platform:
            self.platform.stop_hotkeys()
        self.run_job(lambda progress: self.runtime.stop())
        # Wait asynchronously so a worker cannot spawn an orphan after shutdown.
        self.window.hide()
        self.preview.hide()
        self.diagnostics_window.hide()
        if self.setup_wizard:
            self.setup_wizard.hide()
        self.tray.hide()
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.setInterval(100)
        self.shutdown_timer.timeout.connect(self.finish_shutdown)
        self.shutdown_timer.start()

    def finish_shutdown(self):
        if self.pool.activeThreadCount() == 0:
            self.runtime.stop()
            self.review_runtime.stop()
            self.shutdown_timer.stop()
            for timer in self.findChildren(QTimer):
                timer.stop()
            # Destroy widgets while QApplication is still alive. A visible
            # diagnostic text document can otherwise outlive Qt at interpreter exit.
            self.window.deleteLater()
            self.preview.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.exit(getattr(self, "exit_code", 0))


def main():
    parser = argparse.ArgumentParser(description="ctrlCctrlV desktop app")
    parser.add_argument("--smoke", action="store_true", help="Open the UI briefly without capture, input or credentials")
    parser.add_argument("--smoke-credentials", action="store_true", help="Also verify a disposable system credential with --smoke")
    parser.add_argument("--smoke-model", choices=tuple(MODELS), help="Diagnostic: warm an installed model through GUI workers and exit")
    parser.add_argument("--acceleration", choices=("gpu", "cpu"), help="Select acceleration for --smoke-model")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    if args.smoke_credentials and not args.smoke:
        parser.error("--smoke-credentials требует --smoke")
    if args.acceleration and not args.smoke_model:
        parser.error("--acceleration требует --smoke-model")
    app = QApplication([sys.argv[0]])
    app.setApplicationName("ctrlCctrlV")
    app.setOrganizationName("ctrlCctrlV")
    app.setQuitOnLastWindowClosed(False)
    directory = args.data_dir or data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / "app.lock"))
    if not lock.tryLock(0):
        if args.smoke or args.smoke_model:
            diagnostic_print("Diagnostic run cannot use an already running instance")
            return 1
        (directory / "activate.request").touch()
        return 0
    controller = Controller(app, directory, native=not (args.smoke or args.smoke_model))
    if args.smoke or args.smoke_model:
        controller.show_settings()
    else:
        controller.show_startup()
    if args.smoke_model:
        if args.acceleration:
            controller.window.acceleration.setCurrentIndex(controller.window.acceleration.findData(args.acceleration))
        controller.window.local_model.setCurrentIndex(controller.window.local_model.findData(args.smoke_model))
        controller.start_model()
        monitor = QTimer(controller)
        monitor.setInterval(100)
        def completed():
            if controller.model_cancel is None:
                controller.exit_code = 0 if controller.runtime.ready else 1
                diagnostic_print(controller.window.status.text())
                monitor.stop()
                controller.quit()
        monitor.timeout.connect(completed)
        monitor.start()
    elif args.smoke:
        # Exercise a real background parser in the frozen app, without touching
        # screen capture, native input, credentials or the system clipboard.
        controller.preview.text.setPlainText("def smoke_test(:\n    pass\n")
        smoke_image = QImage(1200, 1600, QImage.Format.Format_RGB32)
        smoke_image.fill(0xffffffff)
        smoke_image.setDevicePixelRatio(2)
        controller.preview.source_image = smoke_image.copy()
        controller.preview.image_view.display(smoke_image)
        controller.preview.fit_image.setChecked(True)
        controller.preview.show()
        deadline = time.monotonic() + 12
        diagnostics_started = False
        network_result = []
        credential_result = []
        from .smoke import check_ocr_transport
        controller.run_job(lambda progress: check_ocr_transport(),
                           network_result.append, lambda message: network_result.append(False))
        if args.smoke_credentials:
            from .smoke import check_credentials
            controller.run_job(lambda progress: check_credentials(), credential_result.append,
                               lambda message: credential_result.append(False))
        monitor = QTimer(controller)
        monitor.setInterval(100)
        def parser_checked():
            nonlocal diagnostics_started
            hint = controller.preview.syntax_message.text()
            if hint.startswith("Возможная ошибка Python") and not diagnostics_started:
                diagnostics_started = True
                controller.show_diagnostics()
            report = controller.diagnostics_window.report.toPlainText()
            if (report and network_result and (not args.smoke_credentials or credential_result)) or time.monotonic() >= deadline:
                try:
                    data = json.loads(report)
                    passed = diagnostics_started and data["schema"] == 1 and "signature" in data["build"]
                    passed = passed and network_result == [True]
                    passed = passed and (not args.smoke_credentials or credential_result == [True])
                    size = controller.preview.image.pixmap().deviceIndependentSize()
                    viewport = controller.preview.image_view.viewport().size()
                    passed = passed and size.width() <= viewport.width() and size.height() <= viewport.height()
                    passed = passed and controller.preview.source_image.size() == smoke_image.size()
                    if getattr(sys, "frozen", False) and sys.platform == "darwin":
                        passed = passed and data["build"]["signature"] in ("adhoc", "certificate") and len(data["build"]["code_hash"] or "") == 40
                except (ValueError, KeyError, TypeError):
                    passed = False
                controller.exit_code = 0 if passed else 1
                diagnostic_print("GUI, background syntax and diagnostics checks passed" if passed else "GUI smoke check failed")
                diagnostic_print("OCR transport and cancellation checks passed" if network_result == [True] else "OCR transport check failed")
                if args.smoke_credentials:
                    diagnostic_print("Disposable credential check passed" if credential_result == [True] else "Credential check failed")
                monitor.stop()
                controller.quit()
        monitor.timeout.connect(parser_checked)
        monitor.start()
    return app.exec()
