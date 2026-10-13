"""Daily workspace, model library, configuration and explicit text history."""
from datetime import datetime, timedelta
from PySide6.QtCore import QEvent, Qt, QSize, Signal, QTimer
import time
from pathlib import Path
import sys
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QProgressBar, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
                               QFrame, QCheckBox, QSpinBox, QDoubleSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QStyledItemDelegate,
                               QScrollArea, QSizePolicy, QMenu)
from .ui import BaseSettingsWindow, SystemTheme, CurrentPageStack, label, card, button, add_page, form, disclosure
from .fonts import prepare_fonts, monospace_font
from .catalog import MODELS
from .theme import fit_window_to_screen
from .workspace_painting import HistoryItemDelegate, navigation_icon
from .workspace_layouts import AdaptiveRow


from .model_picker import DETAIL_ROLE, ModelPicker
from .settings_controls import HotkeyEdit, SpeedPreset, pretty_hotkey, DelaySpinBox, CAPTURE_ACTION_LABEL


def workspace_row(*widgets, spacing=8):
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    container.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return container


def quiet_button(text, callback):
    control = button(text, callback)
    control.setProperty("role", "quiet")
    return control


class RuntimeScrollArea(QScrollArea):
    def sizeHint(self):
        hint = super().sizeHint()
        if self.widget() is not None:
            hint.setHeight(self.widget().sizeHint().height() + 2 * self.frameWidth())
        return hint

    def eventFilter(self, watched, event):
        handled = super().eventFilter(watched, event)
        if watched is self.widget() and event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Resize):
            self.updateGeometry()
        return handled


class ModelStatusDelegate(QStyledItemDelegate):
    """Keep accessible item data while the cell widget supplies its visible status."""
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.text = ""


def take_field(field):
    layout = field.parentWidget().layout()
    def containing(current):
        if isinstance(current, QFormLayout) and current.indexOf(field) >= 0:
            return current
        for index in range(current.count()):
            nested = current.itemAt(index).layout()
            if nested:
                found = containing(nested)
                if found:
                    return found
        return None
    layout = containing(layout) or layout
    if isinstance(layout, QFormLayout):
        row = layout.takeRow(field)
        if row.labelItem and row.labelItem.widget():
            row.labelItem.widget().deleteLater()
    elif layout:
        layout.removeWidget(field)
    field.setParent(None)
    return field


class HistoryPage(QWidget):
    copy_requested = Signal(str)
    type_requested = Signal(str)
    delete_requested = Signal(int)
    search_requested = Signal(str)
    open_requested = Signal(int)
    save_requested = Signal(int)
    pin_requested = Signal(int, bool)
    undo_delete_requested = Signal()

    def __init__(self):
        super().__init__()
        self._pins = set()
        self._history_error = ""
        self._undo_available = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(label("История", "title"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти в истории")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Поиск в истории")
        self.search.setToolTip("Поиск по всем записям. Показываются до 500 последних совпадений.")
        self.search.textChanged.connect(self.search_requested)
        layout.addWidget(self.search)
        split = self.split = QSplitter()
        split.setObjectName("historySplit")
        split.setHandleWidth(7)
        self.entries = QListWidget()
        self.entries.setObjectName("historyEntries")
        self.entries.setItemDelegate(HistoryItemDelegate(self.entries))
        self.entries.setAccessibleName("Записи истории")
        self.entries.setMinimumWidth(200)
        self.entries.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.entries.currentRowChanged.connect(self.select)
        self.entries.itemDoubleClicked.connect(lambda *_: self.request_entry(self.open_requested))
        split.addWidget(self.entries)
        self.text = QPlainTextEdit()
        self.text.setObjectName("historyText")
        self.text.setReadOnly(True)
        self.text.setFont(monospace_font())
        self.text.setPlaceholderText("Здесь появятся результаты OCR и печати")
        self.text.setAccessibleName("Текст записи истории")
        split.addWidget(self.text)
        split.setSizes([220, 420])
        layout.addWidget(split, 1)
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.addStretch()
        heading = self.empty_heading = label("Пока нет записей", "section")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(heading)
        self.empty_message = label("Распознанный текст появится здесь", "muted")
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_message)
        empty_layout.addStretch()
        layout.addWidget(self.empty, 1)
        self.empty.hide()
        self.metadata = label("", "muted")
        self.metadata.hide()
        layout.addWidget(self.metadata)
        actions = QHBoxLayout()
        self.copy = button("Копировать", lambda: self.copy_requested.emit(self.text.toPlainText()), True)
        self.type = button("Напечатать", lambda: self.type_requested.emit(self.text.toPlainText()))
        self.type.setToolTip("Печать начнётся через 3 секунды")
        self.delete = button("Удалить", self.remove_selected)
        self.open = button("Открыть", lambda: self.request_entry(self.open_requested))
        self.save = button("Сохранить…", lambda: self.request_entry(self.save_requested))
        self.delete.setProperty("role", "danger")
        self.pin = button("Закрепить", self.toggle_pin)
        self.pin.setAccessibleName("Закрепить запись истории")
        actions.addWidget(self.copy)
        actions.addWidget(self.type)
        actions.addWidget(self.open)
        actions.addWidget(self.save)
        actions.addWidget(self.pin)
        actions.addStretch()
        self.more = button("Ещё", lambda: self.more_menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft())))
        self.more_menu = QMenu(self.more)
        for text, callback in (("Открыть", lambda: self.request_entry(self.open_requested)),
                               ("Сохранить…", lambda: self.request_entry(self.save_requested)),
                               ("Напечатать", lambda: self.type_requested.emit(self.text.toPlainText())),
                               ("Закрепить / открепить", self.toggle_pin)):
            self.more_menu.addAction(text, callback)
        actions.addWidget(self.more)
        actions.addWidget(self.delete)
        layout.addLayout(actions)
        state = QHBoxLayout()
        self.history_status = label("", "muted")
        self.history_status.setMinimumWidth(0)
        self.history_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        state.addWidget(self.history_status, 1)
        self.undo = button("Отменить", self.undo_delete_requested)
        self.undo.setAccessibleName("Отменить удаление записи истории")
        self.undo.setEnabled(False)
        self.undo.setVisible(False)
        state.addWidget(self.undo)
        self.state_row = QWidget()
        self.state_row.setLayout(state)
        self.state_row.setFixedHeight(38)
        state.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.state_row)
        self.select()

    def selected_ref(self):
        from .history import EntryRef
        item = self.entries.currentItem()
        entry = item.data(Qt.ItemDataRole.UserRole) if item else None
        return EntryRef(entry.id, entry.created) if entry else None

    def set_pins(self, refs):
        self._pins = {(ref.id, ref.created) for ref in refs}

    def toggle_pin(self):
        ref = self.selected_ref()
        if ref:
            self.pin_requested.emit(ref.id, (ref.id, ref.created) not in self._pins)

    def set_undo_available(self, available, busy=False):
        self._undo_available = bool(available)
        self._update_history_status()
        self.undo.setVisible(available)
        self.undo.setEnabled(available and not busy)

    def show_history_error(self, message):
        self._history_error = message
        self._update_history_status()

    def clear_history_error(self):
        self._history_error = ""
        self._update_history_status()

    def _update_history_status(self):
        self.history_status.setText(self._history_error or ("Удалено" if self._undo_available else ""))
        self.history_status.setToolTip(self._history_error)

    def remove_selected(self):
        self.request_entry(self.delete_requested)

    def request_entry(self, signal):
        item = self.entries.currentItem()
        entry = item.data(Qt.ItemDataRole.UserRole) if item else None
        if entry:
            signal.emit(entry.id)

    def display(self, entries, pinned=None):
        if pinned is not None:
            self.set_pins(pinned)
        entries = list(entries)
        selected = self.entries.currentItem()
        selected_entry = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        identifier = (selected_entry.id, selected_entry.created) if selected_entry else None
        self.entries.clear()
        kinds = {"ocr": "OCR", "edit": "Правки OCR", "typing": "Печать"}
        previous_day = None
        today = datetime.now().astimezone().date()
        first_item = None
        pin_entries = [entry for entry in entries if (entry.id, entry.created) in self._pins]
        normal_entries = [entry for entry in entries if (entry.id, entry.created) not in self._pins]
        if pin_entries:
            heading = QListWidgetItem("Закреплённые")
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            heading.setSizeHint(QSize(200, 32))
            self.entries.addItem(heading)
        for entry in pin_entries + normal_entries:
            created = datetime.fromisoformat(entry.created).astimezone()
            day = created.date()
            pinned_entry = (entry.id, entry.created) in self._pins
            if not pinned_entry and day != previous_day:
                heading = QListWidgetItem("Сегодня" if day == today else "Вчера" if day == today - timedelta(days=1) else day.strftime("%d.%m.%Y"))
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                heading.setSizeHint(QSize(200, 32))
                self.entries.addItem(heading)
                previous_day = day
            date = created.strftime("%H:%M")
            sample = entry.text.replace("\n", " ↵ ").replace("\r", "")[:100]
            item = QListWidgetItem(f"{date} · {kinds.get(entry.kind, entry.kind)}\n{sample}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setSizeHint(QSize(200, 56))
            self.entries.addItem(item)
            if first_item is None:
                first_item = item
            if (entry.id, entry.created) == identifier:
                self.entries.setCurrentItem(item)
        if self.entries.currentRow() < 0 and first_item:
            self.entries.setCurrentItem(first_item)
        self.split.setVisible(bool(entries))
        self.empty.setVisible(not entries)
        self.empty_message.setText("По этому запросу ничего нет" if self.search.text() else "Распознанный текст появится здесь")
        self.empty_heading.setText("Ничего не найдено" if self.search.text() else "Пока нет записей")
        self.select()

    def select(self, *_):
        item = self.entries.currentItem()
        entry = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.text.setPlainText(entry.text if entry else "")
        self.metadata.setText((entry.model + " · " if entry.model else "") +
                              {"completed": "", "cancelled": "Ввод остановлен", "error": "Ошибка ввода"}.get(entry.status, entry.status)
                              if entry else "")
        self.metadata.setVisible(bool(entry and entry.status != "completed"))
        self.pin.setText("Открепить" if entry and (entry.id, entry.created) in self._pins else "Закрепить")
        for widget in (self.copy, self.type, self.delete, self.open, self.save, self.pin, self.more):
            widget.setEnabled(entry is not None)
            widget.setVisible(entry is not None)
        self._fit_actions()

    def _fit_actions(self):
        has_entry = bool(self.text.toPlainText())
        compact = self.width() < 740
        for widget in (self.type, self.open, self.save):
            widget.setVisible(has_entry and not compact)
        self.more.setVisible(has_entry and compact)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_actions()


class ElidedFileLabel(QLabel):
    """Readable file name with the exact value available for copying."""
    def __init__(self):
        super().__init__()
        self.full_text = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.copy_menu)

    def set_file(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.elide()

    def elide(self):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideMiddle, max(0, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.elide()

    def copy_menu(self, point):
        menu = QMenu(self)
        menu.addAction("Копировать имя файла", lambda: QApplication.clipboard().setText(self.full_text))
        menu.exec(self.mapToGlobal(point))


class AddModelDialog(SystemTheme, QDialog):
    discover_requested = Signal(str)
    add_requested = Signal(str, str, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Добавить локальную vision-модель")
        self.resize(700, 230)
        self.setMinimumWidth(420)
        self._discovered_repo = ""
        self.init_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(label("Модель по ссылке", "title"))
        self.link = QLineEdit()
        self.link.setPlaceholderText("https://huggingface.co/author/model-GGUF")
        self.link.setAccessibleName("Ссылка на модель Hugging Face")
        self.link.setToolTip("Публичный GGUF-комплект Hugging Face для llama.cpp. SHA256 и версия фиксируются до загрузки.")
        search_row = QHBoxLayout()
        search_row.addWidget(self.link, 1)
        self.discover = button("Найти файлы", lambda: self.discover_requested.emit(self.link.text()))
        search_row.addWidget(self.discover)
        layout.addLayout(search_row)
        self.file_fields = QWidget()
        fields_layout = QVBoxLayout(self.file_fields)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        self.bundle_summary = label("", "section")
        self.bundle_summary.hide()
        self.weights_summary = ElidedFileLabel()
        self.projector_summary = ElidedFileLabel()
        summary_form = form()
        summary_form.addRow("Модель", self.weights_summary)
        summary_form.addRow("Проектор", self.projector_summary)
        fields_layout.addLayout(summary_form)
        self.weights, self.projector = QComboBox(), QComboBox()
        self.mode = QComboBox()
        for combo in (self.weights, self.projector, self.mode):
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.mode.addItem("Vision · текстовые инструкции / JSON", "json")
        self.mode.addItem("Специализированная OCR · только изображение", "transcription")
        advanced = QWidget()
        advanced_fields = form()
        advanced_fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        advanced.setLayout(advanced_fields)
        advanced_fields.addRow("Файл модели", self.weights)
        advanced_fields.addRow("Файл для изображений", self.projector)
        advanced_fields.addRow("Протокол", self.mode)
        self.advanced_toggle = disclosure("Дополнительно", advanced)
        fields_layout.addWidget(self.advanced_toggle)
        advanced_scroll = self.advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setFrameShape(QFrame.Shape.NoFrame)
        advanced_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        advanced_scroll.setMaximumHeight(180)
        advanced_scroll.setWidget(advanced)
        advanced.hide()
        advanced_scroll.hide()
        self.advanced_toggle.toggled.connect(advanced_scroll.setVisible)
        self.advanced_toggle.toggled.connect(self.fit_height)
        fields_layout.addWidget(advanced_scroll)
        layout.addWidget(self.file_fields)
        self.file_fields.hide()
        self.status = label("", "muted")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addWidget(button("Закрыть", self.reject))
        actions.addStretch()
        self.add = button("Добавить", self.add_bundle, True)
        self.add.setEnabled(False)
        self.add.hide()
        self.link.textChanged.connect(self.reset_discovery)
        self.weights.currentTextChanged.connect(self.update_bundle_summary)
        self.projector.currentTextChanged.connect(self.update_bundle_summary)
        actions.addWidget(self.add)
        layout.addLayout(actions)

    def display(self, info):
        self._discovered_repo = info["repo"]
        self.file_fields.show()
        self.weights.clear()
        self.weights.addItems(info["weights"])
        self.projector.clear()
        self.projector.addItems(info["projectors"])
        if info["selected"] in info["weights"]:
            self.weights.setCurrentText(info["selected"])
        self.add.setEnabled(bool(self.weights.currentText() and self.projector.currentText()))
        self.add.show()
        self.mode.setCurrentIndex(self.mode.findData("transcription" if "lightonocr" in info["repo"].lower() else "json"))
        self.advanced_toggle.setChecked(False)
        self.update_bundle_summary()
        self.status.setText("Версия " + info["commit"][:12] + " · лицензия: " + str(info["license"]))
        self.fit_height()

    def reset_discovery(self, *_):
        self._discovered_repo = ""
        self.file_fields.hide()
        self.add.hide()
        self.add.setEnabled(False)
        self.advanced_toggle.setChecked(False)
        self.weights.clear()
        self.projector.clear()
        self.mode.setCurrentIndex(self.mode.findData("json"))
        self.bundle_summary.clear()
        self.status.clear()
        self.fit_height()

    def update_bundle_summary(self, *_):
        if self._discovered_repo:
            self.bundle_summary.setText(self._discovered_repo + "\n" + self.weights.currentText() + " + " + self.projector.currentText())
        self.weights_summary.set_file(self.weights.currentText())
        self.projector_summary.set_file(self.projector.currentText())
        self.add.setEnabled(bool(self._discovered_repo and self.weights.currentText() and self.projector.currentText()))

    def add_bundle(self):
        if self._discovered_repo and self.weights.currentText() and self.projector.currentText():
            self.add_requested.emit(self.weights.currentText(), self.projector.currentText(), self.mode.currentData())

    def fit_height(self, *_):
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())
        if self.isVisible():
            fit_window_to_screen(self)

    def showEvent(self, event):
        super().showEvent(event)
        fit_window_to_screen(self)


class OperationIndicator(SystemTheme, QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("ctrlCctrlV · Выполняется операция")
        self.init_theme()
        self.setFixedSize(208, 36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress, 1)
        self.elapsed = label("00:00", "muted")
        self.elapsed.setFont(monospace_font())
        self.elapsed.setFixedWidth(48)
        layout.addWidget(self.elapsed)
        self.started = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.update_elapsed)

    def update_elapsed(self):
        seconds = int(time.monotonic() - self.started)
        self.elapsed.setText(f"{seconds // 60:02d}:{seconds % 60:02d}")

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def display(self, text, screen=None):
        if not self.isVisible():
            self.started = time.monotonic()
            self.update_elapsed()
            self.timer.start()
            area = (screen or QApplication.primaryScreen()).availableGeometry()
            self.move(area.right() - self.width() - 20, area.bottom() - self.height() - 20)
            self.progress.setRange(0, 0)
            self.show()

    def set_progress(self, done, total):
        self.progress.setRange(0, 100 if total > 1 else 0)
        if total > 1:
            self.progress.setValue(round(done / total * 100))


class SettingsWindow(BaseSettingsWindow):
    model_action_requested = Signal(str, str)
    model_retry_requested = Signal()
    repeat_capture_requested = Signal()
    models_download_requested = Signal(object)
    models_delete_requested = Signal(object)
    catalog_requested = Signal()
    add_model_requested = Signal()
    managed_install_requested = Signal()
    managed_start_requested = Signal()
    managed_stop_requested = Signal()
    managed_delete_requested = Signal()

    def __init__(self, settings, models=None):
        QDialog.__init__(self)
        prepare_fonts()
        self.models = models if models is not None else MODELS
        self._model_queue = []
        self._model_progress = {}
        self._installed = set()
        self._ready = set()
        self._engine_available = False
        self._model_busy = False
        self._repeat_available = False
        self.ui_state = None
        self.setWindowTitle("ctrlCctrlV")
        self.setObjectName("workspaceWindow")
        self.resize(960, 660)
        self.setMinimumSize(640, 440)
        self.init_theme()
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        rail = QFrame()
        rail.setObjectName("workspaceRail")
        rail.setProperty("role", "sidebar")
        rail.setFixedWidth(160)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(16, 26, 16, 22)
        brand = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(QPixmap(str(Path(__file__).parent / "assets/app.png")).scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(icon)
        brand.addWidget(label("ctrlCctrlV", "brand"))
        brand.addStretch()
        rail_layout.addLayout(brand)
        rail_layout.addSpacing(24)
        self.navigation = QListWidget()
        self.navigation.setAccessibleName("Разделы приложения")
        self.navigation.setObjectName("workspaceNav")
        self.navigation.setIconSize(QSize(18, 18))
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for name in ("Работа", "Модели", "Настройки", "История"):
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(140, 44))
            self.navigation.addItem(item)
        self.update_navigation_icons()
        QApplication.instance().paletteChanged.connect(self.update_navigation_icons)
        rail_layout.addWidget(self.navigation, 1)
        self.nav_titles = []
        # Compatibility labels keep shortcut tooltips available without occupying the rail.
        self.action_hint = label("")
        self.typing_hint = label("")
        self.tray_hint = label("")
        for widget in (self.action_hint, self.typing_hint, self.tray_hint):
            widget.setParent(self)
            widget.hide()
        rail_layout.addWidget(label("v0.1", "muted"))
        root.addWidget(rail)
        main = QVBoxLayout()
        main.setContentsMargins(24, 24, 24, 20)
        main.setSpacing(16)
        self.pages = QStackedWidget()
        main.addWidget(self.pages, 1)
        root.addLayout(main, 1)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)

        # Daily actions live in their own cards, with shortcuts alongside the actions.
        work = QWidget()
        work.setMaximumWidth(760)
        wl = QVBoxLayout(work)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(12)
        wl.addWidget(label("Захват и ввод", "title"))
        capture_card, capture_layout = card()
        capture_card.setProperty("role", "workCard")
        capture_layout.setContentsMargins(20, 16, 20, 16)
        capture_layout.setSpacing(16)
        capture_heading = QVBoxLayout()
        capture_heading.setSpacing(0)
        capture_header = QHBoxLayout()
        self.work_capture_title = label("Распознавание", "workTitle")
        self.work_capture_title.setWordWrap(False)
        capture_header.addWidget(self.work_capture_title, 1)
        capture_header.addWidget(quiet_button("Настроить", lambda: self.show_configuration("ocr")))
        capture_heading.addLayout(capture_header)
        self.work_model_info = label("LightOnOCR-3 0.8B", "muted")
        self.work_model_info.setWordWrap(False)
        self.work_model_info.setMinimumWidth(0)
        self.work_model_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        capture_heading.addWidget(self.work_model_info)
        capture_layout.addLayout(capture_heading)
        self.capture_format = QComboBox()
        for name, value in (("Авто", "auto"), ("Текст", "text"), ("CSV", "csv")):
            self.capture_format.addItem(name, value)
        self.capture_format.setCurrentIndex(self.capture_format.findData(settings.capture_format))
        self.capture_format.setToolTip("Авто: выделенная таблица — CSV, остальное — текст")
        self.capture_format.setAccessibleName("Формат распознавания области")
        self.capture_format.setMinimumWidth(160)
        self.capture_format.setMaximumWidth(180)
        self.quick_preview = QCheckBox("Показывать результат")
        self.quick_preview.setChecked(settings.show_preview)
        self.work_format_row = AdaptiveRow(workspace_row(label("Результат"), self.capture_format), self.quick_preview, spacing=12)
        capture_layout.addWidget(self.work_format_row)
        self.capture_button = button(CAPTURE_ACTION_LABEL, self.capture_requested, True)
        self.capture_button.setProperty("workAction", True)
        self.repeat_capture_button = button("Повторить область", self.repeat_capture_requested)
        self.repeat_capture_button.setToolTip("Сделать новый снимок последней области и распознать её заново")
        self.repeat_capture_button.setEnabled(False)
        self.repeat_capture_button.hide()
        self.work_capture_shortcut = label(settings.capture_hotkey, "shortcut")
        self.work_capture_shortcut.setWordWrap(False)
        self.install_button = button("Установить модель", self.install_requested)
        self.prepare_button = button("Подготовить", self.start_requested)
        self.prepare_button.hide()
        self.work_actions_row = AdaptiveRow(
            workspace_row(self.capture_button, self.work_capture_shortcut, spacing=12),
            workspace_row(self.repeat_capture_button, self.install_button, self.prepare_button), spacing=12)
        capture_layout.addWidget(self.work_actions_row)
        self.operation_error = QWidget()
        error_layout = QVBoxLayout(self.operation_error)
        error_layout.setContentsMargins(0, 0, 0, 0)
        error_layout.setSpacing(8)
        self.operation_error_details = QPlainTextEdit()
        self.operation_error_details.setReadOnly(True)
        self.operation_error_details.setAccessibleName("Подробности ошибки распознавания")
        self.operation_error_details.setMinimumHeight(88)
        self.operation_error_details.setMaximumHeight(132)
        error_actions = QHBoxLayout()
        self.operation_error_toggle = disclosure("Ошибка распознавания · подробности", self.operation_error_details)
        error_actions.addWidget(self.operation_error_toggle)
        error_actions.addStretch()
        self.retry_button = button("Повторить OCR", self.retry_requested)
        self.retry_button.hide()
        error_actions.addWidget(self.retry_button)
        error_layout.addLayout(error_actions)
        error_layout.addWidget(self.operation_error_details)
        capture_layout.addWidget(self.operation_error)
        self.operation_error.hide()
        self.local_info = label("", "muted")
        self.local_info.setParent(self)
        self.local_info.hide()
        wl.addWidget(capture_card)
        typing_card, typing_layout = card()
        typing_card.setProperty("role", "workCard")
        typing_layout.setContentsMargins(20, 16, 20, 16)
        typing_layout.setSpacing(12)
        typing_header = QHBoxLayout()
        self.work_typing_title = label("Ввод из буфера", "workTitle")
        self.work_typing_title.setWordWrap(False)
        typing_header.addWidget(self.work_typing_title, 1)
        typing_header.addWidget(quiet_button("Настроить", lambda: self.show_configuration("typing")))
        typing_layout.addLayout(typing_header)
        self.work_typing_shortcut = label(settings.typing_hotkey, "shortcut")
        self.work_typing_shortcut.setWordWrap(False)
        self.typing_button = button("Напечатать буфер", self.typing_requested)
        self.typing_button.setProperty("workAction", True)
        self.typing_button.setToolTip("Переключитесь в редактор за 3 секунды. При смене окна ввод останавливается.")
        typing_layout.addWidget(AdaptiveRow(workspace_row(
            self.typing_button, self.work_typing_shortcut, spacing=12)))
        wl.addWidget(typing_card)
        wl.addStretch()
        add_page(self.pages, work)

        # Model library: a single selection, useful actions and two runtime options.
        models_page = QWidget()
        ml = QVBoxLayout(models_page)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(8)
        library_header = QHBoxLayout()
        library_header.addWidget(label("Модели", "title"))
        library_header.addStretch()
        self.add_model_button = button("Добавить по ссылке", self.add_model_requested)
        library_header.addWidget(self.add_model_button)
        ml.addLayout(library_header)
        toolbar = QHBoxLayout()
        self.download_all = button("Загрузить все", lambda: self.models_download_requested.emit(list(self.models)))
        self.download_selected = button("Загрузить", lambda: self.models_download_requested.emit(self.checked_models()), True)
        self.download_selected.setObjectName("downloadSelected")
        self.delete_selected = button("Удалить", lambda: self.models_delete_requested.emit(self.checked_models()))
        self.delete_selected.setProperty("role", "danger")
        self.download_all.setToolTip("Скачать файлы всех моделей в библиотеке")
        self.download_selected.setToolTip("Скачать файлы моделей, отмеченных галочками")
        for widget in (self.download_all, self.download_selected, self.delete_selected):
            toolbar.addWidget(widget)
        toolbar.addStretch()
        self.queue_cancel = button("Остановить", self.cancel_requested)
        self.queue_cancel.setToolTip("Остановить текущую загрузку и оставшуюся очередь")
        self.queue_cancel.hide()
        toolbar.addWidget(self.queue_cancel)
        ml.addLayout(toolbar)
        self.managed_model = QComboBox(self)
        self.managed_model.hide()
        self.model_table = QTableWidget(0, 5)
        self.model_table.setObjectName("modelLibrary")
        self.model_table.setStyleSheet("QTableWidget::item { padding: 0px 6px; }")
        self.model_table.setItemDelegateForColumn(3, ModelStatusDelegate(self.model_table))
        self.model_table.setAccessibleName("Библиотека локальных моделей")
        self.model_table.setHorizontalHeaderLabels(["", "Модель", "Размер", "Состояние", "Действия"])
        self.select_all_models = QCheckBox(self.model_table.horizontalHeader())
        self.select_all_models.setAccessibleName("Выбрать все модели")
        self.select_all_models.setToolTip("Выбрать все модели")
        self.select_all_models.setGeometry(8, 4, 24, 26)
        self.select_all_models.clicked.connect(lambda: self.toggle_all_models(0))
        self.model_table.verticalHeader().hide()
        self.model_table.setShowGrid(False)
        self.model_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.model_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.model_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.model_table.setMinimumHeight(144)
        self.model_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.model_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.model_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column, width in ((0, 34), (2, 66), (3, 164), (4, 194)):
            self.model_table.setColumnWidth(column, width)
        self.model_table.horizontalHeader().sectionClicked.connect(self.toggle_all_models)
        self.model_table.itemChanged.connect(self.update_model_selection)
        self.model_table.currentCellChanged.connect(self.select_managed_model)
        self.model_rows = {}
        self.model_status_widgets = {}
        ml.addWidget(self.model_table, 1)
        self.model_error = label("", "error")
        self.model_error.setMinimumWidth(0)
        self.model_error.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.model_error.hide()
        self.model_retry_button = button("Повторить", self.model_retry_requested)
        self.model_retry_button.hide()
        self.model_error_row = QHBoxLayout()
        self.model_error_row.addWidget(self.model_error, 1)
        self.model_error_row.addWidget(self.model_retry_button)
        ml.addLayout(self.model_error_row)
        runtime_card, runtime_layout = card()
        runtime_card.setObjectName("runtimeCard")
        runtime_layout.setContentsMargins(18, 12, 18, 12)
        runtime_layout.setSpacing(8)
        runtime_form = form()
        runtime_form.setVerticalSpacing(8)
        self.acceleration = QComboBox()
        self.acceleration.addItem("GPU", "gpu")
        self.acceleration.addItem("CPU", "cpu")
        self.acceleration.setCurrentIndex(self.acceleration.findData(settings.acceleration))
        self.acceleration.setToolTip("Metal на Mac; NVIDIA CUDA на Windows")
        self.acceleration.setMaximumWidth(240)
        runtime_form.addRow("Ускорение", self.acceleration)
        self.idle = QSpinBox()
        self.idle.setRange(1, 120)
        self.idle.setValue(settings.idle_minutes)
        self.idle.setSuffix(" мин")
        self.idle.setMaximumWidth(240)
        runtime_form.addRow("Выгружать через", self.idle)
        runtime_layout.addLayout(runtime_form)
        self.hardware_info = label("", "muted")
        self.hardware_info.hide()
        runtime_layout.addWidget(button("Проверить оборудование", self.hardware_requested), 0, Qt.AlignmentFlag.AlignLeft)
        runtime_layout.addWidget(self.hardware_info)
        runtime_scroll = self.runtime_scroll = RuntimeScrollArea()
        runtime_scroll.setWidgetResizable(True)
        runtime_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        runtime_scroll.setFrameShape(QFrame.Shape.NoFrame)
        runtime_scroll.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustToContents)
        runtime_scroll.setMinimumHeight(60)
        runtime_scroll.setWidget(runtime_card)
        self.runtime_toggle = disclosure("Вычисления", runtime_scroll)
        ml.addWidget(self.runtime_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        ml.addWidget(runtime_scroll)
        ml.addStretch()
        self.pages.addWidget(models_page)
        self.models_page = models_page

        configuration_page = QWidget()
        configuration_page.setMaximumWidth(760)
        cp = QVBoxLayout(configuration_page)
        cp.setContentsMargins(0, 0, 0, 0)
        cp.setSpacing(16)
        cp.addWidget(label("Настройки", "title"))
        self.configuration = QTabWidget()
        self.configuration.setObjectName("workspaceTabs")
        cp.addWidget(self.configuration, 1)
        ocr = QWidget()
        ol = QVBoxLayout(ocr)
        ol.setContentsMargins(0, 12, 0, 0)
        ol.setSpacing(16)
        self.provider_card, pc = card()
        pc.addWidget(label("Чтение снимка", "section"))
        self.provider = QComboBox()
        self.provider.addItem("На компьютере", "local")
        self.provider.addItem("OpenRouter", "openrouter")
        self.provider.setCurrentIndex(self.provider.findData(settings.provider))
        self.provider.setMaximumWidth(155)
        self.local_model = ModelPicker()
        self.remote_model = ModelPicker(settings.remote_model)
        self.provider_pages = CurrentPageStack()
        for field in (self.local_model, self.remote_model):
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addWidget(field)
            self.provider_pages.addWidget(container)
        provider_row = QHBoxLayout()
        provider_row.addWidget(self.provider)
        provider_row.addWidget(self.provider_pages, 1)
        pc.addLayout(provider_row)
        self.provider_pages.setCurrentIndex(self.provider.currentIndex())
        self.provider.currentIndexChanged.connect(self.provider_pages.setCurrentIndex)
        self.show_preview = QCheckBox("Показывать результат после распознавания")
        self.show_preview.setChecked(settings.show_preview)
        self.show_preview.toggled.connect(self.preview_option_changed.emit)
        self.quick_preview.toggled.connect(self.show_preview.setChecked)
        self.show_preview.toggled.connect(self.quick_preview.setChecked)
        pc.addWidget(self.show_preview)
        self.response_mode = QComboBox()
        self.response_mode.addItem("Vision / JSON", "json")
        self.response_mode.addItem("Только изображение", "transcription")
        self.response_mode.setCurrentIndex(self.response_mode.findData(settings.remote_mode))
        self.response_mode.setToolTip("Протокол ответа облачной модели")
        self.response_mode.setParent(self)
        self.response_mode.hide()
        ol.addWidget(self.provider_card)
        self.review_card, rc = card()
        rc.addWidget(label("Уточнение результата", "section"))
        self.review_provider = QComboBox()
        self.review_provider.addItem("На компьютере", "local")
        self.review_provider.addItem("OpenRouter", "openrouter")
        self.review_provider.setCurrentIndex(self.review_provider.findData(settings.review_provider))
        self.review_provider.setMaximumWidth(155)
        self.review_local_model = ModelPicker()
        self.review_remote_model = ModelPicker(settings.review_remote_model)
        self.review_pages = CurrentPageStack()
        for field in (self.review_local_model, self.review_remote_model):
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addWidget(field)
            self.review_pages.addWidget(container)
        review_row = QHBoxLayout()
        review_row.addWidget(self.review_provider)
        review_row.addWidget(self.review_pages, 1)
        rc.addLayout(review_row)
        self.review_provider.currentIndexChanged.connect(self.update_review_provider)
        ol.addWidget(self.review_card)
        self.connection_card, connection = card()
        connection_header = QHBoxLayout()
        connection_header.addWidget(label("OpenRouter", "section"))
        connection_header.addStretch()
        self.catalog_button = button("Обновить модели", self.catalog_requested)
        connection_header.addWidget(self.catalog_button)
        connection.addLayout(connection_header)
        connection_state = QHBoxLayout()
        self.connection_state = label("API-ключ не добавлен", "muted")
        connection_state.addWidget(self.connection_state, 1)
        self.change_key = button("Изменить ключ", self.edit_connection_key)
        self.change_key.hide()
        connection_state.addWidget(self.change_key)
        connection.addLayout(connection_state)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API-ключ")
        self.key.setAccessibleName("Новый API-ключ OpenRouter")
        self.key.setToolTip("Хранится в системном хранилище. Пустое поле сохраняет прежний ключ.")
        connection.addWidget(self.key)
        self.show_key = QCheckBox("Показать ключ")
        self.show_key.toggled.connect(lambda shown: self.key.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password))
        self.key.textChanged.connect(lambda text: self.show_key.setChecked(False) if not text else None)
        connection.addWidget(self.show_key)
        self.catalog_status = label("", "muted")
        self.catalog_status.hide()
        connection.addWidget(self.catalog_status)
        ol.insertWidget(0, self.connection_card)
        ol.addStretch()
        add_page(self.configuration, ocr)
        self.configuration.setTabText(0, "Распознавание")

        typing = QWidget()
        tl = QVBoxLayout(typing)
        tl.setContentsMargins(0, 12, 0, 0)
        timing_card, timing = card()
        timing.addWidget(label("Печать", "section"))
        fields = form()
        self.delay, self.newline = DelaySpinBox(), DelaySpinBox()
        self.delay.setDecimals(6)
        self.newline.setDecimals(6)
        self.delay.setRange(1, 200)
        self.delay.setValue(settings.character_delay * 1000)
        self.delay.setSuffix(" мс")
        self.newline.setRange(0, 2000)
        self.newline.setValue(settings.newline_delay * 1000)
        self.newline.setSuffix(" мс")
        self.delay.setMaximumWidth(132)
        self.newline.setMaximumWidth(132)
        fields.addRow("Между символами", self.delay)
        fields.addRow("Между строками", self.newline)
        self.tabs = QSpinBox()
        self.tabs.setRange(0, 8)
        self.tabs.setSpecialValueText("Оставить Tab")
        self.tabs.setValue(settings.spaces_per_tab)
        fields.addRow("Пробелов вместо Tab", self.tabs)
        self.speed_preset = SpeedPreset(self.delay, self.newline)
        self.speed_preset.setMaximumWidth(260)
        timing.addWidget(self.speed_preset)
        timing_advanced = QWidget()
        timing_advanced_layout = QVBoxLayout(timing_advanced)
        timing_advanced_layout.setContentsMargins(0, 0, 0, 0)
        timing_advanced_layout.addLayout(fields)
        self.autoindent = QCheckBox("В редакторе включены автоотступы")
        self.autoindent.setChecked(settings.autoindent)
        self.autoindent.setToolTip("Для редактора с автоотступами. Автозакрытие скобок и кавычек отключается в самом редакторе.")
        timing_advanced_layout.addWidget(self.autoindent)
        self.timing_toggle = disclosure("Дополнительно", timing_advanced)
        self.speed_preset.currentIndexChanged.connect(lambda *_: self.timing_toggle.setChecked(True) if self.speed_preset.currentData() is None else None)
        timing.addWidget(self.timing_toggle)
        timing.addWidget(timing_advanced)
        tl.addWidget(timing_card)
        tl.addStretch()
        add_page(self.configuration, typing)
        self.configuration.setTabText(1, "Ввод текста")

        system = QWidget()
        sl = QVBoxLayout(system)
        sl.setContentsMargins(0, 12, 0, 0)
        sl.setSpacing(16)
        key_card, key_layout = card()
        key_layout.addWidget(label("Горячие клавиши", "section"))
        key_form = form()
        self.capture_hotkey = HotkeyEdit(settings.capture_hotkey)
        self.typing_hotkey = HotkeyEdit(settings.typing_hotkey)
        self.cancel_hotkey = HotkeyEdit(settings.cancel_hotkey)
        self.hotkey_record_buttons = []
        for title, field in (("Захват", self.capture_hotkey), ("Печать", self.typing_hotkey), ("Остановка", self.cancel_hotkey)):
            hotkey_row = QHBoxLayout()
            hotkey_row.setSpacing(8)
            field.setAccessibleName(title + " · горячие клавиши")
            hotkey_row.addWidget(field, 1)
            record = button("Записать", lambda _=False, current=field: current.finish_recording() if current.recording else current.start_recording())
            record.setFixedWidth(100)
            record.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self.hotkey_record_buttons.append(record)
            field.recording_changed.connect(lambda recording, control=record: control.setText("Отмена" if recording else "Записать"))
            record.setAccessibleName("Записать сочетание · " + title)
            hotkey_row.addWidget(record)
            key_form.addRow(title, hotkey_row)
        key_layout.addLayout(key_form)
        sl.addWidget(key_card)
        access_card, access = card()
        self.permission_labels, self.permission_buttons = {}, {}
        for key in ("accessibility", "screen", "input_monitoring"):
            row = QHBoxLayout()
            self.permission_labels[key] = label("")
            action = button("Разрешить", lambda checked=False, key=key: self.permission_settings_requested.emit(key))
            self.permission_buttons[key] = action
            row.addWidget(self.permission_labels[key], 1)
            row.addWidget(action)
            access.addLayout(row)
        self.permissions_toggle = disclosure("Доступ к системе", access_card)
        sl.addWidget(self.permissions_toggle)
        sl.addWidget(access_card)
        self.capture_adjust = QCheckBox("Уточнять область перед распознаванием")
        self.capture_adjust.setToolTip("Переместите или измените область, затем нажмите Enter. Esc — отмена.")
        self.capture_adjust.toggled.connect(lambda checked: self.ui_state.set(capture_adjust=checked) if self.ui_state is not None else None)
        sl.addWidget(self.capture_adjust)
        self.diagnostics_button = button("Диагностика", self.diagnostics_requested)
        sl.addWidget(self.diagnostics_button, 0, Qt.AlignmentFlag.AlignLeft)
        sl.addStretch()
        add_page(self.configuration, system)
        self.configuration.setTabText(2, "Система")
        self.pages.addWidget(configuration_page)
        self.history_page = HistoryPage()
        self.pages.addWidget(self.history_page)

        footer = QHBoxLayout()
        self.status = label("", "muted")
        self.status.setAccessibleName("Состояние операции")
        footer.addWidget(self.status, 1)
        self.cancel_button = button("Остановить", self.cancel_requested)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        self.save_hint = label("", "muted")
        footer.addWidget(self.save_hint)
        self.save_button = button("Сохранить", self.save_requested, True)
        footer.addWidget(self.save_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.hide()
        main.addWidget(self.progress)
        main.addLayout(footer)
        self._settings_fields = (self.provider, self.review_provider, self.capture_format,
                                 self.local_model, self.acceleration, self.remote_model, self.response_mode,
                                 self.review_local_model, self.review_remote_model, self.capture_hotkey,
                                 self.typing_hotkey, self.cancel_hotkey, self.delay, self.newline,
                                 self.tabs, self.autoindent, self.idle)
        self._saved_values = None
        self.populate_models(settings.local_model)
        self.local_model.setCurrentIndex(max(0, self.local_model.findData(settings.local_model)))
        self.review_local_model.setCurrentIndex(max(0, self.review_local_model.findData(settings.review_local_model)))
        for field in self._settings_fields:
            signal = (field.currentIndexChanged if isinstance(field, QComboBox) else
                      field.textChanged if isinstance(field, QLineEdit) else
                      field.toggled if isinstance(field, QCheckBox) else field.valueChanged)
            signal.connect(self.update_pending)
        self.remote_model.currentTextChanged.connect(self.update_pending)
        self.review_remote_model.currentTextChanged.connect(self.update_pending)
        self.key.textChanged.connect(self.update_pending)
        self.provider.currentIndexChanged.connect(self.update_review_provider)
        self.show_preview.toggled.connect(self.update_review_provider)
        for field in (self.capture_hotkey, self.typing_hotkey, self.cancel_hotkey):
            field.textChanged.connect(self.update_shortcut_hints)
        self.navigation.currentRowChanged.connect(self.update_actions)
        self.update_review_provider()
        self.navigation.setCurrentRow(0)
        self.mark_saved()
        self.update_shortcut_hints()
        for picker in (self.local_model, self.review_local_model):
            picker.install_requested.connect(lambda identifier: self.model_action_requested.emit(identifier, "show_install"))

    def update_navigation_icons(self, *_):
        for index, name in enumerate(("work", "models", "settings", "history")):
            self.navigation.item(index).setIcon(navigation_icon(name))

    def set_ui_state(self, state):
        self.ui_state = state
        for picker in (self.local_model, self.review_local_model, self.remote_model, self.review_remote_model):
            picker.set_ui_state(state)
        self.capture_adjust.blockSignals(True)
        self.capture_adjust.setChecked(bool(state.get("capture_adjust", False)))
        self.capture_adjust.blockSignals(False)

    def populate_models(self, selected=None):
        for combo, review in ((self.local_model, False), (self.review_local_model, True), (self.managed_model, False)):
            current = selected if combo is self.managed_model and selected else combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for identifier, model in self.models.items():
                if not review or model.mode == "json":
                    size = sum(file.size for file in model.files) / 1e9
                    combo.addItem(model.label.split(' · ')[0], identifier)
                    combo.setItemData(combo.count() - 1, {"source": "local", "size": f"{size:.1f} ГБ"}, DETAIL_ROLE)
                    combo.setItemData(combo.count() - 1, model.label, Qt.ItemDataRole.ToolTipRole)
            combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.blockSignals(False)
        self.rebuild_model_table()

    def checked_models(self):
        return [identifier for identifier, (row, _) in self.model_rows.items()
                if self.model_table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def toggle_all_models(self, column):
        if column == 0:
            state = Qt.CheckState.Unchecked if len(self.checked_models()) == len(self.model_rows) else Qt.CheckState.Checked
            for row, _ in self.model_rows.values():
                self.model_table.item(row, 0).setCheckState(state)

    def select_managed_model(self, row, *_):
        if row >= 0 and self.model_table.item(row, 1):
            self.managed_model.setCurrentIndex(self.managed_model.findData(self.model_table.item(row, 1).data(Qt.ItemDataRole.UserRole)))

    def update_model_selection(self, *_):
        checked = self.checked_models()
        self.select_all_models.blockSignals(True)
        self.select_all_models.setChecked(bool(checked) and len(checked) == len(self.model_rows))
        self.select_all_models.blockSignals(False)
        missing = sum(file.size for identifier in checked if identifier not in self._installed for file in self.models[identifier].files)
        self.download_selected.setText(f"Загрузить ({len(checked)})")
        removable = sum(identifier in self._installed for identifier in checked)
        self.delete_selected.setText(f"Удалить ({removable})")
        all_missing = [identifier for identifier in self.models if identifier not in self._installed]
        self.download_all.setText(f"Загрузить все ({len(all_missing)})")
        self.download_all.setToolTip(f"Скачать все недостающие модели: {sum(f.size for identifier in all_missing for f in self.models[identifier].files) / 1e9:.2f} ГБ")
        self.download_selected.setToolTip(f"Выбрано моделей: {len(checked)}. Не загружено: {missing} байт ({missing / 1e9:.2f} ГБ).")
        self.download_selected.setEnabled(bool(checked) and not self._model_busy)
        self.delete_selected.setEnabled(not self._model_busy and any(i in self._installed for i in checked))

    def rebuild_model_table(self):
        checked = set(self.checked_models())
        self.model_table.blockSignals(True)
        self.model_table.setRowCount(0)
        self.model_rows.clear()
        self.model_status_widgets.clear()
        for identifier, model in self.models.items():
            row = self.model_table.rowCount()
            self.model_table.insertRow(row)
            self.model_table.setRowHeight(row, 52)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if identifier in checked else Qt.CheckState.Unchecked)
            self.model_table.setItem(row, 0, check)
            name = QTableWidgetItem(model.label.split(" · ")[0])
            name.setData(Qt.ItemDataRole.UserRole, identifier)
            name.setToolTip(model.label)
            self.model_table.setItem(row, 1, name)
            self.model_table.setWordWrap(True)
            size = QTableWidgetItem(f"{sum(file.size for file in model.files) / 1e9:.1f} ГБ")
            size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.model_table.setItem(row, 2, size)
            self.model_table.setItem(row, 3, QTableWidgetItem("Не загружена"))
            status_panel = QWidget()
            status_layout = QVBoxLayout(status_panel)
            status_layout.setContentsMargins(6, 3, 6, 3)
            status_layout.setSpacing(2)
            status_label = label("Не скачана", "muted")
            status_label.setAccessibleName(model.label.split(" · ")[0] + " · состояние")
            progress = QProgressBar()
            progress.setTextVisible(False)
            progress.setRange(0, 100)
            progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            progress.setAccessibleName(model.label.split(" · ")[0] + " · загрузка")
            volume = label("", "downloadVolume")
            status_layout.addWidget(status_label)
            status_layout.addWidget(progress)
            status_layout.addWidget(volume)
            progress.hide()
            volume.hide()
            self.model_table.setCellWidget(row, 3, status_panel)
            self.model_status_widgets[identifier] = (status_label, progress, volume)
            panel = QWidget()
            actions = QHBoxLayout(panel)
            actions.setContentsMargins(4, 6, 8, 6)
            actions.setSpacing(6)
            load = button("Загрузить", lambda _=False, i=identifier: self.model_action_requested.emit(i, self.model_rows[i][1][0].property("action")))
            delete = button("Удалить", lambda _=False, i=identifier: self.models_delete_requested.emit([i]))
            load.setFixedWidth(100)
            delete.setFixedWidth(62)
            delete.setProperty("role", "danger")
            delete.hide()
            load.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            actions.addWidget(load)
            actions.addWidget(delete)
            actions.addStretch()
            self.model_table.setCellWidget(row, 4, panel)
            self.model_rows[identifier] = (row, (load, delete))
        self.model_table.blockSignals(False)
        self.model_table.setMaximumHeight(self.model_table.horizontalHeader().sizeHint().height() +
                                         52 * len(self.models) + 2 * self.model_table.frameWidth())
        self.update_model_selection()

    def update_model_states(self, installed, ready, engine_available, busy):
        self._installed, self._ready = set(installed), set(ready)
        self._engine_available, self._model_busy = engine_available, busy
        for picker in (self.local_model, self.review_local_model):
            picker.set_installed(installed)
        for identifier, (row, (load, delete)) in self.model_rows.items():
            present, loaded = identifier in installed, identifier in ready
            state = self.model_table.item(row, 3)
            status_label, progress, volume = self.model_status_widgets[identifier]
            progress.hide()
            volume.hide()
            state.setText("В памяти" if loaded else "На диске" if present else "Не скачана")
            if identifier in self._model_progress:
                text, done, total = self._model_progress[identifier]
                percent = max(0, min(100, round(done / total * 100))) if total > 1 else 0
                state.setText(f"{percent}%" if total > 1 else "Подготовка…")
                state.setToolTip(text + (f" · {done} / {total}" if total > 0 else ""))
                progress.setRange(0, 100 if total > 1 else 0)
                progress.setValue(percent)
                progress.show()
                if total > 1:
                    divisor, unit = (1e9, "ГБ") if total >= 1e9 else (1e6, "МБ")
                    digits = 2 if divisor == 1e9 else 0
                    volume.setText(f"{done / divisor:.{digits}f} / {total / divisor:.{digits}f} {unit}")
                    volume.show()
                elif done:
                    divisor, unit = (1e9, "ГБ") if done >= 1e9 else (1e6, "МБ")
                    volume.setText(f"{done / divisor:.{2 if divisor == 1e9 else 0}f} {unit}")
                    volume.show()
            elif identifier in self._model_queue:
                state.setText("В очереди")
                state.setToolTip("Ожидает загрузки")
            else:
                state.setToolTip("")
            status_label.setText(state.text())
            for widget in (status_label, progress, volume):
                widget.setToolTip(state.toolTip())
            state.setData(Qt.ItemDataRole.UserRole, present)
            action = "stop" if loaded else "start" if present and engine_available else "install"
            load.setProperty("action", action)
            load.setText({"stop": "Выгрузить", "start": "Подготовить", "install": "Загрузить"}[action])
            load.setToolTip("Запустить модель в памяти" if action == "start" else "Восстановить движок" if present and action == "install" else "")
            load.setEnabled(not busy)
            delete.setEnabled(present and not busy)
            delete.setVisible(present)
        self.download_all.setEnabled(not busy and (len(installed) < len(self.models) or not engine_available))
        self.update_model_selection()

    def set_model_queue(self, identifiers):
        self._model_queue = list(identifiers)
        self._model_progress.clear()
        self._refresh_model_progress()

    def set_model_progress(self, identifier, label, done, total):
        self._model_progress[identifier] = (label, max(0, done), total)
        self._refresh_model_progress()

    def clear_model_queue(self):
        self._model_queue.clear()
        self._model_progress.clear()
        self._refresh_model_progress()

    def set_model_complete(self, identifier):
        self._model_queue = [item for item in self._model_queue if item != identifier]
        self._model_progress.pop(identifier, None)
        if identifier in self.models:
            self._installed.add(identifier)
        self._refresh_model_progress()

    def show_operation_error(self, message):
        self.operation_error_details.setPlainText(message)
        self.operation_error_toggle.setChecked(False)
        self.operation_error.show()

    def show_model_error(self, message):
        self.model_error.setText(message.splitlines()[0])
        self.model_error.setToolTip(message)
        self.model_error.show()

    def clear_model_error(self):
        self.model_error.clear()
        self.model_error.setToolTip("")
        self.model_error.hide()
        self.model_retry_button.hide()

    def set_model_retry_available(self, available):
        self.model_retry_button.setVisible(available)
        self.model_retry_button.setEnabled(available and not self._model_busy)

    def clear_operation_error(self):
        self.operation_error.hide()
        self.operation_error_toggle.setChecked(False)
        self.operation_error_details.clear()
        self.retry_button.hide()

    def _refresh_model_progress(self):
        self.update_model_states(self._installed, self._ready, self._engine_available, self._model_busy)
        self._update_progress_visibility()

    def set_connection_state(self, connected):
        self.connection_state.setText("Подключено" if connected else "API-ключ не добавлен")
        self.change_key.setVisible(connected)
        self.key.setVisible(not connected)
        self.show_key.setVisible(not connected)
        self.show_key.setChecked(False)

    def set_repeat_available(self, available):
        self._repeat_available = bool(available)
        self.repeat_capture_button.setVisible(self._repeat_available)
        self.repeat_capture_button.setEnabled(self._repeat_available and not self._model_busy)

    def edit_connection_key(self):
        self.key.show()
        self.show_key.show()
        self.change_key.hide()
        self.key.setFocus()

    def update_review_provider(self, *_):
        self.review_card.setVisible(self.show_preview.isChecked())
        local = self.review_provider.currentData() == "local"
        self.review_pages.setCurrentIndex(0 if local else 1)
        self.review_local_model.setEnabled(local)
        self.review_remote_model.setEnabled(not local)
        self.connection_card.setVisible((self.show_preview.isChecked() and not local) or self.provider.currentData() == "openrouter")

    def set_catalog(self, models):
        self.remote_model.set_models(models)
        self.review_remote_model.set_models(models)
        self.catalog_status.setText("")
        self.catalog_status.hide()
        self.catalog_button.setToolTip(f"{len(models)} vision-моделей. Цены в USD за 1M входных / выходных токенов.")

    def show_configuration(self, tab="ocr"):
        self.navigation.setCurrentRow(2)
        self.configuration.setCurrentIndex({"ocr": 0, "typing": 1, "system": 2}[tab])

    def update_actions(self, *_):
        work = self.navigation.currentRow() == 0
        self.capture_button.setVisible(work)
        self.typing_button.setVisible(work)
        self.save_button.setVisible(self.has_pending() and self.navigation.currentRow() in (1, 2))
        self.save_hint.setVisible(self.has_pending() and self.navigation.currentRow() in (1, 2))
        self._update_progress_visibility()

    def _update_progress_visibility(self):
        row_progress = self.navigation.currentRow() == 1 and bool(self._model_queue or self._model_progress)
        self.progress.setVisible(self._model_busy and not row_progress)
        self.queue_cancel.setVisible(row_progress)
        self.cancel_button.setVisible(self._model_busy and not row_progress)

    def has_pending(self):
        return self.settings_values() != self._saved_values or bool(self.key.text())

    def update_pending(self, *_):
        pending = self.has_pending()
        self.save_hint.setText("Изменено" if pending else "")
        self.save_button.setEnabled(pending and not self._model_busy)
        self.update_actions()

    def update_shortcut_hints(self, *_):
        super().update_shortcut_hints()
        self.work_capture_shortcut.setText(pretty_hotkey(self.capture_hotkey.text()))
        self.work_typing_shortcut.setText(pretty_hotkey(self.typing_hotkey.text()))

    def resizeEvent(self, event):
        QDialog.resizeEvent(self, event)
        if hasattr(self, "model_table"):
            self.model_table.setColumnWidth(3, 168 if self.model_table.width() >= 720 else 164)

    def showEvent(self, event):
        super().showEvent(event)
        fit_window_to_screen(self)

    def show_permissions(self, values):
        self.permissions_toggle.setChecked(not all(values.values()))
        for key, title in (("accessibility", "Ввод"), ("screen", "Захват экрана"), ("input_monitoring", "Горячие клавиши")):
            self.permission_labels[key].setText(title + (" · доступен" if values[key] else ""))
            self.permission_buttons[key].setVisible(not values[key])
            self.permission_buttons[key].setEnabled(not values[key])

    def set_busy(self, busy):
        self._model_busy = busy
        super().set_busy(busy)
        self._update_progress_visibility()
        self.quick_preview.setEnabled(not busy)
        self.repeat_capture_button.setEnabled(self._repeat_available and not busy)
        self.speed_preset.setEnabled(not busy)
        for control in self.hotkey_record_buttons:
            control.setEnabled(not busy)
        self.key.setEnabled(not busy)
        self.show_key.setEnabled(not busy)
        self.model_retry_button.setEnabled(not busy)
        if hasattr(self, "models_page"):
            self.model_table.setEnabled(not busy)
            self.select_all_models.setEnabled(not busy)
            self.runtime_scroll.setEnabled(not busy)
            self.add_model_button.setEnabled(not busy)
            self.update_model_states(self._installed, self._ready, self._engine_available, busy)
            self.queue_cancel.setEnabled(True)
            self.review_card.setEnabled(not busy)
            self.catalog_button.setEnabled(not busy)
            self.history_page.type.setEnabled(not busy and bool(self.history_page.text.toPlainText()))
            if not busy:
                self.update_review_provider()
                self.save_button.setEnabled(self.has_pending())

    def apply_to(self, settings):
        # Base validation sees the same per-profile registry through this override.
        settings.review_provider = self.review_provider.currentData()
        settings.capture_format = self.capture_format.currentData()
        super().apply_to(settings)
