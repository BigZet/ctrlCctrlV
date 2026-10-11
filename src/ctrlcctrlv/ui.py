from pathlib import Path
import sys

from PySide6.QtCore import Qt, QRect, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen, QPixmap, QShortcut, QKeySequence, QSyntaxHighlighter, QTextCharFormat, QTextOption
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
    QWizard, QWizardPage,
)

from .catalog import MODELS
from .theme import colours, editor_palette, stylesheet
from .fonts import monospace_font, prepare_fonts


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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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
        help_text = "Выделите область · Esc или правая кнопка — отмена"
        help_width = painter.fontMetrics().horizontalAdvance(help_text) + 32
        help_rect = QRect((self.width() - help_width) // 2, 20, help_width, 40)
        if not area.intersects(help_rect):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(16, 21, 30, 235))
            painter.drawRoundedRect(help_rect, 10, 10)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(help_rect, Qt.AlignmentFlag.AlignCenter, help_text)
        if not area.isEmpty():
            ratio = self.snapshot.devicePixelRatio()
            dimensions = f"{round(area.width() * ratio)} × {round(area.height() * ratio)} px"
            width = painter.fontMetrics().horizontalAdvance(dimensions) + 20
            badge = QRect(min(area.left(), max(0, self.width() - width)), area.bottom() + 8, width, 28)
            if badge.bottom() >= self.height():
                badge.moveBottom(area.top() - 8)
            if self.rect().contains(badge) and not badge.intersects(help_rect):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(16, 21, 30, 235))
                painter.drawRoundedRect(badge, 6, 6)
                painter.setPen(Qt.GlobalColor.white)
                painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, dimensions)

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
    widget.setTextFormat(Qt.TextFormat.PlainText)
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
    widget.setAutoDefault(False)
    widget.setMinimumHeight(34)
    if primary:
        widget.setProperty("role", "primary")
    widget.clicked.connect(signal.emit if hasattr(signal, "emit") else signal)
    return widget


def form():
    layout = QFormLayout()
    layout.setHorizontalSpacing(20)
    layout.setVerticalSpacing(12)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    return layout


def steps(items):
    frame, layout = card()
    row = QHBoxLayout()
    row.setSpacing(18)
    for number, (title, description) in enumerate(items, 1):
        column = QVBoxLayout()
        column.setSpacing(5)
        column.addWidget(label(f"0{number}  {title}", "eyebrow"))
        column.addWidget(label(description, "muted"))
        row.addLayout(column, 1)
    layout.addLayout(row)
    return frame


def view_toggle(text, name, callback):
    toggle = QToolButton()
    toggle.setProperty("role", "viewToggle")
    toggle.setText(text)
    toggle.setCheckable(True)
    toggle.setAccessibleName(name)
    toggle.toggled.connect(callback)
    return toggle


class SystemTheme:
    def init_theme(self):
        self.apply_system_theme()
        QApplication.instance().paletteChanged.connect(self.apply_system_theme)

    def apply_system_theme(self, *_):
        self.setStyleSheet(stylesheet())
        self.setPalette(editor_palette())
        if hasattr(self, 'diff_highlighter'):
            self.diff_highlighter.rehighlight()
        self.update()


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


class LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event):
        editor = self.editor
        painter = QPainter(self)
        c = colours()
        painter.fillRect(event.rect(), QColor(c['panel']))
        painter.setFont(editor.font())
        block = editor.firstVisibleBlock()
        top = round(editor.blockBoundingGeometry(block).translated(editor.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            height = round(editor.blockBoundingRect(block).height())
            if block.isVisible() and top + height >= event.rect().top():
                active = block.blockNumber() == editor.textCursor().blockNumber()
                painter.setPen(QColor(c['accent'] if active else c['muted']))
                painter.drawText(0, top, self.width() - 9, editor.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            top += height
            block = block.next()


class CodeTextEdit(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.gutter = LineNumbers(self)
        self.blockCountChanged.connect(self.update_gutter)
        self.updateRequest.connect(self.update_gutter)
        self.cursorPositionChanged.connect(self.gutter.update)
        self.update_gutter()

    def update_gutter(self, *_):
        width = 20 + self.fontMetrics().horizontalAdvance('9') * len(str(self.blockCount()))
        self.setViewportMargins(width, 0, 0, 0)
        rect = self.contentsRect()
        self.gutter.setGeometry(rect.left(), rect.top(), width, rect.height())
        self.gutter.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_gutter()

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


class Preview(SystemTheme, QMainWindow):
    copy_requested = Signal(str)
    repeat_requested = Signal()
    review_requested = Signal()
    review_cancel_requested = Signal()
    accept_review_requested = Signal(object)
    review_invalidated = Signal()
    syntax_requested = Signal(str, str, int)

    def __init__(self):
        prepare_fonts()
        super().__init__()
        self.original_text = ""
        self.copied_text = None
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
        self.resize(1180, 780)
        self.setMinimumSize(840, 620)
        self.init_theme()
        panel = QWidget()
        self.setCentralWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(5)
        heading.addWidget(label("РЕЗУЛЬТАТ РАСПОЗНАВАНИЯ", "eyebrow"))
        heading.addWidget(label("Сверьте и используйте текст", "title"))
        header.addLayout(heading, 1)
        self.copy_button = button("Копировать текст", lambda: self.copy_requested.emit(self.text.toPlainText()), True)
        self.copy_button.setToolTip("Копировать весь текст, включая ручные правки")
        header.addWidget(self.copy_button)
        self.copy_shortcut = QShortcut(QKeySequence("Meta+Shift+C" if sys.platform == "darwin" else "Ctrl+Shift+C"), self)
        self.copy_shortcut.activated.connect(self.copy_button.click)
        layout.addLayout(header)
        self.status = label("", "muted")
        layout.addWidget(self.status)
        split = QSplitter()
        split.setHandleWidth(12)
        split.setChildrenCollapsible(False)
        self.image_view = ImageView()
        self.image = self.image_view.image
        image_panel = QFrame()
        image_panel.setProperty("role", "workspace")
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(14, 12, 14, 12)
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
        self.image_details = label("Исходная область экрана", "muted")
        image_layout.addWidget(self.image_details)
        self.text = CodeTextEdit()
        self.text.setAccessibleName("Распознанный текст для редактирования")
        self.text.setToolTip("Tab вставляет табуляцию. Ctrl+Tab переводит фокус к другим элементам окна.")
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(monospace_font())
        self.text.setPlaceholderText("Здесь появится распознанный текст. Его можно исправить перед копированием.")
        self.text.update_gutter()
        text_panel = QFrame()
        text_panel.setProperty("role", "workspace")
        text_layout = QVBoxLayout(text_panel)
        text_layout.setContentsMargins(14, 12, 14, 12)
        text_layout.setSpacing(8)
        format_row = QHBoxLayout()
        format_row.addWidget(label("Текст", "section"))
        format_row.addStretch()
        self.font_size = QComboBox()
        for size in (12, 14, 16, 18, 20, 24):
            self.font_size.addItem(f"{size} px", size)
        self.font_size.setAccessibleName("Размер шрифта распознанного текста")
        self.font_size.setToolTip("Размер текста в редакторе. Содержимое буфера не меняется.")
        self.font_size.setCurrentIndex(1)
        self.font_size.currentIndexChanged.connect(self.update_editor_font)
        self.update_editor_font()
        format_row.addWidget(self.font_size)
        self.syntax_language = QComboBox()
        for name, value in (("Авто", "auto"), ("Python", "python"), ("JSON", "json"), ("Без проверки", "none")):
            self.syntax_language.addItem(name, value)
        self.syntax_language.setAccessibleName("Язык проверки синтаксиса")
        self.syntax_language.setToolTip("Проверка синтаксиса — подсказка, не проверка точности OCR. Неполный фрагмент может содержать ошибку разбора.")
        self.fit_image.setFixedHeight(self.syntax_language.sizeHint().height())
        format_row.addWidget(self.syntax_language)
        text_layout.addLayout(format_row)
        text_layout.addWidget(self.text, 1)
        editor_tools = QHBoxLayout()
        self.whitespace = view_toggle("Пробелы", "Показать пробелы и табуляцию", self.show_whitespace)
        self.whitespace.setToolTip("Показать пробелы и Tab, не изменяя текст")
        editor_tools.addWidget(self.whitespace)
        self.wrap_text = view_toggle("Перенос", "Переносить длинные строки на экране", lambda enabled:
                                     self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if enabled else QPlainTextEdit.LineWrapMode.NoWrap))
        self.wrap_text.setToolTip("Перенос только на экране. Содержимое буфера сохраняет исходные строки.")
        editor_tools.addWidget(self.wrap_text)
        editor_tools.addStretch()
        self.text_stats = label("", "muted")
        editor_tools.addWidget(self.text_stats)
        text_layout.addLayout(editor_tools)
        self.copy_state = label("", "muted")
        text_layout.addWidget(self.copy_state)
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
        proposed.addWidget(label("Выберите исправления и сверьте их со снимком. Применение обновит текст и буфер.", "muted"))
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
        self.diff.setFont(monospace_font())
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
        self.text.textChanged.connect(self.update_text_details)
        self.text.cursorPositionChanged.connect(self.update_text_details)
        self.syntax_language.currentIndexChanged.connect(self.schedule_syntax)
        self.update_text_details()

    def show_whitespace(self, enabled):
        option = self.text.document().defaultTextOption()
        flags = option.flags()
        flag = QTextOption.Flag.ShowTabsAndSpaces
        option.setFlags(flags | flag if enabled else flags & ~flag)
        self.text.document().setDefaultTextOption(option)

    def update_editor_font(self, *_):
        self.text.setStyleSheet(f"font-size: {self.font_size.currentData()}px;")
        self.text.update_gutter()

    def update_text_details(self):
        text = self.text.toPlainText()
        cursor = self.text.textCursor()
        self.text_stats.setText(f"{len(text)} симв. · {self.text.blockCount()} стр.")
        self.text_stats.setToolTip(f"Курсор: строка {cursor.blockNumber() + 1}, столбец {cursor.positionInBlock() + 1}")
        pending = text != self.copied_text
        self.copy_state.setText("Правки ещё не скопированы" if pending and self.copied_text is not None else
                                "Совпадает с последней копией · правки копируются кнопкой" if self.copied_text is not None else
                                "После редактирования нажмите «Копировать текст»")
        self.copy_button.setEnabled(bool(text))

    def mark_copied(self, text):
        self.copied_text = text
        self.update_text_details()

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
        if self.copied_text is not None:
            self.status.setText("Текст изменён · нажмите «Копировать текст»" if self.text.toPlainText() != self.copied_text else
                                "Текст совпадает с последней копией")
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
        self.copied_text = text
        self.text.setPlainText(text)
        self.update_text_details()
        self.image_details.setText(f"{image.width()} × {image.height()} px · оригинал сохранён")
        self.invalidate_review()
        self.status.setText(f"Скопировано в буфер · {elapsed:.2f} с · {model}" +
                            (" · дольше цели 4 с" if elapsed > 4 else ""))
        self.show()
        self.raise_()
        self.activateWindow()


class DiagnosticsDialog(SystemTheme, QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ctrlCctrlV · Диагностика")
        self.resize(720, 560)
        self.init_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.addWidget(label("СОСТОЯНИЕ ПРИЛОЖЕНИЯ", "eyebrow"))
        layout.addWidget(label("Диагностика", "title"))
        layout.addWidget(label("Версии, разрешения и состояние моделей.\nБез снимков, распознанного текста, содержимого буфера и ключей.", "muted"))
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFont(monospace_font())
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


class SetupWizard(SystemTheme, QWizard):
    """Choose an explicit OCR mode before entering the normal settings screen."""

    def __init__(self, parent=None):
        prepare_fonts()
        super().__init__(parent)
        self.setWindowTitle("ctrlCctrlV · Первый запуск")
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setTitleFormat(Qt.TextFormat.RichText)
        self.init_theme()
        self.setMinimumSize(700, 540)
        self.setButtonText(QWizard.WizardButton.NextButton, "Далее")
        self.setButtonText(QWizard.WizardButton.BackButton, "Назад")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Открыть настройки")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Позже")
        choice = QWizardPage()
        choice.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        choice.setTitle('<span style="font-size:24px; font-weight:600">Добро пожаловать в ctrlCctrlV</span>')
        choice.setSubTitle("Текст со снимка — в буфер. Текст из буфера — в ваш редактор.")
        layout = QVBoxLayout(choice)
        layout.setSpacing(16)
        layout.addWidget(steps((("Выделите", "Захватите только нужную область экрана."), ("Сверьте", "Проверьте символы и отступы по снимку."), ("Перенесите", "Скопируйте результат или напечатайте в VDI."))))
        mode_card, mode_layout = card()
        mode_layout.addWidget(label("Где выполнять распознавание?", "section"))
        self.provider = QComboBox()
        self.provider.addItem("На этом компьютере", "local")
        self.provider.addItem("Через OpenRouter", "openrouter")
        self.provider.setAccessibleName("Режим OCR при первом запуске")
        mode_layout.addWidget(self.provider)
        self.mode_description = label("", "muted")
        mode_layout.addWidget(self.mode_description)
        self.acceleration = QComboBox()
        self.acceleration.addItem("GPU · Metal / NVIDIA CUDA", "gpu")
        self.acceleration.addItem("CPU · без ускорения GPU", "cpu")
        self.acceleration.setAccessibleName("Ускорение при первом запуске")
        mode_layout.addWidget(self.acceleration)
        layout.addWidget(mode_card)
        from .hardware import GIB, memory_capacity
        import platform
        total, _ = memory_capacity()
        system = f"{platform.system()} {platform.machine()}"
        if total:
            system += f" · RAM {total / GIB:.1f} ГБ"
        layout.addWidget(label(system, "muted"))
        layout.addStretch()
        self.addPage(choice)
        configuration = QWizardPage()
        configuration.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        configuration.setTitle('<span style="font-size:24px; font-weight:600">Остался один шаг</span>')
        configuration.setSubTitle("Приложение откроет настройки. Установка и запросы запускаются вами.")
        layout = QVBoxLayout(configuration)
        self.choice_summary = label("", "badge")
        layout.addWidget(self.choice_summary)
        self.instructions = label("", "subtitle")
        layout.addWidget(self.instructions)
        layout.addWidget(label("После настройки используйте кнопку «Выделить область» или горячие клавиши. Закрытие окна оставляет приложение в трее.", "notice"))
        layout.addStretch()
        self.addPage(configuration)
        self.provider.currentIndexChanged.connect(self.update_choice)
        self.acceleration.currentIndexChanged.connect(self.update_choice)
        self.update_choice()

    def update_choice(self):
        local = self.provider.currentData() == "local"
        self.acceleration.setVisible(local)
        if local:
            mode = "GPU" if self.acceleration.currentData() == "gpu" else "CPU"
            self.mode_description.setText("Снимки остаются на компьютере. После установки модель работает без сети. GPU использует Metal на Mac или NVIDIA CUDA на Windows; CPU выбирается отдельно.")
            self.choice_summary.setText("На этом компьютере · " + mode)
            self.instructions.setText(f"1. Установите движок и модель LightOnOCR (около 1 ГБ весов).\n\n2. Нажмите «Подготовить» — приложение проверит оборудование и память для {mode}.\n\n3. Выделите область экрана и сверьте результат. При необходимости выберите другой режим вычислений в настройках.")
        else:
            self.mode_description.setText("Выделенная область отправляется в OpenRouter. Нужны интернет, API-ключ и баланс. Стоимость зависит от выбранной модели; установка локальных весов не требуется.")
            self.choice_summary.setText("Через OpenRouter · облачная модель")
            self.instructions.setText("1. Введите API-ключ OpenRouter в настройках.\n\n2. Проверьте выбранную модель с поддержкой изображений и нажмите «Сохранить». Ключ попадёт в системное хранилище.\n\n3. Выделите область экрана. Только этот запуск отправит платный запрос выбранной модели.")


class SettingsWindow(SystemTheme, QDialog):
    hardware_requested = Signal()
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
        prepare_fonts()
        super().__init__()
        self.setWindowTitle("ctrlCctrlV")
        self.resize(1040, 820)
        self.setMinimumSize(820, 620)
        self.init_theme()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        sidebar_panel = QFrame()
        sidebar_panel.setProperty("role", "sidebar")
        sidebar_panel.setFixedWidth(220)
        sidebar = QVBoxLayout(sidebar_panel)
        sidebar.setContentsMargins(20, 26, 20, 22)
        sidebar.setSpacing(10)
        brand = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(QPixmap(str(Path(__file__).parent / "assets/app.png")).scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(icon)
        brand.addWidget(label("ctrlCctrlV", "brand"))
        brand.addStretch()
        sidebar.addLayout(brand)
        sidebar.addWidget(label("ЗАХВАТИТЬ. СВЕРИТЬ. ПЕРЕНЕСТИ.", "eyebrow"))
        sidebar.addSpacing(18)
        self.navigation = QListWidget()
        self.navigation.setAccessibleName("Разделы приложения")
        self.navigation.setMinimumHeight(232)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_titles = []
        for title, description in (("Распознавание", "Модели и результат"), ("Печать в VDI", "Ввод из буфера"), ("Система", "Клавиши и доступ")):
            item = QListWidgetItem(self.navigation)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, title)
            item.setSizeHint(QSize(180, 70))
            item.setToolTip(description)
            contents = QWidget()
            contents.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            item_layout = QVBoxLayout(contents)
            item_layout.setContentsMargins(14, 10, 10, 10)
            item_layout.setSpacing(4)
            title_label = label(title, "navtitle")
            item_layout.addWidget(title_label)
            item_layout.addWidget(label(description, "muted"))
            self.navigation.setItemWidget(item, contents)
            self.nav_titles.append(title_label)
        sidebar.addWidget(self.navigation, 1)
        sidebar.addWidget(label("Работает из трея", "section"))
        self.tray_hint = label("Закройте окно и используйте горячие клавиши. Настройки доступны через значок приложения.", "muted")
        sidebar.addWidget(self.tray_hint)
        body.addWidget(sidebar_panel)
        self.pages = pages = QStackedWidget()
        content_panel = QWidget()
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(28, 24, 28, 22)
        content_layout.addWidget(pages)
        body.addWidget(content_panel, 1)
        self.navigation.currentRowChanged.connect(pages.setCurrentIndex)

        ocr = QWidget()
        layout = QVBoxLayout(ocr)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(label("ИЗ ОБЛАСТИ ЭКРАНА В ТЕКСТ", "eyebrow"))
        layout.addWidget(label("Распознавание", "title"))
        layout.addWidget(label("Выделите область — текст окажется в буфере.", "subtitle"))
        provider_card, pc = card()
        self.provider = QComboBox()
        self.provider.addItem("На этом компьютере", "local")
        self.provider.addItem("Через OpenRouter", "openrouter")
        self.provider.setCurrentIndex(0 if settings.provider == "local" else 1)
        pc.addWidget(label("Способ распознавания", "section"))
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
        local_form = form()
        local_form.addRow("Модель OCR", self.local_model)
        self.acceleration = QComboBox()
        self.acceleration.addItem("GPU · Metal / NVIDIA CUDA", "gpu")
        self.acceleration.addItem("CPU · без ускорения GPU", "cpu")
        self.acceleration.setCurrentIndex(max(0, self.acceleration.findData(settings.acceleration)))
        self.acceleration.setAccessibleName("Ускорение локального OCR")
        local_form.addRow("Вычисления", self.acceleration)
        ll.addLayout(local_form)
        ll.addWidget(label("Снимок обрабатывается на этом компьютере. Интернет нужен для установки модели.", "muted"))
        self.local_info = label("Модель ещё не установлена", "badge")
        ll.addWidget(self.local_info)
        controls = QHBoxLayout()
        self.install_button = button("Установить", self.install_requested)
        self.prepare_button = button("Подготовить", self.start_requested)
        self.install_button.setToolTip("Скачать движок и выбранную модель. Размер загрузки указан в списке.")
        self.prepare_button.setToolTip("Загрузить установленную модель в память и подготовить OCR")
        self.prepare_button.hide()
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
        af.addRow(button("Проверить оборудование", self.hardware_requested))
        self.hardware_info = label("При подготовке проверяются GPU и доступная память. CPU выбирается явно.", "muted")
        af.addRow(self.hardware_info)
        maintenance = QHBoxLayout()
        maintenance.addWidget(button("Выгрузить", self.stop_requested))
        delete_button = button("Удалить модель", self.delete_requested)
        delete_button.setProperty("role", "danger")
        maintenance.addWidget(delete_button)
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
        cloud_form = form()
        cloud_form.addRow("Модель OCR", self.remote_model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API-ключ · системное хранилище")
        self.key.setAccessibleName("Новый API-ключ OpenRouter")
        cloud_form.addRow("API-ключ", self.key)
        cf.addLayout(cloud_form)
        self.show_key = QCheckBox("Показать введённый ключ")
        self.show_key.toggled.connect(lambda shown: self.key.setEchoMode(QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password))
        self.key.textChanged.connect(lambda text: self.show_key.setChecked(False) if not text else None)
        cf.addWidget(self.show_key)
        cf.addWidget(label("Выделенный снимок отправляется в OpenRouter. Запрос оплачивается по тарифу выбранной модели. Ключ хранится в системном хранилище; пустое поле сохраняет прежний ключ.", "notice"))
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
        oc.addWidget(label("После распознавания", "section"))
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
        tl.addWidget(label("ИЗ БУФЕРА В РЕДАКТОР", "eyebrow"))
        tl.addWidget(label("Печать в VDI", "title"))
        tl.addWidget(label("Поставьте курсор в редактор и нажмите сочетание печати.", "subtitle"))
        tl.addWidget(steps((("Скопируйте", "Поместите нужный текст в буфер."), ("Выберите окно", "Поставьте курсор в целевое поле."), ("Запустите", "Нажмите горячие клавиши печати."))))
        typing_card, tc = card()
        tc.addWidget(label("Ритм ввода", "section"))
        self.autoindent = QCheckBox("Компенсировать автоотступы")
        self.autoindent.setChecked(settings.autoindent)
        tc.addWidget(self.autoindent)
        tc.addWidget(label("Проверяйте этот режим в своём редакторе. Автозакрытие скобок и кавычек отключите в редакторе.", "muted"))
        tf = form()
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
        typing_help, th = card()
        th.addWidget(label("Остановка и переключение окна", "section"))
        self.typing_hint = label("", "muted")
        th.addWidget(self.typing_hint)
        th.addWidget(label("Кнопка внизу даёт 3 секунды, чтобы переключиться в редактор. Горячие клавиши запускают ввод в текущем окне.", "muted"))
        tl.addWidget(typing_help)
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
        sl.addWidget(label("ПОД ВАШ РАБОЧИЙ ПРОЦЕСС", "eyebrow"))
        sl.addLayout(system_header)
        windows = sys.platform == "win32"
        sl.addWidget(label("Доступ к системе и сочетания клавиш.", "subtitle"))
        permission_card, perms = card()
        perms.addWidget(label("Доступ к системе", "section"))
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
        perms.addWidget(label("Windows предоставляет доступ в текущем сеансе. Печать в окна с повышенными правами может быть недоступна."
                              if windows else "Кнопка запросит доступ у macOS. После разрешения статус обновится автоматически.", "muted"))
        sl.addWidget(permission_card)
        hotkeys_card, hk = card()
        hk.addWidget(label("Горячие клавиши", "section"))
        keyform = form()
        self.capture_hotkey = QLineEdit(settings.capture_hotkey)
        self.typing_hotkey = QLineEdit(settings.typing_hotkey)
        self.cancel_hotkey = QLineEdit(settings.cancel_hotkey)
        keyform.addRow("Распознавание", self.capture_hotkey)
        keyform.addRow("Печать", self.typing_hotkey)
        keyform.addRow("Остановка", self.cancel_hotkey)
        hk.addLayout(keyform)
        hk.addWidget(label("ctrl — Control · alt — Alt · shift — Shift · win — Windows"
                           if windows else "cmd — Command · alt — Option · ctrl — Control", "muted"))
        sl.addWidget(hotkeys_card)
        sl.addWidget(label("Изменения сочетаний применяются после сохранения. При конфликте с другим приложением выберите другое сочетание.", "notice"))
        sl.addStretch()
        add_page(pages, system)
        self.navigation.setCurrentRow(0)
        outer.addLayout(body, 1)
        footer = QFrame()
        footer.setProperty("role", "footer")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(24, 14, 24, 18)
        footer_layout.setSpacing(10)
        self.status = label("Подготовьте модель и выделите область", "muted")
        self.status.setAccessibleName("Состояние текущей операции")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        self.retry_button = button("Повторить OCR", self.retry_requested)
        self.retry_button.setToolTip("Распознать тот же снимок ещё раз с текущими настройками")
        self.retry_button.hide()
        status_row.addWidget(self.retry_button)
        footer_layout.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.hide()
        footer_layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.save_button = button("Сохранить", self.save_requested)
        buttons.addWidget(self.save_button)
        self.save_hint = label("", "muted")
        buttons.addWidget(self.save_hint)
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
        footer_layout.addLayout(buttons)
        outer.addWidget(footer)
        self.action_hint = label("", "muted")
        sidebar.addSpacing(16)
        sidebar.addWidget(self.action_hint)
        self._saved_values = None
        self._settings_fields = (self.provider, self.local_model, self.acceleration, self.remote_model,
                                 self.response_mode, self.review_local_model, self.review_remote_model,
                                 self.capture_hotkey, self.typing_hotkey, self.cancel_hotkey,
                                 self.delay, self.newline, self.tabs, self.autoindent, self.idle)
        for field in self._settings_fields:
            signal = (field.currentIndexChanged if isinstance(field, QComboBox) else
                      field.textChanged if isinstance(field, QLineEdit) else
                      field.toggled if isinstance(field, QCheckBox) else field.valueChanged)
            signal.connect(self.update_pending)
        self.key.textChanged.connect(self.update_pending)
        for field in (self.capture_hotkey, self.typing_hotkey, self.cancel_hotkey):
            field.textChanged.connect(self.update_shortcut_hints)
        self.mark_saved()
        self.update_shortcut_hints()

    def settings_values(self):
        return tuple(field.currentData() if isinstance(field, QComboBox) else
                     field.text() if isinstance(field, QLineEdit) else
                     field.isChecked() if isinstance(field, QCheckBox) else field.value()
                     for field in self._settings_fields)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'tray_hint'):
            self.tray_hint.setVisible(self.height() >= 700)

    def update_pending(self, *_):
        pending = self.settings_values() != self._saved_values or bool(self.key.text())
        self.save_hint.setText("Есть изменения" if pending else "Нет изменений")
        self.save_hint.setToolTip("Сохраните настройки. При запуске действия они также сохраняются автоматически." if pending else "Текущие значения сохранены")

    def mark_saved(self):
        self._saved_values = self.settings_values()
        self.update_pending()

    def update_shortcut_hints(self, *_):
        self.capture_button.setToolTip("Выделить область экрана · " + self.capture_hotkey.text())
        self.typing_button.setToolTip("Выберите целевой редактор в течение трёх секунд. Отмена: " + self.cancel_hotkey.text())
        self.typing_hint.setText("Остановить ввод: " + self.cancel_hotkey.text() + ". При смене активного окна печать останавливается.")
        self.action_hint.setText("Захват  " + self.capture_hotkey.text() + "\nВвод  " + self.typing_hotkey.text())

    def update_actions(self, *_):
        typing = self.navigation.currentRow() == 1
        self.typing_button.setVisible(typing)
        self.capture_button.setVisible(not typing)
        for index, title in enumerate(self.nav_titles):
            title.setProperty("active", index == self.navigation.currentRow())
            title.style().unpolish(title)
            title.style().polish(title)

    def set_busy(self, busy):
        self.retry_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.save_button.setEnabled(not busy)
        self.capture_button.setEnabled(not busy)
        self.typing_button.setEnabled(not busy)
        self.provider_card.setEnabled(not busy)
        self.diagnostics_button.setEnabled(not busy)
        for field in self._settings_fields:
            field.setEnabled(not busy)
        self.progress.setVisible(busy)
        if not busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

    def show_permissions(self, values):
        labels = {"accessibility": "Управление вводом" if sys.platform == "win32" else "Управление вводом (Accessibility)",
                  "screen": "Захват экрана", "input_monitoring": "Глобальные клавиши" if sys.platform == "win32" else "Мониторинг ввода"}
        for key, name in labels.items():
            allowed = values[key]
            self.permission_labels[key].setText(
                f"{name}\n"
                f"{'Предоставлено' if allowed else 'Не предоставлено'}")
            self.permission_buttons[key].setVisible(not allowed)
            self.permission_buttons[key].setEnabled(not allowed)

    def apply_to(self, settings):
        settings.provider = self.provider.currentData()
        settings.acceleration = self.acceleration.currentData()
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
