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

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QLockFile, QObject, QRect, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QImage
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu, QSystemTrayIcon

from .catalog import MODELS
from .core import Operation, OperationError
from .ocr import OCRProvider, OCRResult
from .platforms import active_target, create_platform
from .runtime import LocalRuntime
from .settings import Credentials, Settings, data_directory
from .ui import DiagnosticsDialog, Preview, SelectionOverlay, SettingsWindow, SetupWizard
from .workspace_ui import AddModelDialog, OperationIndicator
from .custom_models import ModelRegistry
from .history import EntryRef, History
from .history_actions import HistoryUndo
from .ui_state import UiState
from .settings_controls import CAPTURE_ACTION_LABEL
from .capture_region import CaptureRegion, describe_screen, physical_rect, region_is_valid


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
    progress = Signal(object, int, int)
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
    action = Signal(str, int)


class ModelNotReady(OperationError):
    """A capture preflight that preparation of the same model can resolve."""


class Controller(QObject):
    def __init__(self, app, directory=None, platform_adapter=None, native=True):
        super().__init__()
        self.app = app
        self.directory = directory or data_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.first_run = not (self.directory / "settings.json").exists()
        self.setup_wizard = None
        self.registry = ModelRegistry(self.directory)
        self.models = self.registry.models
        self.settings = Settings.load(self.directory, self.models)
        self.history = History(self.directory)
        self.ui_state = UiState(self.directory)
        self.history_undo = HistoryUndo()
        self.history_restore_busy = False
        self.history_undo_timer = QTimer(self)
        self.history_undo_timer.setSingleShot(True)
        self.history_undo_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.history_undo_timer.setInterval(30_000)
        self.history_undo_timer.timeout.connect(self.expire_history_undo)
        self.catalog_loading = False
        self.model_retry = None
        self.add_model_dialog = None
        self.credentials = Credentials()
        self.runtime = LocalRuntime(self.directory, acceleration=self.settings.acceleration, models=self.models)
        self.review_runtime = LocalRuntime(self.directory, session="review", acceleration=self.settings.acceleration, models=self.models)
        self.review_cancel = None
        self.diagnostics_cancel = None
        self.platform = platform_adapter
        if native and self.platform is None:
            self.platform = create_platform()
        self.window = SettingsWindow(self.settings, self.models)
        self.connection_generation = 0
        self.window.key.textChanged.connect(self.invalidate_connection_read)
        self.window.change_key.clicked.connect(self.invalidate_connection_read)
        self.indicator = OperationIndicator()
        self.preview = Preview()
        self.window.set_ui_state(self.ui_state)
        self.preview.set_ui_state(self.ui_state)
        self.diagnostics_window = DiagnosticsDialog(self.window, ui_state=self.ui_state)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.jobs = {}
        self.operation = None
        self.ocr_worker_operation = None
        self.model_cancel = self.typing_cancel = None
        self.overlays = []
        self.capture_pending = False
        self.repeat_pending = False
        self.last_capture_region = None
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
        self.capture_preflight_recovery = None
        self.ocr_image = None
        self.ocr_elapsed_second = 0
        self.last_activity = time.monotonic()
        self.hotkeys = Hotkeys()
        self.hotkey_generation = 0
        self.hotkeys_suspended = False
        self.permission_snapshot = None
        self.hotkeys.action.connect(self.handle_hotkey)
        for field in (self.window.capture_hotkey, self.window.typing_hotkey, self.window.cancel_hotkey):
            field.recording_changed.connect(self.hotkey_recording_changed)
        self.window.save_requested.connect(self.save)
        self.window.capture_requested.connect(self.capture)
        self.window.repeat_capture_requested.connect(self.repeat_capture)
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
        self.window.managed_model.currentIndexChanged.connect(self.refresh)
        self.window.model_action_requested.connect(self.library_action)
        self.window.models_download_requested.connect(self.download_models)
        self.window.models_delete_requested.connect(self.delete_models)
        self.window.model_retry_requested.connect(self.retry_model_action)
        self.window.navigation.currentRowChanged.connect(self.clear_previous_page_status)
        self.window.catalog_requested.connect(self.refresh_catalog)
        self.window.add_model_requested.connect(self.show_add_model)
        self.window.history_page.copy_requested.connect(self.copy_history)
        self.window.history_page.type_requested.connect(lambda text: self.schedule_typing(text))
        self.window.history_page.delete_requested.connect(self.delete_history)
        self.window.history_page.search_requested.connect(self.refresh_history)
        self.window.history_page.open_requested.connect(self.open_history)
        self.window.history_page.save_requested.connect(self.save_history)
        self.window.history_page.pin_requested.connect(self.pin_history)
        self.window.history_page.undo_delete_requested.connect(self.undo_history_delete)
        self.refresh_history()
        from .remote_catalog import load_cache
        cached_models = load_cache(self.directory)
        self.remote_models = {model.id: model for model in cached_models}
        if cached_models:
            self.window.set_catalog(cached_models)
        def load_cloud_catalog(*_):
            if not self.remote_models and (self.window.provider.currentData() == "openrouter" or self.window.review_provider.currentData() == "openrouter"):
                self.refresh_catalog()
        if native:
            self.window.provider.currentIndexChanged.connect(load_cloud_catalog)
            self.window.review_provider.currentIndexChanged.connect(load_cloud_catalog)
        self.preview.copy_requested.connect(self.copy_edited)
        self.preview.dismissed.connect(self.preview_dismissed)
        self.preview.review_requested.connect(self.review_text)
        self.preview.review_cancel_requested.connect(self.cancel)
        self.preview.accept_review_requested.connect(self.accept_review)
        self.preview.review_invalidated.connect(self.review_edited)
        self.preview.syntax_requested.connect(self.check_syntax)
        self.preview.format_requested.connect(self.format_preview)
        self.preview.refine_model.install_requested.connect(self.show_model_install)
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
        if native:
            QTimer.singleShot(0, load_cloud_catalog)
            self.load_connection_state()

    def invalidate_connection_read(self, *_):
        self.connection_generation += 1

    def load_connection_state(self):
        generation = self.connection_generation
        def loaded(connected):
            if not self.shutting_down and generation == self.connection_generation:
                self.window.set_connection_state(bool(connected))
        self.run_job(lambda _: bool(self.credentials.read()), loaded, lambda _: loaded(False))

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
        self.tray_actions = {}
        for key, label, handler in (("capture", CAPTURE_ACTION_LABEL, self.capture),
                               ("repeat", "Повторить область", self.repeat_capture), ("type", "Напечатать буфер", self.type_clipboard),
                               ("cancel", "Остановить", self.cancel), ("settings", "Настройки", self.show_settings), ("quit", "Выход", self.quit)):
            if key == "quit":
                menu.addSeparator()
            action = QAction(label, menu)
            action.triggered.connect(handler)
            menu.addAction(action)
            self.tray_actions[key] = action
        self.tray_stop_action = self.tray_actions["cancel"]
        self.update_tray_actions()
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_settings() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def update_tray_actions(self):
        from .settings_controls import pretty_hotkey
        for key, label, binding in (("capture", CAPTURE_ACTION_LABEL, self.settings.capture_hotkey),
                                    ("type", "Напечатать буфер", self.settings.typing_hotkey),
                                    ("cancel", "Остановить", self.settings.cancel_hotkey)):
            self.tray_actions[key].setText(label + "\t" + pretty_hotkey(binding))
        self.tray_stop_action.setEnabled(self.busy())
        self.tray_actions['repeat'].setEnabled(self.last_capture_region is not None and not self.busy())

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
        self.window.set_repeat_available(self.last_capture_region is not None)
        self.tray_stop_action.setEnabled(self.busy())
        self.tray_actions['repeat'].setEnabled(self.last_capture_region is not None and not self.busy())
        self.preview.refine_model.setEnabled(not self.busy())
        self.preview.review_button.setEnabled(not self.busy() and self.preview.source_image is not None)
        if self.operation is not None and not self.operation.cancelled.is_set() and not self.capture_pending and not self.overlays:
            self.indicator.display(text, self.window.screen())
        else:
            self.indicator.hide()

    def clear_previous_page_status(self, *_):
        if not self.busy():
            self.window.status.clear()

    def retry_model_action(self):
        if self.busy() or self.model_retry is None:
            return
        action, values = self.model_retry
        if action == 'download':
            self.download_models(values)
        elif action == 'start':
            self.model_job(start=True, identifier=values[0])

    def model_failure(self, message, retry=None):
        self.model_retry = retry
        self.window.navigation.setCurrentRow(1)
        self.window.show_model_error(message)
        self.window.set_model_retry_available(retry is not None)
        self.window.status.clear()

    def refresh(self):
        activation = self.directory / "activate.request"
        if activation.exists():
            activation.unlink(missing_ok=True)
            QTimer.singleShot(0, self.reopen_window)
        self.window.set_busy(self.busy())
        self.window.set_repeat_available(self.last_capture_region is not None)
        self.tray_stop_action.setEnabled(self.busy())
        self.tray_actions['repeat'].setEnabled(self.last_capture_region is not None and not self.busy())
        if self.runtime.ready and (not self.runtime.process or self.runtime.process.poll() is not None):
            self.runtime.ready = False
            self.status("Локальный движок завершился; подготовьте модель заново")
        identifier = self.window.local_model.currentData()
        model = self.models[identifier]
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
        local_provider = self.window.provider.currentData() == "local"
        self.window.work_model_info.setText(model.label.split(" · ")[0] + (" · готова" if ready else " · на диске" if installed else " · не скачана") if local_provider else self.window.remote_model.currentText())
        self.window.work_model_info.setToolTip("На компьютере" if local_provider else "OpenRouter · " + self.window.remote_model.text())
        installed_models = {i for i in self.models if self.runtime.installed(i)}
        self.window.update_model_states(
            installed_models,
            {runtime.model_id for runtime in (self.runtime, self.review_runtime) if getattr(runtime, "ready", False)},
            engine_available, self.busy())
        self.preview.refine_model.set_installed(installed_models)
        self.window.local_info.setToolTip(f"Лицензия: {model.license}. Качество и скорость проверяйте на своих примерах.")
        self.window.install_button.setText("Восстановить движок" if installed and not engine_available else "Скачать модель")
        self.window.install_button.setVisible(local_provider and (not installed or not engine_available))
        self.window.prepare_button.setVisible(local_provider and installed and engine_available and not ready)
        self.window.install_button.setEnabled(not self.busy())
        self.window.prepare_button.setEnabled(not self.busy())
        self.window.provider_pages.update_current_size()
        if self.platform:
            values = self.platform.permissions()
            self.window.show_permissions(values)
            if values != self.permission_snapshot:
                self.permission_snapshot = values.copy()
                if values["input_monitoring"] and not self.hotkeys_registered and not self.hotkeys_suspended:
                    self.retry_hotkeys()
                elif not values["input_monitoring"] and self.hotkeys_registered:
                    self.platform.stop_hotkeys()
                    self.hotkeys_registered = False

    def register_hotkeys(self):
        if self.hotkeys_suspended:
            return
        self.hotkey_generation += 1
        generation = self.hotkey_generation
        self.bindings = {"capture": self.settings.capture_hotkey, "type": self.settings.typing_hotkey,
            "cancel": self.settings.cancel_hotkey}
        self.hotkeys_registered = bool(self.platform and self.platform.permissions()["input_monitoring"]
            and self.platform.start_hotkeys(lambda action: self.hotkeys.action.emit(action, generation), self.bindings))
        if not self.hotkeys_registered:
            self.status(getattr(self.platform, "last_hotkey_error", "") or
                        "Глобальные клавиши недоступны. Операции доступны из меню; проверьте разрешения.")

    def hotkey_recording_changed(self, recording):
        if recording:
            self.hotkeys_suspended = True
            self.hotkey_generation += 1
            if self.platform:
                self.platform.stop_hotkeys()
            self.hotkeys_registered = False
        else:
            QTimer.singleShot(0, self.resume_recorded_hotkeys)

    def resume_recorded_hotkeys(self):
        if self.shutting_down or any(field.recording for field in
                (self.window.capture_hotkey, self.window.typing_hotkey, self.window.cancel_hotkey)):
            return
        if self.hotkeys_suspended:
            self.hotkeys_suspended = False
            self.register_hotkeys()

    def handle_hotkey(self, action, generation=None):
        if self.hotkeys_suspended or (generation is not None and generation != self.hotkey_generation):
            return
        if any(getattr(field, "recording", False) for field in
               (self.window.capture_hotkey, self.window.typing_hotkey, self.window.cancel_hotkey)):
            return
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
        self.window.review_provider.setCurrentIndex(self.window.review_provider.findData(self.setup_wizard.provider.currentData()))
        self.window.acceleration.setCurrentIndex(self.window.acceleration.findData(self.setup_wizard.acceleration.currentData()))
        self.window.local_model.setCurrentIndex(self.window.local_model.findData(self.setup_wizard.local_model.currentData()))
        if self.setup_wizard.setup_action == "connect":
            self.window.key.setText(self.setup_wizard.key.text())
        if not self.save(announce=False):
            self.show_settings()
            return
        self.first_run = False
        self.show_settings()
        if self.setup_wizard.setup_action == "download":
            self.window.navigation.setCurrentRow(1)
            self.install()
        elif self.setup_wizard.setup_action == "connect":
            self.setup_wizard.key.clear()
            self.refresh_catalog()
            self.status("Ключ OpenRouter сохранён")

    def save(self, announce=True):
        try:
            updated = replace(self.settings)
            self.window.apply_to(updated)
            value = self.window.key.text().strip()
            updated.save(self.directory, self.models)
        except ValueError as error:
            self.status(str(error))
            return False
        except Exception:
            self.status("Не удалось записать настройки. Проверьте свободное место и права доступа.")
            return False
        self.settings = updated
        self.update_tray_actions()
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
                self.window.show_configuration("ocr")
                self.status("Настройки сохранены, но API-ключ не сохранён. Повторите попытку; введённый ключ оставлен в поле.")
                self.window.key.setFocus()
                return False
            self.window.key.clear()
            self.window.key.setPlaceholderText("API-ключ сохранён в системном хранилище; введите новый для замены")
            self.window.set_connection_state(True)
        self.window.mark_saved()
        if announce:
            self.status("Настройки и API-ключ сохранены" if value else "Настройки сохранены")
        return True

    def set_preview_option(self, enabled):
        updated = replace(self.settings, show_preview=enabled)
        try:
            updated.save(self.directory, self.models)
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

    def remote_provider(self, identifier, mode="json", max_tokens=None):
        key = self.credentials.read()
        if not key:
            raise OperationError("Введите и сохраните API-ключ OpenRouter в настройках")
        model = self.remote_models.get(identifier)
        if model is None:
            raise OperationError("Выберите vision-модель из обновлённого каталога OpenRouter")
        limit = max_tokens or self.settings.max_tokens
        if model.max_completion_tokens:
            limit = min(limit, model.max_completion_tokens)
        temperature = (model.default_temperature if model.default_temperature is not None else 0) if model.temperature else None
        return OCRProvider("https://openrouter.ai/api/v1/chat/completions", identifier, key,
                           limit, mode=mode, response_format=model.structured, temperature=temperature)

    def provider(self, source=None, identifier=None):
        source = source or self.settings.provider
        if source == "openrouter":
            return self.remote_provider(identifier or self.settings.remote_model, self.settings.remote_mode)
        identifier = identifier or self.settings.local_model
        runtime = next((r for r in (self.runtime, self.review_runtime) if r.ready and r.model_id == identifier
                        and (r.loaded_acceleration or r.acceleration) == self.settings.acceleration), None)
        if runtime is None:
            raise ModelNotReady("Подготовьте локальную модель")
        model = self.models[identifier]
        return OCRProvider(runtime.endpoint + "/v1/chat/completions", identifier,
                           runtime.key, self.settings.max_tokens, True, model.mode)

    def refresh_refine_models(self, selected=None):
        models = [("local", identifier, model.label.split(" · ")[0])
                  for identifier, model in self.models.items() if model.mode == "json"]
        models += [("openrouter", model.id, model.name, f"${model.input_price} / ${model.output_price}") for model in self.remote_models.values()]
        if selected is None and self.preview.refine_model.currentData() is None:
            selected = (self.settings.review_provider, self.settings.review_local_model
                        if self.settings.review_provider == "local" else self.settings.review_remote_model)
        self.preview.set_refine_models(models, selected)

    def capture(self):
        self.begin_capture()

    def repeat_capture(self):
        if self.last_capture_region is not None:
            self.begin_capture(repeat=True)

    def begin_capture(self, repeat=False):
        if self.busy():
            self.status("Сначала завершите или отмените текущую операцию")
            return
        if not self.preview.confirm_replace_result():
            return
        if not self.save(announce=False):
            return
        try:
            self.provider()  # Do not capture if OCR is not configured/ready.
            if self.platform and not self.platform.permissions()["screen"]:
                raise OperationError("Разрешите захват экрана в разделе «Система»")
        except Exception as error:
            self.capture_preflight_recovery = ((self.settings.local_model, self.settings.acceleration, str(error))
                                              if isinstance(error, ModelNotReady) else None)
            self.window.navigation.setCurrentRow(0)
            self.status(str(error))
            self.window.show_operation_error(str(error))
            self.window.status.clear()
            self.show_settings()
            return
        self.window.hide()
        self.preview.hide()
        self.capture_pending = True
        self.repeat_pending = repeat
        self.status("Повтор области…" if repeat else "Выделите область · Esc для отмены")
        self.capture_timer.start()

    def show_selection(self):
        if not self.capture_pending:
            return
        self.capture_pending = False
        if self.busy():
            return
        if self.repeat_pending:
            self.repeat_pending = False
            if self.show_repeat_selection():
                return
            self.last_capture_region = None
            self.status("Экран изменился · выделите область заново")
        try:
            screens = self.app.screens()
            topology = tuple(describe_screen(screen) for screen in screens)
            screenshots = [(screen, screen.grabWindow(0)) for screen in screens]
            current = tuple(describe_screen(screen) for screen in self.app.screens())
            if not screenshots or any(pixmap.isNull() for _, pixmap in screenshots):
                raise OperationError("Не удалось получить снимок экрана. Проверьте доступ к экрану и активный сеанс.")
            if (len({value.identity for value in topology}) != len(topology) or set(current) != set(topology)
                    or any(not self.snapshot_matches_screen(pixmap, descriptor)
                           for (_, pixmap), descriptor in zip(screenshots, topology))):
                self.last_capture_region = None
                raise OperationError("Экран изменился · выделите область заново")
        except (OperationError, RuntimeError, ValueError) as error:
            message = str(error) if isinstance(error, OperationError) else "Экран недоступен · повторите выделение области"
            self.window.navigation.setCurrentRow(0)
            self.status(message)
            self.window.show_operation_error(message)
            self.window.status.clear()
            self.show_settings()
            return
        for (screen, snapshot), descriptor in zip(screenshots, topology):
            overlay = SelectionOverlay(screen, snapshot, adjust=self.ui_state.get("capture_adjust"),
                                       show_help=not self.ui_state.get("capture_help_seen"))
            self.connect_selection(overlay, descriptor, topology)
            overlay.cancelled.connect(self.selection_cancelled)
            self.overlays.append(overlay)
        for overlay in self.overlays:
            overlay.show()
        self.overlays[0].activateWindow()
        self.ui_state.set(capture_help_seen=True)

    def connect_selection(self, overlay, screen, topology):
        overlay.selected.connect(lambda image: self.selected_region(image, overlay, screen, topology))

    @staticmethod
    def snapshot_matches_screen(snapshot, screen):
        if snapshot.isNull() or snapshot.devicePixelRatio() != screen.dpr:
            return False
        return snapshot.rect().contains(physical_rect(QRect(0, 0, *screen.geometry[2:]), screen.dpr))

    def selected_region(self, image, overlay, screen, topology):
        area = overlay.selection().intersected(overlay.rect())
        region = CaptureRegion(screen, area.getRect(), topology)
        current = tuple(describe_screen(value) for value in self.app.screens())
        if not region_is_valid(region, current) or not self.snapshot_matches_screen(overlay.snapshot, screen):
            self.close_selection()
            self.last_capture_region = None
            self.capture_pending = True
            self.status("Экран изменился · выделите область заново")
            self.capture_timer.start()
            return
        self.last_capture_region = region
        self.window.set_repeat_available(True)
        self.selected(image)

    def show_repeat_selection(self):
        screens = self.app.screens()
        topology = tuple(describe_screen(screen) for screen in screens)
        region = self.last_capture_region
        if region is None or not region_is_valid(region, topology):
            return False
        screen = next(screen for screen in screens if describe_screen(screen) == region.screen)
        snapshot = screen.grabWindow(0)
        if (snapshot.isNull() or snapshot.devicePixelRatio() != region.screen.dpr
                or not region_is_valid(region, tuple(describe_screen(value) for value in self.app.screens()))):
            return False
        area = QRect(*region.rect)
        physical = physical_rect(area, snapshot.devicePixelRatio())
        if not snapshot.rect().contains(physical):
            return False
        if self.ui_state.get('capture_adjust'):
            overlay = SelectionOverlay(screen, snapshot, adjust=True)
            overlay.origin, overlay.position = area.topLeft(), area.bottomRight()
            overlay._adjust_ready = True
            self.connect_selection(overlay, region.screen, topology)
            overlay.cancelled.connect(self.selection_cancelled)
            self.overlays.append(overlay)
            overlay.show()
            overlay.activateWindow()
        else:
            image = snapshot.copy(physical).toImage()
            image.setDevicePixelRatio(1)
            self.selected(image)
        return True

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

    def retry_failed_ocr(self):
        if (self.retry_image is not None and not self.busy()
                and self.preview.confirm_replace_result() and self.save(announce=False)):
            self.recognize(self.retry_image, Operation())

    def recognize(self, image, operation, provider=None):
        if self.busy():
            self.status("Дождитесь завершения текущей операции")
            return
        self.operation = operation
        self.ocr_image = image.copy()
        self.retry_image = None
        self.window.retry_button.hide()
        self.window.clear_operation_error()
        self.capture_preflight_recovery = None
        try:
            provider = provider or self.provider()
            capture_format = self.settings.capture_format
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
                from .table_ocr import recognition_prompt, table_result
                detailed = getattr(provider, 'recognize_result', None)
                # A legacy subclass may override recognize() to supply its transport.
                # Respect that override instead of silently bypassing its contract.
                legacy_override = (isinstance(provider, OCRProvider)
                    and type(provider).recognize is not OCRProvider.recognize
                    and type(provider).recognize_result is OCRProvider.recognize_result)
                if callable(detailed) and not legacy_override:
                    result = detailed(png, operation, prompt=recognition_prompt(capture_format))
                else:
                    result = OCRResult(provider.recognize(png, operation, prompt=recognition_prompt(capture_format)))
                operation.check()
                return OCRResult(table_result(result.text, capture_format), result.raw_text)
            self.ocr_worker_operation = operation
            self.run_job(task,
                lambda text: self.finish_ocr_job(operation, lambda: self.ocr_complete(operation, image, provider, text, capture_format)),
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

    def ocr_complete(self, operation, image, provider, text, capture_format="auto"):
        if self.operation is not operation:
            return
        raw_text = text.raw_text if isinstance(text, OCRResult) else None
        text = text.text if isinstance(text, OCRResult) else text
        try:
            elapsed = operation.publish(text, self.app.clipboard().setText)
            self.record_history("ocr", text, provider.model)
            self.operation = None
            self.status(f"Скопировано · {elapsed:.2f} с")
            if self.settings.show_preview:
                self.refresh_refine_models((self.settings.review_provider, self.settings.review_local_model
                    if self.settings.review_provider == "local" else self.settings.review_remote_model))
                self.preview.display(image, text, elapsed, provider.model, format_mode=capture_format)
                self.preview.set_raw_response(raw_text)
            else:
                self.preview.set_raw_response(None)
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
        self.capture_preflight_recovery = None
        self.window.retry_button.setVisible(self.retry_image is not None)
        self.status(message)
        self.window.show_operation_error(message)
        self.window.navigation.setCurrentRow(0)
        self.window.status.clear()
        self.window.show()

    def check_deadline(self):
        if self.operation:
            try:
                self.operation.check()
                elapsed_second = int(time.monotonic() - self.operation.started)
                if elapsed_second >= 4 and elapsed_second != self.ocr_elapsed_second:
                    self.ocr_elapsed_second = elapsed_second
                    self.status(f"Распознавание… {elapsed_second} с")
            except OperationError as error:
                self.ocr_error(self.operation, str(error))

    def preview_dismissed(self):
        if self.review_cancel:
            self.review_cancel.set()

    def review_edited(self):
        if self.review_cancel:
            self.review_cancel.set()
            self.preview.status.setText("Текст изменён · повторите проверку после завершения отмены")

    def review_text(self):
        if self.preview.source_image is None:
            return
        if self.busy() or self.preview.source_image is None or not self.save(announce=False):
            return
        self.preview.clear_review_error()
        self.preview.commit_table_edit()
        from .review import ReviewProvider, local_review_session
        settings = replace(self.settings)
        if not self.preview.refine_model.count():
            self.refresh_refine_models()
        choice = self.preview.refine_model.currentData()
        if not choice:
            self.preview.status.setText("Выберите модель уточнения")
            return
        settings.review_provider, identifier = choice
        if settings.review_provider == "local":
            model = self.models.get(identifier)
            if model is None or model.mode != "json":
                self.preview.status.setText("Выберите модель, поддерживающую изображения и промпт")
                return
            settings.review_local_model = identifier
        else:
            settings.review_remote_model = identifier
        image = self.preview.source_image.copy()
        original = self.preview.text.toPlainText()
        details = self.preview.review_context.toPlainText()
        review_options = {"format_mode": "csv"} if self.preview.table_candidate or self.preview.format_mode == "csv" else {}
        try:
            review_remote = self.remote_provider(settings.review_remote_model, max_tokens=settings.max_tokens) if settings.review_provider == "openrouter" else None
        except OperationError as error:
            self.preview.show_review_error(str(error))
            return
        if len(details) > 8192:
            self.preview.status.setText("Контекст проверки должен быть не длиннее 8192 символов")
            return
        if not original.strip():
            self.preview.status.setText("Нет текста для проверки")
            return
        if settings.review_provider == "local" and not self.review_runtime.installed(settings.review_local_model):
            self.preview.status.setText("Для проверки установите " + self.models[settings.review_local_model].label.split(" · ")[0] + " в разделе «Модели»")
            return
        cancel = self.review_cancel = threading.Event()
        self.preview.review_result = None
        self.preview.full_review_result = None
        self.preview.review_panel.hide()
        self.preview.set_review_busy(True)
        self.status("Уточнение текста…")
        self.preview.status.setText("Подготовка модели…")
        restore_errors = []
        def task(progress):
            started = time.monotonic()
            session = (local_review_session(self.runtime, self.review_runtime, settings.review_local_model,
                       settings.acceleration, cancel, progress, restore_errors, lambda: self.shutting_down)
                       if settings.review_provider == "local" else nullcontext(None))
            with session as runtime:
                if settings.review_provider == "local":
                    provider = OCRProvider(runtime.endpoint + "/v1/chat/completions",
                                           settings.review_local_model, runtime.key, settings.max_tokens, True)
                else:
                    provider = review_remote
                operation = Operation(budget=30, cancelled=cancel)
                progress("Сверка текста со снимком…", 0, 1)
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.save(buffer, "PNG"):
                    raise OperationError("Не удалось подготовить изображение")
                result = ReviewProvider(provider).inspect(bytes(buffer.data()), original, operation, details=details, **review_options)
            result = replace(result, elapsed=time.monotonic() - started)
            result.prepare()
            if cancel.is_set():
                raise OperationError("Проверка отменена")
            return result
        def complete(result):
            if self.review_cancel is not cancel:
                return
            self.review_cancel = None
            self.preview.set_review_busy(False)
            self.status("Проверка завершена" if not cancel.is_set() else "Проверка отменена")
            if not cancel.is_set():
                self.preview.propose(result)
                if restore_errors:
                    self.preview.show_review_error(restore_errors[0], "OCR не восстановлена")
            self.refresh()
        def failed(message):
            if self.review_cancel is not cancel:
                return
            self.review_cancel = None
            self.preview.set_review_busy(False)
            self.status("Проверка отменена" if cancel.is_set() else "Проверка не завершена")
            if self.preview.text.toPlainText() == original:
                if cancel.is_set():
                    self.preview.status.setText("Проверка отменена · исходный текст сохранён")
                else:
                    self.preview.show_review_error(message)
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
        self.record_history("edit", text)
        self.preview.set_raw_response(None)
        self.preview.hide()

    def check_syntax(self, text, language, revision):
        if self.shutting_down:
            return
        from .syntax import syntax_hint
        self.run_job(lambda progress: syntax_hint(text, language),
                     lambda hint: self.preview.show_syntax_hint(revision, hint),
                     lambda message: None)

    def format_preview(self, text, language, revision):
        if self.busy():
            return
        from .formatting import format_text
        self.preview.format_button.setEnabled(False)
        def complete(formatted):
            self.preview.format_button.setEnabled(True)
            self.preview.apply_format(formatted, revision)
        def failed(message):
            self.preview.format_button.setEnabled(True)
            if self.preview.syntax_revision == revision:
                self.preview.status.setText("Не удалось отформатировать: " + message)
        self.run_job(lambda progress: format_text(text, language), complete, failed)

    def record_history(self, kind, text, model="", status="completed"):
        try:
            self.history.add(kind, text, model, status)
            self.refresh_history()
        except Exception:
            # History storage failure must never suppress successful OCR/typing.
            self.window.history_page.show_history_error("Не удалось сохранить историю · проверьте место и доступ к профилю")

    def history_pins(self):
        return [EntryRef(**value) for value in self.ui_state.get('history_pins', [])]

    def write_history_pins(self, refs):
        values = [{'id': ref.id, 'created': ref.created} for ref in dict.fromkeys(refs)]
        if len(values) > 100:
            self.window.history_page.show_history_error("Можно закрепить до 100 записей")
            return False
        if not self.ui_state.set(history_pins=values) or self.ui_state.get('history_pins') != values:
            self.window.history_page.show_history_error("Не удалось сохранить закрепления")
            return False
        return True

    def refresh_history(self, *_):
        try:
            refs = self.history_pins()
            valid = [EntryRef(entry.id, entry.created) for entry in self.history.pinned_entries(refs)]
            entries = self.history.visible_entries(valid, self.window.history_page.search.text())
            self.window.history_page.clear_history_error()
            # Clean stale identities only after both database reads succeeded.
            if set(refs) != set(valid):
                self.write_history_pins(valid)
            self.window.history_page.display(entries, pinned=valid)
        except Exception:
            self.window.history_page.show_history_error("Не удалось прочитать историю")

    def copy_history(self, text):
        self.app.clipboard().setText(text)
        self.status("Запись истории скопирована")

    def open_history(self, identifier):
        if self.busy():
            return
        entry = self.history.get(identifier)
        if entry is None:
            self.refresh_history()
            return
        if not self.preview.confirm_replace_result():
            return
        self.last_image = None
        self.preview.display_history(entry.text, entry.model)

    def save_history(self, identifier):
        from xlsxwriter.exceptions import XlsxWriterException
        from .csv_editor import csv_rows
        from .text_export import FORMATS, save_text, suggest_extension
        from .table_export import export_table
        entry = self.history.get(identifier)
        if entry is None:
            self.refresh_history()
            return
        table = csv_rows(entry.text) is not None
        extension = "csv" if table else suggest_extension(entry.text)
        dialog = QFileDialog(self.window, "Сохранить в…")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setNameFilters(["CSV (*.csv)", "XLSX (*.xlsx)"] if table else
                              [f"{FORMATS[extension]} (*.{extension})", "Все файлы (*)"])
        dialog.setDefaultSuffix(extension)
        if table:
            dialog.filterSelected.connect(lambda value: dialog.setDefaultSuffix("xlsx" if "xlsx" in value else "csv"))
        self.preview.prepare_save_dialog(dialog, "Снимок", extension)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            destination = dialog.selectedFiles()[0]
            if table:
                kind = "xlsx" if "xlsx" in dialog.selectedNameFilter() else "csv"
                export_table(destination, entry.text, kind)
            else:
                save_text(destination, entry.text)
            self.preview.remember_save_directory(destination)
            self.status("Сохранено: " + Path(destination).name)
        except (OSError, ValueError, XlsxWriterException) as error:
            self.status("Не удалось сохранить: " + str(error))
        finally:
            dialog.deleteLater()

    def show_model_install(self, identifier):
        if isinstance(identifier, tuple):
            identifier = identifier[-1]
        self.show_settings()
        self.window.navigation.setCurrentRow(1)
        if identifier in self.window.model_rows:
            row = self.window.model_rows[identifier][0]
            self.window.model_table.setCurrentCell(row, 1)
            self.window.model_table.scrollToItem(self.window.model_table.item(row, 1))

    def delete_history(self, identifier):
        try:
            selected = self.window.history_page.selected_ref()
            expected = selected.created if selected and selected.id == identifier else None
            entry = self.history.delete(identifier, expected_created=expected)
            if entry is None:
                self.refresh_history()
                return
            ref = EntryRef(entry.id, entry.created)
            pinned = ref in self.history_pins()
            self.history_undo.remember(entry, pinned)
            self.history_undo_timer.start()
            self.refresh_history()
            self.window.history_page.set_undo_available(True)
            try:
                self.history.compact()
            except Exception:
                self.window.history_page.show_history_error("Запись удалена · не удалось уплотнить файл истории")
        except Exception:
            self.window.history_page.show_history_error("Не удалось удалить запись истории")

    def pin_history(self, identifier, pinned):
        try:
            entry = self.history.get(identifier)
            selected = self.window.history_page.selected_ref()
            if entry is None or (selected and selected.id == identifier and selected.created != entry.created):
                self.refresh_history()
                return
            ref = EntryRef(entry.id, entry.created)
            refs = [value for value in self.history_pins() if value != ref]
            if pinned:
                refs.append(ref)
            if self.write_history_pins(refs):
                self.refresh_history()
        except Exception:
            self.window.history_page.show_history_error("Не удалось изменить закрепление")

    def expire_history_undo(self):
        pending = self.history_undo.peek()
        self.window.history_page.set_undo_available(pending is not None)

    def undo_history_delete(self):
        pending = self.history_undo.peek()
        if pending is None or self.history_restore_busy:
            self.window.history_page.set_undo_available(pending is not None, self.history_restore_busy)
            return
        entry, pinned = pending
        self.history_restore_busy = True
        self.window.history_page.set_undo_available(True, busy=True)
        try:
            restored = self.history.restore(entry)
        except Exception:
            self.window.history_page.set_undo_available(self.history_undo.peek() is not None)
            self.window.history_page.show_history_error("Не удалось восстановить запись · повторите попытку")
            return
        else:
            self.history_undo.clear()
            self.history_undo_timer.stop()
            self.refresh_history()
            self.window.history_page.set_undo_available(False)
            if pinned:
                refs = self.history_pins() + [EntryRef(restored.id, restored.created)]
                if self.write_history_pins(refs):
                    self.refresh_history()
        finally:
            self.history_restore_busy = False

    def refresh_catalog(self):
        if self.catalog_loading:
            return
        from .remote_catalog import fetch_models, save_cache
        self.catalog_loading = True
        self.window.catalog_button.setEnabled(False)
        self.window.catalog_button.setText("Загрузка…")
        self.window.catalog_status.setText("Получение списка VLM…")
        def task(progress):
            models = fetch_models()
            save_cache(self.directory, models)
            return models
        def complete(models):
            self.catalog_loading = False
            self.window.catalog_button.setEnabled(True)
            self.window.catalog_button.setText("Обновить модели")
            self.window.set_catalog(models)
            self.remote_models = {model.id: model for model in models}
            self.refresh_refine_models()
        def failed(message):
            self.catalog_loading = False
            self.window.catalog_button.setEnabled(True)
            self.window.catalog_button.setText("Повторить")
            self.window.catalog_status.setText("Список недоступен · сохранённые модели остаются доступны")
            self.window.catalog_status.show()
        self.run_job(task, complete, failed)

    def show_add_model(self):
        if self.busy():
            return
        if not self.add_model_dialog:
            self.add_model_dialog = AddModelDialog(self.window)
            self.add_model_dialog.discover_requested.connect(self.discover_model)
            self.add_model_dialog.add_requested.connect(self.add_custom_model)
        self.add_model_dialog.show()
        self.add_model_dialog.raise_()

    def discover_model(self, link):
        from .custom_models import discover
        dialog = self.add_model_dialog
        dialog.add.setEnabled(False)
        dialog.discover.setEnabled(False)
        dialog.link.setEnabled(False)
        dialog.model_info = None
        dialog.status.setText("Поиск файлов модели…")
        def complete(info):
            dialog.model_info = info
            dialog.discover.setEnabled(True)
            dialog.link.setEnabled(True)
            dialog.display(info)
        def failed(message):
            dialog.discover.setEnabled(True)
            dialog.link.setEnabled(True)
            dialog.status.setText("Не удалось прочитать комплект. " + message if not message.startswith("http") else "Проверьте ссылку и соединение")
        self.run_job(lambda progress: discover(link), complete, failed)

    def add_custom_model(self, weights, projector, mode):
        from .custom_models import make_model
        dialog = self.add_model_dialog
        if not getattr(dialog, "model_info", None):
            return
        try:
            model = make_model(dialog.model_info, weights, projector, mode)
            self.registry.add(model)
            self.window.populate_models(model.id)
            self.window.update_pending()
            self.refresh()
            dialog.accept()
            self.status("Модель добавлена · выберите «Загрузить» в её строке")
        except Exception as error:
            dialog.status.setText(str(error))

    def unload_managed_model(self, identifier=None):
        if self.busy():
            return
        identifier = identifier or self.window.managed_model.currentData()
        def unload():
            for runtime in (self.runtime, self.review_runtime):
                if runtime.model_id == identifier:
                    runtime.stop()
        self.runtime_maintenance(unload, "Выгрузка модели…", "Модель выгружена")

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
        if isinstance(label, tuple):
            identifier, description = label
            if self.model_cancel is None or self.model_cancel.is_set():
                return
            if description == "__complete__":
                self.window.set_model_complete(identifier)
            else:
                self.window.set_model_progress(identifier, description, done, total)
            return
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
        self.indicator.set_progress(done, total)

    def library_action(self, identifier, action):
        if action == "show_install":
            self.show_model_install(identifier)
        elif action == "stop":
            self.unload_managed_model(identifier)
        else:
            self.model_job(start=action == "start", identifier=identifier)

    def download_models(self, identifiers):
        if self.busy() or not identifiers or not self.save(announce=False):
            return
        selected = tuple(dict.fromkeys(i for i in identifiers if i in self.models and
                                      (not self.runtime.installed(i) or self.runtime.binary is None)))
        if not selected:
            return
        self.model_retry = None
        self.window.clear_model_error()
        token = self.model_cancel = threading.Event()
        self.runtime.acceleration = self.settings.acceleration
        self.window.set_model_queue(selected)
        self.status("Загрузка моделей…")
        def task(progress):
            completed = 0
            try:
                for identifier in selected:
                    if token.is_set():
                        raise OperationError("Загрузка отменена")
                    self.runtime.install(identifier, token, lambda label, done, total, i=identifier: progress((i, label), done, total))
                    progress((identifier, "__complete__"), 1, 1)
                    completed += 1
            except Exception as error:
                raise OperationError(f"Загружено {completed}/{len(selected)} · {error}") from None
            return f"Загружено моделей: {completed}"
        def complete(message):
            if self.model_cancel is token:
                self.model_cancel = None
                self.window.clear_model_queue()
                self.window.progress.hide()
                self.status(message)
                self.refresh()
        def failed(message):
            if self.model_cancel is not token:
                return
            was_cancelled = token.is_set()
            complete(message)
            if not was_cancelled:
                self.model_failure(message, ('download', selected))
        self.run_job(task, complete, failed,
                     lambda label, done, total: self.model_progress(label, done, total)
                     if self.model_cancel is token and not token.is_set() else None)

    def delete_models(self, identifiers):
        if self.busy():
            return
        selected = tuple(dict.fromkeys(i for i in identifiers if i in self.models and self.runtime.installed(i)))
        if not selected:
            return
        token = self.model_cancel = threading.Event()
        self.status("Удаление моделей…")
        def task(progress):
            completed = 0
            try:
                for identifier in selected:
                    if token.is_set():
                        raise OperationError("Удаление отменено")
                    if self.review_runtime.model_id == identifier:
                        self.review_runtime.stop()
                    self.runtime.delete(identifier)
                    completed += 1
                    progress("Удаление моделей…", completed, len(selected))
            except Exception as error:
                raise OperationError(f"Удалено {completed}/{len(selected)} · {error}") from None
            return f"Удалено моделей: {completed}"
        def complete(message):
            if self.model_cancel is token:
                self.model_cancel = None
                self.window.progress.hide()
                self.status(message)
                self.refresh()
        self.run_job(task, complete, complete,
                     lambda label, done, total: self.model_progress(label, done, total)
                     if self.model_cancel is token and not token.is_set() else None)

    def model_job(self, start=False, identifier=None):
        if self.busy():
            self.status("Сначала завершите или отмените текущую операцию")
            return
        if not self.save(announce=False):
            return
        self.model_retry = None
        self.window.clear_model_error()
        cancelled = threading.Event()
        self.model_cancel = cancelled
        self.status("Подготовка модели…" if start else "Подготовка загрузки…")
        identifier = identifier or self.settings.local_model
        self.runtime.acceleration = self.settings.acceleration
        self.window.set_model_queue([identifier])

        def task(progress):
            report = lambda label, done, total: progress((identifier, label), done, total)
            if start:
                self.runtime.start(identifier, cancelled, report)
            else:
                self.runtime.install(identifier, cancelled, report)
            return "Модель готова к OCR" if start else "Модель установлена. Нажмите «Подготовить»."

        def complete(message, *, succeeded=True):
            if self.model_cancel is not cancelled:
                return
            self.model_cancel = None
            self.window.clear_model_queue()
            self.window.progress.hide()
            self.status(message)
            self.refresh()
            recovery = self.capture_preflight_recovery
            if (succeeded and start and not cancelled.is_set() and recovery is not None
                    and recovery[:2] == (identifier, self.runtime.loaded_acceleration)
                    and recovery[:2] == (self.settings.local_model, self.settings.acceleration)
                    and self.runtime.ready and self.runtime.model_id == identifier
                    and self.window.operation_error_details.toPlainText() == recovery[2]):
                self.window.clear_operation_error()
                self.capture_preflight_recovery = None
            if self.runtime.hardware:
                from .hardware import describe_hardware
                self.window.hardware_info.setText(describe_hardware(self.runtime.hardware, self.models[identifier]))
            self.last_activity = time.monotonic()

        def failed(message):
            if self.model_cancel is not cancelled:
                return
            was_cancelled = cancelled.is_set()
            complete(message, succeeded=False)
            if not was_cancelled:
                self.model_failure(message, ('start' if start else 'download', (identifier,)))
        self.run_job(task, complete, failed,
                     lambda label, done, total: self.model_progress(label, done, total)
                     if self.model_cancel is cancelled and not cancelled.is_set() else None)

    def install(self):
        self.model_job()

    def check_hardware(self):
        if self.busy() or not self.save(announce=False):
            return
        from .hardware import describe_hardware, inspect_hardware, require_capacity
        cancel = self.model_cancel = threading.Event()
        acceleration = self.settings.acceleration
        model = self.models[self.window.managed_model.currentData()]
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
                    self.window.hardware_info.show()
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

    def delete_model(self, identifier=None):
        if self.busy():
            self.status("Сначала завершите или отмените операцию")
            return
        identifier = identifier or self.window.local_model.currentData()
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

    def schedule_typing(self, text=None):
        if self.busy() or not self.save(announce=False):
            return
        if not self.platform or not self.platform.permissions()["accessibility"]:
            self.status("Для печати предоставьте доступ в разделе «Система»")
            self.window.show_configuration("system")
            return
        text = self.app.clipboard().text() if text is None else text
        if not text:
            self.status("Буфер обмена пуст")
            return
        self.pending_typing_text = text
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
        target = active_target(self.platform)
        if target is None:
            self.status("Не удалось определить целевое окно · выберите редактор и повторите команду печати")
            return
        cancelled = threading.Event()
        self.typing_cancel = cancelled
        settings = replace(self.settings)
        self.status("Печать…")
        from .typing import type_text

        def complete(message):
            if self.typing_cancel is cancelled:
                self.typing_cancel = None
                self.record_history("typing", text, status="cancelled" if cancelled.is_set() else "completed" if message == "Печать завершена" else "error")
                self.window.progress.hide()
                self.status(message)

        self.run_job(lambda progress: (type_text(self.platform, text, settings, cancelled,
            lambda done, total: progress("Печать…", done, total), expected_target=target), "Печать завершена")[1],
            complete, complete, self.model_progress)

    def cancel(self):
        self.capture_pending = False
        self.repeat_pending = False
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

    def quit(self, _checked=False, *, force=False):
        if self.shutting_down:
            return True
        if not force and not self.preview.confirm_replace_result():
            return False
        self.shutting_down = True
        self.history_undo.clear()
        self.history_undo_timer.stop()
        self.preview.set_raw_response(None)
        self.preview.syntax_timer.stop()
        self.cancel()
        if self.platform:
            self.platform.stop_hotkeys()
        self.run_job(lambda progress: self.runtime.stop())
        # Wait asynchronously so a worker cannot spawn an orphan after shutdown.
        self.window.hide()
        self.preview.hide()
        self.indicator.hide()
        if self.add_model_dialog:
            self.add_model_dialog.hide()
        self.diagnostics_window.hide()
        if self.setup_wizard:
            self.setup_wizard.hide()
        self.tray.hide()
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.setInterval(100)
        self.shutdown_timer.timeout.connect(self.finish_shutdown)
        self.shutdown_timer.start()
        return True

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
            self.indicator.deleteLater()
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
                controller.quit(force=True)
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
        formatting_result = []
        export_result = []
        credential_result = []
        from .smoke import check_ocr_transport, check_formatters, check_table_exports
        controller.run_job(lambda progress: check_table_exports(), export_result.append,
                           lambda message: export_result.append(False))
        controller.run_job(lambda progress: check_formatters(), formatting_result.append,
                           lambda message: formatting_result.append(False))
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
            if (report and network_result and formatting_result and export_result and (not args.smoke_credentials or credential_result)) or time.monotonic() >= deadline:
                try:
                    data = json.loads(report)
                    passed = diagnostics_started and data["schema"] == 1 and "signature" in data["build"]
                    passed = passed and network_result == [True]
                    passed = passed and formatting_result == [True]
                    passed = passed and export_result == [True]
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
                diagnostic_print("Python, SQL, Java and JSON formatter checks passed" if formatting_result == [True] else "Formatter checks failed")
                diagnostic_print("Text, CSV and XLSX export checks passed" if export_result == [True] else "File export checks failed")
                if args.smoke_credentials:
                    diagnostic_print("Disposable credential check passed" if credential_result == [True] else "Credential check failed")
                monitor.stop()
                controller.quit(force=True)
        monitor.timeout.connect(parser_checked)
        monitor.start()
    return app.exec()
