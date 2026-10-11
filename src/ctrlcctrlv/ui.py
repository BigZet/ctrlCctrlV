from pathlib import Path

from PySide6.QtCore import Qt, QRect, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen, QPixmap, QSyntaxHighlighter, QTextCharFormat, QTextOption
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QFrame, QLabel, QLineEdit, QListWidget, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from .catalog import MODELS
from .theme import stylesheet


class SelectionOverlay(QWidget):
    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, screen, snapshot):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Window)
        self.setWindowTitle("ctrlCctrlV · Выделение области")
        self.snapshot = snapshot
        self.origin = self.position = None
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def selection(self):
        return QRect(self.origin, self.position).normalized() if self.origin is not None else QRect()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.snapshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 95))
        area = self.selection()
        if not area.isEmpty():
            painter.save()
            painter.setClipRect(area)
            painter.drawPixmap(self.rect(), self.snapshot)
            painter.restore()
            painter.setPen(QPen(QColor("#7cc4ff"), 2))
            painter.drawRect(area)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(20, 30, "Выделите область · Esc — отмена")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = self.position = event.position().toPoint()
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.position = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.origin is None:
            return
        self.position = event.position().toPoint()
        area = self.selection().intersected(self.rect())
        if area.width() < 3 or area.height() < 3:
            self.cancelled.emit()
            return
        ratio = self.snapshot.devicePixelRatio()
        physical = QRect(round(area.x() * ratio), round(area.y() * ratio),
                         round(area.width() * ratio), round(area.height() * ratio))
        image = self.snapshot.copy(physical).toImage()
        image.setDevicePixelRatio(1)
        self.selected.emit(image)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()


def label(text, role=None):
    widget = QLabel(text)
    widget.setWordWrap(True)
    if role:
        widget.setProperty("role", role)
    return widget


def card():
    frame = QFrame()
    frame.setProperty("role", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(12)
    return frame, layout


def button(text, signal, primary=False):
    widget = QPushButton(text)
    widget.setMinimumHeight(34)
    if primary:
        widget.setProperty("role", "primary")
    widget.clicked.connect(signal.emit if hasattr(signal, "emit") else signal)
    return widget


def disclosure(text, body):
    toggle = QToolButton()
    toggle.setText(text)
    toggle.setCheckable(True)
    toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    toggle.setArrowType(Qt.ArrowType.RightArrow)
    body.hide()
    def changed(expanded):
        body.setVisible(expanded)
        toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        ancestor = body.parentWidget()
        while ancestor:
            if isinstance(ancestor, CurrentPageStack):
                ancestor.setMinimumHeight(ancestor.minimumSizeHint().height())
            ancestor.updateGeometry()
            ancestor = ancestor.parentWidget()
    toggle.toggled.connect(changed)
    return toggle


class CurrentPageStack(QStackedWidget):
    """Only the active provider contributes to the card's height."""
    def __init__(self):
        super().__init__()
        self.currentChanged.connect(self.update_current_size)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def update_current_size(self, *_):
        self.setMinimumHeight(self.minimumSizeHint().height())
        self.updateGeometry()

    def sizeHint(self):
        return self.currentWidget().sizeHint() if self.currentWidget() else super().sizeHint()

    def minimumSizeHint(self):
        return self.currentWidget().minimumSizeHint() if self.currentWidget() else super().minimumSizeHint()


def add_page(pages, content):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    content.setAutoFillBackground(False)
    scroll.viewport().setAutoFillBackground(False)
    pages.addWidget(scroll)


class DiffHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        dark = QApplication.palette().window().color().lightness() < 128
        format = QTextCharFormat()
        if text.startswith(("---", "+++", "@@")):
            format.setForeground(QColor("#a1aec2" if dark else "#64748b"))
        elif text.startswith("+"):
            format.setForeground(QColor("#8de0b1" if dark else "#166534"))
            format.setBackground(QColor("#203b30" if dark else "#e9f8ee"))
        elif text.startswith("-"):
            format.setForeground(QColor("#ffb3b3" if dark else "#a12727"))
            format.setBackground(QColor("#422b31" if dark else "#fcecec"))
        self.setFormat(0, len(text), format)


class CodeTextEdit(QPlainTextEdit):
    def keyPressEvent(self, event):
        if event.key() in {Qt.Key.Key_Tab, Qt.Key.Key_Backtab} and event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            # Qt maps Control/Meta differently on macOS. Accept both delivered
            # variants; normal Tab remains available for editing indentation.
            QWidget.focusNextPrevChild(self, not bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            event.accept()
            return
        super().keyPressEvent(event)


class ImageView(QScrollArea):
    """Fit the displayed pixels while retaining the original image for OCR review."""
    def __init__(self):
        super().__init__()
        self.original_pixmap = QPixmap()
        self.fit = False
        self.image = QLabel()
        self.image.setAccessibleName("Исходный снимок области экрана")
        self.image.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setWidget(self.image)

    def display(self, image):
        self.original_pixmap = QPixmap.fromImage(image)
        self.update_image()

    def set_fit(self, enabled):
        self.fit = enabled
        self.update_image()

    def update_image(self):
        pixmap = self.original_pixmap
        if pixmap.isNull():
            return
        if self.fit and not self.viewport().size().isEmpty():
            ratio = pixmap.devicePixelRatio()
            target = self.viewport().size() * ratio
            if pixmap.width() > target.width() or pixmap.height() > target.height():
                pixmap = pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
        self.image.setPixmap(pixmap)
        self.image.adjustSize()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_image()


class Preview(QMainWindow):
    copy_requested = Signal(str)
    repeat_requested = Signal()
    review_requested = Signal()
    review_cancel_requested = Signal()
    accept_review_requested = Signal(object)
    review_invalidated = Signal()
    syntax_requested = Signal(str, str, int)

    def __init__(self):
        super().__init__()
        self.original_text = ""
        self.source_image = None
        self.review_result = None
        self.full_review_result = None
        self.review_choices = []
        self.syntax_revision = 0
        self.syntax_timer = QTimer(self)
        self.syntax_timer.setSingleShot(True)
        self.syntax_timer.setInterval(250)
        self.syntax_timer.timeout.connect(lambda: self.syntax_requested.emit(
            self.text.toPlainText(), self.syntax_language.currentData(), self.syntax_revision))
        self.setWindowTitle("ctrlCctrlV · Результат")
        self.resize(1040, 660)
        self.setStyleSheet(stylesheet())
        panel = QWidget()
        self.setCentralWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        header = QHBoxLayout()
        header.addWidget(label("Текст распознан", "title"))
        header.addStretch()
        header.addWidget(button("Копировать текст", lambda: self.copy_requested.emit(self.text.toPlainText()), True))
        layout.addLayout(header)
        self.status = label("", "muted")
        layout.addWidget(self.status)
        split = QSplitter()
        self.image_view = ImageView()
        self.image = self.image_view.image
        image_panel = QWidget()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(8)
        image_header = QHBoxLayout()
        image_header.setContentsMargins(0, 0, 12, 0)
        image_header.addWidget(label("Снимок", "section"))
        image_header.addStretch()
        self.fit_image = QToolButton()
        self.fit_image.setProperty("role", "viewToggle")
        self.fit_image.setText("Вписать")
        self.fit_image.setCheckable(True)
        self.fit_image.setAccessibleName("Вписать исходный снимок в окно")
        self.fit_image.setToolTip("Показать снимок целиком. Нажмите ещё раз для исходного масштаба.")
        self.fit_image.toggled.connect(self.image_view.set_fit)
        image_header.addWidget(self.fit_image)
        image_layout.addLayout(image_header)
        image_layout.addWidget(self.image_view, 1)
        self.text = CodeTextEdit()
        self.text.setAccessibleName("Распознанный текст для редактирования")
        self.text.setToolTip("Tab вставляет табуляцию. Ctrl+Tab переводит фокус к другим элементам окна.")
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        text_panel = QWidget()
        text_layout = QVBoxLayout(text_panel)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(8)
        format_row = QHBoxLayout()
        format_row.addWidget(label("Текст", "section"))
        format_row.addStretch()
        self.syntax_language = QComboBox()
        for name, value in (("Авто", "auto"), ("Python", "python"), ("JSON", "json"), ("Без проверки", "none")):
            self.syntax_language.addItem(name, value)
        self.syntax_language.setAccessibleName("Язык проверки синтаксиса")
        self.syntax_language.setToolTip("Проверка синтаксиса — подсказка, не проверка точности OCR. Неполный фрагмент может содержать ошибку разбора.")
        self.fit_image.setFixedHeight(self.syntax_language.sizeHint().height())
        format_row.addWidget(self.syntax_language)
        text_layout.addLayout(format_row)
        text_layout.addWidget(self.text, 1)
        self.syntax_message = label("", "muted")
        self.syntax_message.setToolTip("Сверьте этот участок со снимком. Код не исполняется. Для неполного фрагмента можно выбрать «Без проверки».")
        self.syntax_message.hide()
        text_layout.addWidget(self.syntax_message)
        split.addWidget(image_panel)
        split.addWidget(text_panel)
        split.setSizes([440, 560])
        layout.addWidget(split, 1)
        self.review_panel, proposed = card()
        proposed.addWidget(label("Предлагаемые изменения", "section"))
        changes = QSplitter()
        choices = QWidget()
        self.choices_layout = QVBoxLayout(choices)
        self.choices_layout.setContentsMargins(0, 0, 12, 0)
        self.choices_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        choice_scroll = QScrollArea()
        choice_scroll.setWidgetResizable(True)
        choice_scroll.setWidget(choices)
        choices.setAutoFillBackground(False)
        choice_scroll.viewport().setAutoFillBackground(False)
        choice_scroll.setMinimumWidth(230)
        changes.addWidget(choice_scroll)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setAccessibleName("Различия исходного текста и предложения модели")
        self.diff.setToolTip("Строки с минусом удаляются, с плюсом добавляются. Точки обозначают пробелы.")
        self.diff_highlighter = DiffHighlighter(self.diff.document())
        self.diff.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        option = self.diff.document().defaultTextOption()
        option.setFlags(option.flags() | QTextOption.Flag.ShowTabsAndSpaces)
        self.diff.document().setDefaultTextOption(option)
        self.diff.setMaximumHeight(160)
        changes.addWidget(self.diff)
        changes.setSizes([280, 680])
        changes.setMaximumHeight(160)
        proposed.addWidget(changes)
        decision = QHBoxLayout()
        decision.addWidget(button("Отклонить", self.reject_review))
        decision.addStretch()
        self.accept_button = button("Применить и скопировать", lambda: self.accept_review_requested.emit(self.review_result), True)
        decision.addWidget(self.accept_button)
        proposed.addLayout(decision)
        self.review_panel.hide()
        layout.addWidget(self.review_panel)
        footer = QHBoxLayout()
        self.restore_button = button("Исходный текст", self.restore_original)
        self.restore_button.hide()
        footer.addWidget(self.restore_button)
        footer.addStretch()
        self.repeat_button = button("Повторить OCR", self.repeat_requested)
        footer.addWidget(self.repeat_button)
        self.review_button = button("Проверить текст", self.review_requested)
        self.review_button.setToolTip("Сверить текст со снимком отдельной моделью. Исправления принимаете вы.")
        footer.addWidget(self.review_button)
        self.review_cancel_button = button("Остановить проверку", self.review_cancel_requested)
        self.review_cancel_button.hide()
        footer.addWidget(self.review_cancel_button)
        layout.addLayout(footer)
        self.text.textChanged.connect(self.invalidate_review)
        self.text.textChanged.connect(self.schedule_syntax)
        self.syntax_language.currentIndexChanged.connect(self.schedule_syntax)

    def schedule_syntax(self):
        self.syntax_revision += 1
        self.syntax_message.clear()
        self.syntax_message.hide()
        self.syntax_timer.start()

    def show_syntax_hint(self, revision, hint):
        if revision != self.syntax_revision:
            return
        self.syntax_message.setText(hint.label if hint else "")
        self.syntax_message.setVisible(hint is not None)

    def invalidate_review(self):
        self.review_result = None
        self.full_review_result = None
        self.review_panel.hide()
        self.restore_button.setVisible(self.text.toPlainText() != self.original_text)
        self.review_invalidated.emit()

    def set_review_busy(self, busy):
        self.review_button.setEnabled(not busy)
        self.review_button.setText("Проверка…" if busy else "Проверить текст")
        self.repeat_button.setEnabled(not busy)
        self.review_cancel_button.setVisible(busy)

    def propose(self, result):
        if self.text.toPlainText() != result.original:
            return False
        self.full_review_result = result
        while self.choices_layout.count():
            self.choices_layout.takeAt(0).widget().deleteLater()
        self.review_choices = []
        for change in result.changes():
            item = QWidget()
            item_layout = QVBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 6)
            item_layout.setSpacing(0)
            choice = QCheckBox(change.label.split(" · ")[0])
            choice.setAccessibleName(change.label)
            choice.setToolTip(change.explanation + ". Снимите галочку, чтобы оставить исходный текст.")
            choice.setChecked(True)
            choice.toggled.connect(self.select_review_changes)
            item_layout.addWidget(choice)
            item_layout.addWidget(label(change.explanation, "muted"))
            self.choices_layout.addWidget(item)
            self.review_choices.append(choice)
        self.select_review_changes()
        self.review_panel.setVisible(result.changed)
        self.status.setText(f"{'Есть предложение — проверьте различия' if result.changed else 'Изменений не предложено'} · {result.elapsed:.1f} с")
        return True

    def select_review_changes(self):
        if self.full_review_result is None:
            return
        self.review_result = self.full_review_result.select(
            index for index, choice in enumerate(self.review_choices) if choice.isChecked())
        self.diff.setPlainText(self.review_result.diff())
        self.accept_button.setEnabled(self.review_result.changed)

    def reject_review(self):
        self.review_result = None
        self.full_review_result = None
        self.review_panel.hide()
        self.status.setText("Предложение отклонено · текст и буфер не изменены")

    def replace_text(self, text):
        # Keep the edit undoable, including accepting a model's proposal.
        cursor = self.text.textCursor()
        cursor.beginEditBlock()
        cursor.select(cursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()

    def restore_original(self):
        self.replace_text(self.original_text)
        self.status.setText("Исходный текст восстановлен · нажмите «Копировать текст»")

    def display(self, image, text, elapsed, model):
        self.source_image = image.copy()
        self.image_view.display(image)
        self.original_text = text
        self.text.setPlainText(text)
        self.invalidate_review()
        self.status.setText(f"Скопировано в буфер · {elapsed:.2f} с · {model}" +
                            (" · дольше цели 4 с" if elapsed > 4 else ""))
        self.show()
        self.raise_()
        self.activateWindow()


class DiagnosticsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ctrlCctrlV · Диагностика")
        self.resize(720, 560)
        self.setStyleSheet(stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.addWidget(label("Диагностика", "title"))
        layout.addWidget(label("Версии, разрешения и состояние моделей.\nБез снимков, распознанного текста, содержимого буфера и ключей.", "muted"))
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.report.setAccessibleName("Диагностический отчёт приложения")
        layout.addWidget(self.report, 1)
        row = QHBoxLayout()
        row.addWidget(button("Закрыть", self.close))
        row.addStretch()
        self.copy_button = button("Копировать отчёт", lambda: QApplication.clipboard().setText(self.report.toPlainText()), True)
        row.addWidget(self.copy_button)
        layout.addLayout(row)

    def display(self, report):
        self.report.setPlainText(report)
        self.show()
        self.raise_()
        self.activateWindow()


class SettingsWindow(QDialog):
    save_requested = Signal()
    install_requested = Signal()
    start_requested = Signal()
    stop_requested = Signal()
    delete_requested = Signal()
    permissions_requested = Signal()
    permission_settings_requested = Signal(str)
    capture_requested = Signal()
    retry_requested = Signal()
    typing_requested = Signal()
    cancel_requested = Signal()
    preview_option_changed = Signal(bool)
    diagnostics_requested = Signal()

    def __init__(self, settings):
        super().__init__()
        self.setWindowTitle("ctrlCctrlV")
        self.resize(840, 650)
        self.setMinimumSize(780, 610)
        self.setStyleSheet(stylesheet())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 22, 22, 18)
        outer.setSpacing(18)
        body = QHBoxLayout()
        body.setSpacing(24)
        sidebar = QVBoxLayout()
        brand = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(QPixmap(str(Path(__file__).parent / "assets/app.png")).scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(icon)
        brand.addWidget(label("ctrlCctrlV", "brand"))
        brand.addStretch()
        sidebar.addLayout(brand)
        sidebar.addSpacing(18)
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(156)
        self.navigation.addItems(["Распознавание", "Печать", "Система"])
        sidebar.addWidget(self.navigation, 1)
        sidebar.addWidget(label("Текст между\nMac и VDI", "muted"))
        body.addLayout(sidebar)
        self.pages = pages = QStackedWidget()
        body.addWidget(pages, 1)
        self.navigation.currentRowChanged.connect(pages.setCurrentIndex)

        ocr = QWidget()
        layout = QVBoxLayout(ocr)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(label("Распознавание", "title"))
        layout.addWidget(label("Выделите область — текст окажется в буфере.", "subtitle"))
        provider_card, pc = card()
        self.provider = QComboBox()
        self.provider.addItem("На этом Mac", "local")
        self.provider.addItem("Через OpenRouter", "openrouter")
        self.provider.setCurrentIndex(0 if settings.provider == "local" else 1)
        pc.addWidget(label("Источник OCR", "section"))
        pc.addWidget(self.provider)
        self.provider_pages = CurrentPageStack()
        local = QWidget()
        ll = QVBoxLayout(local)
        ll.setContentsMargins(0, 4, 0, 0)
        ll.setSpacing(10)
        ll.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.local_model = QComboBox()
        for identifier, model in MODELS.items():
            size = sum(f.size for f in model.files) / 1_000_000_000
            self.local_model.addItem(f"{model.label.split(' · ')[0]} · {size:.1f} ГБ", identifier)
            self.local_model.setItemData(self.local_model.count() - 1, model.label, Qt.ItemDataRole.ToolTipRole)
        self.local_model.setCurrentIndex(max(0, self.local_model.findData(settings.local_model)))
        ll.addWidget(self.local_model)
        self.local_info = label("", "muted")
        ll.addWidget(self.local_info)
        controls = QHBoxLayout()
        self.install_button = button("Установить", self.install_requested)
        self.prepare_button = button("Подготовить", self.start_requested)
        controls.addWidget(self.install_button)
        controls.addWidget(self.prepare_button)
        controls.addStretch()
        ll.addLayout(controls)
        advanced = QWidget()
        af = QFormLayout(advanced)
        af.setContentsMargins(0, 4, 0, 0)
        self.idle = QSpinBox()
        self.idle.setRange(1, 120)
        self.idle.setValue(settings.idle_minutes)
        self.idle.setSuffix(" мин")
        af.addRow("Выгрузка после простоя", self.idle)
        self.review_local_model = QComboBox()
        for identifier, model in MODELS.items():
            if model.mode == "json":
                self.review_local_model.addItem(model.label.split(" · ")[0], identifier)
        self.review_local_model.setCurrentIndex(max(0, self.review_local_model.findData(settings.review_local_model)))
        af.addRow("Модель проверки текста", self.review_local_model)
        maintenance = QHBoxLayout()
        maintenance.addWidget(button("Выгрузить", self.stop_requested))
        maintenance.addWidget(button("Удалить модель", self.delete_requested))
        af.addRow(maintenance)
        af.addRow(label("LightOnOCR — экспериментальная модель. Проверяйте символы и отступы.", "muted"))
        ll.addWidget(disclosure("Дополнительно", advanced))
        ll.addWidget(advanced)
        self.provider_pages.addWidget(local)
        cloud = QWidget()
        cf = QVBoxLayout(cloud)
        cf.setContentsMargins(0, 4, 0, 0)
        cf.setSpacing(10)
        cf.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.remote_model = QLineEdit(settings.remote_model)
        self.remote_model.setPlaceholderText("Модель с поддержкой изображений")
        cf.addWidget(label("Модель", "muted"))
        cf.addWidget(self.remote_model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API-ключ · хранится в Keychain")
        cf.addWidget(self.key)
        remote_advanced = QWidget()
        rf = QFormLayout(remote_advanced)
        rf.setContentsMargins(0, 4, 0, 0)
        self.response_mode = QComboBox()
        self.response_mode.addItem("Обычная vision-модель", "json")
        self.response_mode.addItem("Специализированная OCR-модель", "transcription")
        self.response_mode.setCurrentIndex(0 if settings.remote_mode == "json" else 1)
        rf.addRow("Тип модели", self.response_mode)
        self.review_remote_model = QLineEdit(settings.review_remote_model)
        rf.addRow("Модель проверки текста", self.review_remote_model)
        rf.addRow(label("OCR-модели могут возвращать Markdown. Формат сохраняется.", "muted"))
        cf.addWidget(disclosure("Дополнительно", remote_advanced))
        cf.addWidget(remote_advanced)
        self.provider_pages.addWidget(cloud)
        self.provider_pages.setCurrentIndex(self.provider.currentIndex())
        self.provider.currentIndexChanged.connect(self.provider_pages.setCurrentIndex)
        self.provider_card = provider_card
        pc.addWidget(self.provider_pages)
        layout.addWidget(provider_card)
        output_card, oc = card()
        self.show_preview = QCheckBox("Показывать предпросмотр")
        self.show_preview.setChecked(settings.show_preview)
        self.show_preview.toggled.connect(self.preview_option_changed.emit)
        oc.addWidget(self.show_preview)
        self.preview_hint = label("", "muted")
        def preview_hint(enabled):
            enabled = self.show_preview.isChecked()
            self.preview_hint.setText("Текст скопируется, затем откроется для проверки." if enabled else "Текст скопируется сразу, без окна результата.")
        preview_hint(settings.show_preview)
        self.show_preview.toggled.connect(preview_hint)
        oc.addWidget(self.preview_hint)
        layout.addWidget(output_card)
        layout.addStretch()
        add_page(pages, ocr)

        typing = QWidget()
        tl = QVBoxLayout(typing)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(14)
        tl.addWidget(label("Печать в VDI", "title"))
        tl.addWidget(label("Поставьте курсор в редактор и нажмите сочетание печати.", "subtitle"))
        typing_card, tc = card()
        self.autoindent = QCheckBox("Компенсировать автоотступы")
        self.autoindent.setChecked(settings.autoindent)
        tc.addWidget(self.autoindent)
        tc.addWidget(label("Проверяйте этот режим в своём редакторе. Автозакрытие скобок и кавычек отключите в редакторе.", "muted"))
        tf = QFormLayout()
        self.delay = QDoubleSpinBox()
        self.delay.setRange(1, 200)
        self.delay.setValue(settings.character_delay * 1000)
        self.delay.setSuffix(" мс")
        tf.addRow("Между символами", self.delay)
        self.newline = QDoubleSpinBox()
        self.newline.setRange(0, 2000)
        self.newline.setValue(settings.newline_delay * 1000)
        self.newline.setSuffix(" мс")
        tf.addRow("После Enter", self.newline)
        self.tabs = QSpinBox()
        self.tabs.setRange(0, 8)
        self.tabs.setValue(settings.spaces_per_tab)
        self.tabs.setSpecialValueText("Передавать Tab")
        tf.addRow("Пробелов вместо Tab", self.tabs)
        tc.addLayout(tf)
        tl.addWidget(typing_card)
        tl.addWidget(label("При смене активного приложения печать останавливается.", "muted"))
        tl.addStretch()
        add_page(pages, typing)

        system = QWidget()
        sl = QVBoxLayout(system)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(14)
        system_header = QHBoxLayout()
        system_header.addWidget(label("Система", "title"))
        system_header.addStretch()
        self.diagnostics_button = button("Диагностика", self.diagnostics_requested)
        system_header.addWidget(self.diagnostics_button)
        sl.addLayout(system_header)
        sl.addWidget(label("Разрешения macOS и сочетания клавиш.", "subtitle"))
        permission_card, perms = card()
        self.permission_labels = {}
        self.permission_buttons = {}
        for key in ("accessibility", "screen", "input_monitoring"):
            row = QHBoxLayout()
            description = label("")
            action = button("Предоставить доступ", lambda checked=False, key=key:
                            self.permission_settings_requested.emit(key))
            row.addWidget(description, 1)
            row.addWidget(action)
            perms.addLayout(row)
            self.permission_labels[key] = description
            self.permission_buttons[key] = action
        perms.addWidget(label("Кнопка запросит доступ у macOS. После разрешения статус обновится автоматически.", "muted"))
        sl.addWidget(permission_card)
        hotkeys_card, hk = card()
        hk.addWidget(label("Горячие клавиши", "section"))
        keyform = QFormLayout()
        self.capture_hotkey = QLineEdit(settings.capture_hotkey)
        self.typing_hotkey = QLineEdit(settings.typing_hotkey)
        self.cancel_hotkey = QLineEdit(settings.cancel_hotkey)
        keyform.addRow("Распознавание", self.capture_hotkey)
        keyform.addRow("Печать", self.typing_hotkey)
        keyform.addRow("Остановка", self.cancel_hotkey)
        hk.addLayout(keyform)
        hk.addWidget(label("cmd — Command · alt — Option · ctrl — Control", "muted"))
        sl.addWidget(hotkeys_card)
        sl.addStretch()
        add_page(pages, system)
        self.navigation.setCurrentRow(0)
        outer.addLayout(body, 1)
        self.status = label("Подготовьте модель и выделите область", "muted")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        self.retry_button = button("Повторить OCR", self.retry_requested)
        self.retry_button.setToolTip("Распознать тот же снимок ещё раз с текущими настройками")
        self.retry_button.hide()
        status_row.addWidget(self.retry_button)
        outer.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.hide()
        outer.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.save_button = button("Сохранить", self.save_requested)
        buttons.addWidget(self.save_button)
        self.cancel_button = button("Остановить", self.cancel_requested)
        self.cancel_button.hide()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        self.capture_button = button("Выделить область", self.capture_requested, True)
        self.capture_button.setToolTip(settings.capture_hotkey)
        buttons.addWidget(self.capture_button)
        self.typing_button = button("Напечатать буфер через 3 с", self.typing_requested, True)
        self.typing_button.setToolTip("Выберите целевой редактор в течение трёх секунд. Отмена: " + settings.cancel_hotkey)
        buttons.addWidget(self.typing_button)
        self.navigation.currentRowChanged.connect(self.update_actions)
        self.update_actions()
        outer.addLayout(buttons)

    def update_actions(self, *_):
        typing = self.navigation.currentRow() == 1
        self.typing_button.setVisible(typing)
        self.capture_button.setVisible(not typing)

    def set_busy(self, busy):
        self.retry_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.save_button.setEnabled(not busy)
        self.capture_button.setEnabled(not busy)
        self.typing_button.setEnabled(not busy)
        self.provider_card.setEnabled(not busy)
        self.diagnostics_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if not busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

    def show_permissions(self, values):
        labels = {"accessibility": "Управление вводом (Accessibility)",
                  "screen": "Запись экрана", "input_monitoring": "Мониторинг ввода"}
        for key, name in labels.items():
            allowed = values[key]
            self.permission_labels[key].setText(
                f"{'✓' if allowed else '○'} {name}\n"
                f"{'Предоставлено' if allowed else 'Не предоставлено'}")
            self.permission_buttons[key].setVisible(not allowed)
            self.permission_buttons[key].setEnabled(not allowed)

    def apply_to(self, settings):
        settings.provider = self.provider.currentData()
        settings.local_model = self.local_model.currentData()
        settings.remote_model = self.remote_model.text().strip()
        settings.remote_mode = self.response_mode.currentData()
        settings.show_preview = self.show_preview.isChecked()
        settings.review_local_model = self.review_local_model.currentData()
        settings.review_remote_model = self.review_remote_model.text().strip()
        settings.capture_hotkey = self.capture_hotkey.text()
        settings.typing_hotkey = self.typing_hotkey.text()
        settings.cancel_hotkey = self.cancel_hotkey.text()
        settings.character_delay = self.delay.value() / 1000
        settings.newline_delay = self.newline.value() / 1000
        settings.autoindent = self.autoindent.isChecked()
        settings.spaces_per_tab = self.tabs.value()
        settings.idle_minutes = self.idle.value()
        settings.validate()
