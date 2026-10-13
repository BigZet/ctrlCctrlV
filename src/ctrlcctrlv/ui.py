from pathlib import Path
from datetime import datetime
import sys
import json

from PySide6.QtCore import Qt, QRect, QSize, QPoint, QTimer, Signal, QSignalBlocker, QEvent
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen, QPixmap, QShortcut, QKeySequence, QSyntaxHighlighter, QTextCharFormat, QTextOption, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
    QWizard, QWizardPage, QTabWidget, QMenu, QWidgetAction, QFileDialog, QMessageBox, QToolTip, QTextEdit,
)

from .catalog import MODELS
from .theme import colours, editor_palette, stylesheet, GAP, PAGE_MARGIN, DIALOG_MARGIN, COMPACT_HEIGHT, fit_window_to_screen
from .fonts import monospace_font, prepare_fonts
from .model_picker import ModelPicker, DETAIL_ROLE
from .csv_editor import CsvEditor, csv_rows
from .capture_adjust import AdjustableSelectionMixin
from .capture_region import physical_rect
from .settings_controls import DelaySpinBox
from .result_search import SearchBar, find_spans, qt_offset


class SelectionOverlay(AdjustableSelectionMixin, QWidget):
    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, screen, snapshot, adjust=False, show_help=True):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Window)
        self.setWindowTitle("ctrlCctrlV · Выделение области")
        self.snapshot = snapshot
        self.origin = self.position = None
        self.init_capture_adjustment(adjust)
        self.show_help = bool(show_help)
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
        help_text = (self.adjustment_help_text() if self.capture_adjust else
                     "Выделите область · Esc или правая кнопка — отмена")
        help_width = painter.fontMetrics().horizontalAdvance(help_text) + 32
        help_rect = QRect((self.width() - help_width) // 2, 20, help_width, 40)
        if not self.capture_adjust and not self.show_help:
            help_rect = QRect()
        if not help_rect.isEmpty() and not area.intersects(help_rect):
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
        self.paint_adjustment(painter)

    def mousePressEvent(self, event):
        if self.handle_adjust_press(event):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = self.position = event.position().toPoint()
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()

    def mouseMoveEvent(self, event):
        if self.handle_adjust_move(event):
            return
        if self.origin is not None:
            self.position = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.handle_adjust_release(event):
            return
        if event.button() != Qt.MouseButton.LeftButton or self.origin is None:
            return
        self.position = event.position().toPoint()
        area = self.selection().intersected(self.rect())
        if area.width() < 3 or area.height() < 3:
            self.cancelled.emit()
            return
        ratio = self.snapshot.devicePixelRatio()
        physical = physical_rect(area, ratio)
        image = self.snapshot.copy(physical).toImage()
        image.setDevicePixelRatio(1)
        self.selected.emit(image)

    def keyPressEvent(self, event):
        if self.handle_adjust_key(event):
            return
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

    def showEvent(self, event):
        super().showEvent(event)
        fit_window_to_screen(self)


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
    if isinstance(pages, QTabWidget):
        pages.addTab(scroll, "")
    else:
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
                if block.blockNumber() in editor.changed_lines:
                    painter.setPen(QColor(c['muted']))
                    painter.drawText(1, top, 12, editor.fontMetrics().height(), Qt.AlignmentFlag.AlignLeft,
                                     editor.changed_lines[block.blockNumber()])
                if editor.syntax_hint and editor.syntax_hint.line == block.blockNumber() + 1:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(c['warning']))
                    painter.drawPolygon([QPoint(3, top + 12), QPoint(11, top + 12), QPoint(7, top + 4)])
            top += height
            block = block.next()

    def mousePressEvent(self, event):
        hint = self.editor.syntax_hint
        if hint and hint.line:
            block = self.editor.document().findBlockByNumber(hint.line - 1)
            top = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
            if top <= event.position().y() < top + self.editor.blockBoundingRect(block).height():
                cursor = self.editor.textCursor()
                cursor.setPosition(block.position())
                self.editor.setTextCursor(cursor)
                self.editor.setFocus()
                QToolTip.showText(self.mapToGlobal(event.position().toPoint()), hint.label, self)
                return
        super().mousePressEvent(event)


class CodeTextEdit(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.syntax_hint = None
        self.changed_lines = {}
        self._base_selections = []
        self._search_selections = []
        self.gutter = LineNumbers(self)
        self.gutter.setAccessibleName("Номера строк и подсказки синтаксиса")
        self.blockCountChanged.connect(self.update_gutter)
        self.updateRequest.connect(self.update_gutter)
        self.cursorPositionChanged.connect(self.gutter.update)
        self.update_gutter()

    def setExtraSelections(self, selections):
        self._base_selections = list(selections)
        super().setExtraSelections(self._base_selections + self._search_selections)

    def set_search_selections(self, selections):
        self._search_selections = list(selections)
        super().setExtraSelections(self._base_selections + self._search_selections)

    def set_syntax_hint(self, hint):
        self.syntax_hint = hint
        self.gutter.setToolTip(hint.label if hint else "")
        self.gutter.update()

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
    zoom_changed = Signal(float, bool)
    def __init__(self):
        super().__init__()
        self.original_pixmap = QPixmap()
        self.fit = True
        self.zoom = 100
        self.actual_zoom = 100.0
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

    def set_zoom(self, percent):
        self.zoom = max(25, min(400, percent))
        self.fit = False
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
        elif self.zoom != 100:
            pixmap = pixmap.scaled(pixmap.size() * (self.zoom / 100), Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
        self.image.setPixmap(pixmap)
        self.image.adjustSize()
        self.actual_zoom = pixmap.width() / self.original_pixmap.width() * 100
        self.zoom_changed.emit(self.actual_zoom, self.fit)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_image()


class Preview(SystemTheme, QMainWindow):
    copy_requested = Signal(str)
    dismissed = Signal()
    review_requested = Signal()
    review_cancel_requested = Signal()
    accept_review_requested = Signal(object)
    review_invalidated = Signal()
    syntax_requested = Signal(str, str, int)
    format_requested = Signal(str, str, int)

    def __init__(self):
        prepare_fonts()
        super().__init__()
        self.original_text = ""
        self.ui_state = None
        self.history_mode = False
        self.copied_text = None
        self.format_mode = "auto"
        self.result_view = "auto"
        self.table_candidate = False
        self.is_csv = False
        self.source_image = None
        self.review_result = None
        self.full_review_result = None
        self.review_choices = []
        self.raw_response = None
        self.search_matches = []
        self.search_index = -1
        self.review_change_index = -1
        self.comparison_orientation_override = None
        self._arranging_comparison = False
        self._syntax_status_state = None
        self._review_error_detail = None
        self._review_error_previous = None
        self.syntax_revision = 0
        self.syntax_timer = QTimer(self)
        self.syntax_timer.setSingleShot(True)
        self.syntax_timer.setInterval(250)
        self.syntax_timer.timeout.connect(lambda: self.syntax_requested.emit(
            self.text.toPlainText(), self.syntax_language.currentData(), self.syntax_revision))
        self.setWindowTitle("ctrlCctrlV · Результат")
        self.resize(1180, 780)
        self.setMinimumSize(640, 380)
        self.init_theme()
        panel = QWidget()
        self.setCentralWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(5)
        heading.addWidget(label("Результат OCR", "title"))
        header.addLayout(heading, 1)
        self.copy_button = button("Копировать и закрыть", self.copy_result, True)
        self.copy_button.setToolTip("Копировать весь текст, включая ручные правки")
        header.addWidget(self.copy_button)
        self.copy_shortcut = QShortcut(QKeySequence("Meta+Shift+C" if sys.platform == "darwin" else "Ctrl+Shift+C"), self)
        self.copy_shortcut.activated.connect(self.copy_button.click)
        layout.addLayout(header)
        self.status = label("", "muted")
        self.status.setWordWrap(False)
        self.status.setFixedHeight(self.status.fontMetrics().height() + 6)
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.status)
        self.review_error_button = QToolButton()
        self.review_error_button.setText("Подробности")
        self.review_error_button.setAccessibleName("Полные подробности ошибки уточнения")
        self.review_error_button.setFixedHeight(COMPACT_HEIGHT)
        self.review_error_button.clicked.connect(self.show_review_error_details)
        self.review_error_button.hide()
        header.insertWidget(1, self.review_error_button)
        self.review_error_dialog = QDialog(self)
        self.review_error_dialog.setWindowTitle("Подробности уточнения")
        self.review_error_dialog.resize(640, 320)
        details_layout = QVBoxLayout(self.review_error_dialog)
        self.review_error_text = QPlainTextEdit()
        self.review_error_text.setReadOnly(True)
        self.review_error_text.setAccessibleName("Полный текст ошибки уточнения")
        details_layout.addWidget(self.review_error_text, 1)
        details_layout.addWidget(button("Закрыть", self.review_error_dialog.close))
        split = QSplitter()
        self.preview_split = split
        split.splitterMoved.connect(self.remember_splitter)
        split.setHandleWidth(12)
        split.setChildrenCollapsible(False)
        self.image_view = ImageView()
        self.image = self.image_view.image
        image_panel = QFrame()
        self.image_panel = image_panel
        image_panel.setProperty("role", "workspace")
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(14, 12, 14, 12)
        image_layout.setSpacing(8)
        image_header = QHBoxLayout()
        image_heading = label("Снимок", "section")
        image_heading.setMinimumHeight(38)
        image_header.addWidget(image_heading)
        image_header.addStretch()
        image_layout.addLayout(image_header)
        image_tools = QHBoxLayout()
        image_tools.setSpacing(8)
        self.fit_image = QToolButton()
        self.fit_image.setProperty("role", "viewToggle")
        self.fit_image.setText("Вписать")
        self.fit_image.setCheckable(True)
        self.fit_image.setAccessibleName("Вписать исходный снимок в окно")
        self.fit_image.setToolTip("Показать снимок целиком. Нажмите ещё раз для исходного масштаба.")
        self.fit_image.toggled.connect(self.image_view.set_fit)
        self.fit_image.setChecked(True)
        image_tools.addWidget(self.fit_image)
        self.image_zoom = QSpinBox()
        self.image_zoom.setRange(25, 400)
        self.image_zoom.setSingleStep(25)
        self.image_zoom.setSuffix(" %")
        self.image_zoom.setValue(100)
        self.image_zoom.setAccessibleName("Масштаб снимка")
        self.image_zoom.valueChanged.connect(self.change_image_zoom)
        image_tools.addWidget(self.image_zoom)
        self.actual_size = QToolButton()
        self.actual_size.setText("100%")
        self.actual_size.clicked.connect(lambda: self.change_image_zoom(100))
        image_tools.addWidget(self.actual_size)
        image_tools.addStretch()
        for control in (self.fit_image, self.image_zoom, self.actual_size):
            control.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        image_layout.addLayout(image_tools)
        image_layout.addWidget(self.image_view, 1)
        self.image_details = label("Исходная область экрана", "muted")
        self.image_details.hide()
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
        self.result_heading = label("Текст", "section")
        format_row.addWidget(self.result_heading)
        self.result_mode = QComboBox()
        for title, mode in (("Авто", "auto"), ("Текст", "text"), ("Таблица", "table")):
            self.result_mode.addItem(title, mode)
        self.result_mode.setAccessibleName("Представление результата")
        self.result_mode.currentIndexChanged.connect(lambda: self.set_result_view(self.result_mode.currentData()))
        format_row.addWidget(self.result_mode)
        format_row.addStretch()
        self.font_size = QComboBox()
        for size in (12, 14, 16, 18, 20, 24):
            self.font_size.addItem(f"{size} px", size)
        self.font_size.setAccessibleName("Размер шрифта распознанного текста")
        self.font_size.setToolTip("Размер текста в редакторе. Содержимое буфера не меняется.")
        self.font_size.setCurrentIndex(1)
        self.font_size.currentIndexChanged.connect(self.update_editor_font)
        self.update_editor_font()
        self.syntax_language = QComboBox()
        for name, value in (("Язык: авто", "auto"), ("Python", "python"), ("SQL", "sql"), ("Java", "java"), ("JSON", "json"), ("Без подсветки", "none")):
            self.syntax_language.addItem(name, value)
        self.syntax_language.setAccessibleName("Язык проверки синтаксиса")
        self.syntax_language.setToolTip("Проверка синтаксиса — подсказка, не проверка точности OCR. Неполный фрагмент может содержать ошибку разбора.")
        self.fit_image.setFixedHeight(self.syntax_language.sizeHint().height())
        self.syntax_language.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        format_row.addWidget(self.syntax_language)
        action_row = QHBoxLayout()
        self.format_button = button("Форматировать", lambda: self.format_requested.emit(self.text.toPlainText(), self.syntax_language.currentData(), self.syntax_revision))
        self.format_button.setToolTip("Отформатировать Python, SQL, Java или JSON. Правки можно отменить Ctrl+Z; буфер обновляется кнопкой копирования.")
        action_row.addWidget(self.format_button)
        self.save_file_button = button("Сохранить в…", self.save_current_text)
        action_row.addWidget(self.save_file_button)
        self.export_csv_button = button("Экспорт в CSV", lambda: self.export_current_table("csv"))
        self.export_xlsx_button = button("Экспорт в XLSX", lambda: self.export_current_table("xlsx"))
        for action in (self.export_csv_button, self.export_xlsx_button):
            action.hide()
            action_row.addWidget(action)
        action_row.addStretch()
        for control in (self.format_button, self.save_file_button, self.export_csv_button, self.export_xlsx_button):
            control.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        from .highlighting import CodeHighlighter
        self.code_highlighter = CodeHighlighter(self.text.document())
        text_layout.addLayout(format_row)
        text_layout.addLayout(action_row)
        self.table = CsvEditor()
        self.table.csv_changed.connect(self.apply_table_edit)
        self.result_stack = QStackedWidget()
        self.result_stack.addWidget(self.text)
        self.result_stack.addWidget(self.table)
        self.search_bar = SearchBar()
        self.search_bar.hide()
        self.search_bar.query_changed.connect(self.update_search)
        self.search_bar.step_requested.connect(self.step_search)
        self.search_bar.closed.connect(self.close_search)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(120)
        self.search_timer.timeout.connect(lambda: self.update_search(navigate=False))
        self.text.textChanged.connect(self.search_timer.start)
        self.find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.find_shortcut.activated.connect(self.open_search)
        text_layout.addWidget(self.search_bar)
        text_layout.addWidget(self.result_stack, 1)
        self.table_undo = QShortcut(QKeySequence.StandardKey.Undo, self.table)
        self.table_undo.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.table_undo.activated.connect(self.text.undo)
        self.table_redo = QShortcut(QKeySequence.StandardKey.Redo, self.table)
        self.table_redo.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.table_redo.activated.connect(self.text.redo)
        editor_tools = QHBoxLayout()
        self.whitespace = view_toggle("Пробелы", "Показать пробелы и табуляцию", self.show_whitespace)
        self.whitespace.setToolTip("Показать пробелы и Tab, не изменяя текст")
        self.wrap_text = view_toggle("Перенос", "Переносить длинные строки на экране", lambda enabled:
                                     self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if enabled else QPlainTextEdit.LineWrapMode.NoWrap))
        self.wrap_text.setToolTip("Перенос только на экране. Содержимое буфера сохраняет исходные строки.")
        self.whitespace.hide()
        self.wrap_text.hide()
        self.editor_view = QToolButton()
        self.editor_view.setText("Вид")
        self.editor_view.setProperty("role", "viewToggle")
        self.editor_view.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.editor_view.setAccessibleName("Вид редактора")
        view_menu = QMenu(self.editor_view)
        view_panel = QWidget()
        view_layout = QVBoxLayout(view_panel)
        view_layout.setContentsMargins(16, 12, 16, 12)
        view_layout.addWidget(label("Размер текста", "section"))
        view_layout.addWidget(self.font_size)
        view_action = QWidgetAction(view_menu)
        view_action.setDefaultWidget(view_panel)
        view_menu.addAction(view_action)
        for toggle in (self.whitespace, self.wrap_text):
            action = view_menu.addAction(toggle.text())
            action.setCheckable(True)
            action.setToolTip(toggle.toolTip())
            action.toggled.connect(toggle.setChecked)
            toggle.toggled.connect(action.setChecked)
        self.image_visible_action = view_menu.addAction("Показать снимок")
        self.image_visible_action.setCheckable(True)
        self.image_visible_action.setChecked(True)
        self.image_visible_action.toggled.connect(self.set_image_visible)
        view_menu.addSeparator()
        self.restore_action = view_menu.addAction("Вернуть исходный текст", self.restore_original)
        self.restore_action.setEnabled(False)
        self.raw_action = view_menu.addAction("Исходный ответ OCR…", self.show_raw_response)
        self.raw_action.setVisible(False)
        self.raw_dialog = QDialog(self)
        self.raw_dialog.setWindowTitle("Исходный ответ OCR")
        self.raw_dialog.resize(680, 440)
        raw_layout = QVBoxLayout(self.raw_dialog)
        raw_layout.setContentsMargins(16,16,16,16)
        self.raw_editor = CodeTextEdit()
        self.raw_editor.setReadOnly(True)
        self.raw_editor.setFont(monospace_font())
        raw_layout.addWidget(self.raw_editor, 1)
        raw_buttons = QHBoxLayout()
        raw_buttons.addWidget(button("Закрыть", self.raw_dialog.close))
        raw_buttons.addStretch()
        raw_buttons.addWidget(button("Использовать этот текст", self.use_raw_response, True))
        raw_layout.addLayout(raw_buttons)
        self.raw_dialog.finished.connect(self.raw_editor.clear)
        self.editor_view.setMenu(view_menu)
        editor_tools.addWidget(self.editor_view)
        self.table_heading = QCheckBox("Первая строка — заголовок")
        self.table_heading.setToolTip("Выделить первую строку. Она остаётся редактируемой и входит в экспорт.")
        self.table_heading.toggled.connect(self.table.set_first_row_heading)
        self.table_heading.hide()
        editor_tools.addWidget(self.table_heading)
        editor_tools.addStretch()
        self.text_stats = label("", "muted")
        editor_tools.addWidget(self.text_stats)
        text_layout.addLayout(editor_tools)
        self.copy_state = label("", "muted")
        self.copy_state.hide()
        self.syntax_message = label("", "muted")
        self.syntax_message.setToolTip("Сверьте этот участок со снимком. Код не исполняется. Для неполного фрагмента можно выбрать «Без проверки».")
        self.syntax_message.hide()
        split.addWidget(image_panel)
        split.addWidget(text_panel)
        split.setSizes([440, 560])
        layout.addWidget(split, 1)
        self.review_panel = QDialog(self)
        self.review_panel.setWindowTitle("Проверка текста")
        self.review_panel.setWindowModality(Qt.WindowModality.WindowModal)
        self.review_panel.resize(1180, 680)
        self.review_panel.setMinimumSize(680, 420)
        self.review_panel.installEventFilter(self)
        proposed = QVBoxLayout(self.review_panel)
        proposed.setContentsMargins(24, 24, 24, 20)
        proposed.setSpacing(16)
        proposed.addWidget(label("Предлагаемые исправления", "title"))
        self.review_panel.rejected.connect(self.reject_review)
        self.review_choice_toggle = QToolButton()
        self.review_choice_toggle.setText('Изменения')
        self.review_choice_toggle.setCheckable(True)
        self.review_choice_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.review_choice_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.review_choice_toggle.toggled.connect(self.arrange_comparison)
        proposed.addWidget(self.review_choice_toggle)
        self.review_choice_toggle.hide()
        changes = QSplitter()
        self.review_changes_split = changes
        choices = QWidget()
        self.choices_layout = QVBoxLayout(choices)
        self.choices_layout.setContentsMargins(0, 0, 12, 0)
        self.choices_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        choice_scroll = QScrollArea()
        self.review_choice_scroll = choice_scroll
        choice_scroll.setWidgetResizable(True)
        choice_scroll.setWidget(choices)
        choices.setAutoFillBackground(False)
        choice_scroll.viewport().setAutoFillBackground(False)
        choice_scroll.setMinimumWidth(180)
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
        self.diff.setMinimumHeight(220)
        comparison = QWidget()
        comparison_layout = QVBoxLayout(comparison)
        comparison_layout.setContentsMargins(0, 0, 0, 0)
        self.comparison_split = QSplitter()
        self.comparison_split.installEventFilter(self)
        self.review_changes_split.splitterMoved.connect(self.arrange_comparison)
        self.before_text, self.after_text = CodeTextEdit(), CodeTextEdit()
        for title, editor in (("Было", self.before_text), ("Стало", self.after_text)):
            column = QWidget()
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.addWidget(label(title, "section"))
            editor.setReadOnly(True)
            editor.setFont(monospace_font())
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            editor.setAccessibleName(title + " · результат уточнения")
            column_layout.addWidget(editor, 1)
            self.comparison_split.addWidget(column)
        from .comparison import synchronize_scroll
        synchronize_scroll(self.before_text, self.after_text)
        comparison_layout.addWidget(self.comparison_split, 1)
        self.technical_diff = QCheckBox("Технический diff")
        self.technical_diff.toggled.connect(self.diff.setVisible)
        comparison_layout.addWidget(self.technical_diff)
        self.diff.setMinimumHeight(100)
        self.diff.setMaximumHeight(200)
        self.diff.hide()
        comparison_layout.addWidget(self.diff)
        changes.addWidget(comparison)
        changes.setSizes([240, 900])
        proposed.addWidget(changes, 1)
        self.review_footer = QWidget()
        self.review_footer.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Fixed)
        decision = QHBoxLayout(self.review_footer)
        self.review_decision_layout = decision
        decision.setContentsMargins(0,0,0,0)
        decision.setSpacing(GAP)
        self.review_navigation = QWidget()
        navigation = QHBoxLayout(self.review_navigation)
        navigation.setContentsMargins(0,0,0,0)
        navigation.setSpacing(GAP)
        navigation.addWidget(button("Предыдущее", lambda: self.step_review_change(-1)))
        navigation.addWidget(button("Следующее", lambda: self.step_review_change(1)))
        self.comparison_orientation = QToolButton()
        self.comparison_orientation.setText("Расположение")
        self.comparison_orientation.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        orientation_menu = QMenu(self.comparison_orientation)
        orientation_menu.addAction("Рядом", lambda: self.set_comparison_orientation(Qt.Orientation.Horizontal))
        orientation_menu.addAction("Друг над другом", lambda: self.set_comparison_orientation(Qt.Orientation.Vertical))
        self.comparison_orientation.setMenu(orientation_menu)
        navigation.addWidget(self.comparison_orientation)
        navigation.addStretch()
        decision.addWidget(self.review_navigation)
        self.review_decisions = QWidget()
        actions = QHBoxLayout(self.review_decisions)
        actions.setContentsMargins(0,0,0,0)
        actions.setSpacing(GAP)
        actions.addWidget(button("Отклонить", self.reject_review))
        actions.addStretch()
        self.accept_button = button("Применить и скопировать", lambda: self.accept_review_requested.emit(self.review_result), True)
        actions.addWidget(self.accept_button)
        decision.addWidget(self.review_decisions,1)
        proposed.addWidget(self.review_footer)
        self.review_panel.hide()
        context_body = QWidget()
        self.context_body = context_body
        context_layout = QVBoxLayout(context_body)
        context_layout.setContentsMargins(0, 0, 0, 0)
        self.review_context = QPlainTextEdit()
        self.review_context.setMaximumHeight(80)
        self.review_context.setPlaceholderText("Например: сверь цифры, сохрани отступы и пустые строки")
        self.review_context.setAccessibleName("Дополнительный промпт для уточнения")
        context_layout.addWidget(self.review_context)
        self.review_context.setToolTip("До 8192 символов. Отправляется только при проверке текста.")
        self.review_context.textChanged.connect(self.invalidate_review)
        self.context_toggle = disclosure("Дополнительный промпт", context_body)
        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.refine_label = label("Модель уточнения", "section")
        footer.addWidget(self.refine_label)
        self.refine_model = ModelPicker()
        self.refine_model.setMinimumWidth(220)
        self.refine_model.setAccessibleName("Модель для уточнения результата")
        self.refine_model.setToolTip("Повторно прочитать снимок с текущим текстом и дополнительным промптом")
        footer.addWidget(self.refine_model, 1)
        self.review_button = button("Уточнить", self.review_requested)
        self.review_button.setToolTip("Перечитать снимок выбранной моделью и предложить исправления")
        footer.addWidget(self.review_button)
        self.review_cancel_button = button("Остановить", self.review_cancel_requested)
        self.review_cancel_button.hide()
        footer.addWidget(self.review_cancel_button)
        layout.addLayout(footer)
        review_actions = QHBoxLayout()
        review_actions.addWidget(self.context_toggle)
        self.restore_button = button("Вернуть исходный текст", self.restore_original)
        self.restore_button.hide()
        review_actions.addStretch()
        layout.addLayout(review_actions)
        layout.addWidget(context_body)
        self.text.textChanged.connect(self.invalidate_review)
        self.text.textChanged.connect(self.schedule_syntax)
        self.text.textChanged.connect(self.update_text_details)
        self.text.textChanged.connect(self.refresh_table)
        self.text.cursorPositionChanged.connect(self.update_text_details)
        self.syntax_language.currentIndexChanged.connect(self.schedule_syntax)
        self.update_text_details()
        self.format_shortcut = QShortcut(QKeySequence("Meta+Shift+F" if sys.platform == "darwin" else "Ctrl+Shift+F"), self)
        self.format_shortcut.activated.connect(self.format_button.click)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self.save_current_result)
        self.table_save_menu = QMenu(self)
        self.table_save_menu.addAction("Сохранить CSV…", lambda: self.export_current_table("csv"))
        self.table_save_menu.addAction("Сохранить XLSX…", lambda: self.export_current_table("xlsx"))
        # One result toolbar frees the editor from stacked action rows.
        layout.setContentsMargins(PAGE_MARGIN, DIALOG_MARGIN, PAGE_MARGIN, DIALOG_MARGIN)
        layout.setSpacing(GAP)
        layout.removeWidget(self.status)
        header.insertWidget(1, self.status, 1)
        heading.itemAt(0).widget().setProperty('role', 'section')
        toolbar = QHBoxLayout()
        toolbar.setSpacing(GAP)
        for control in (self.editor_view, self.result_mode, self.syntax_language, self.format_button,
                        self.save_file_button, self.export_csv_button, self.export_xlsx_button):
            toolbar.addWidget(control)
        toolbar.addStretch()
        self.show_image_button = QToolButton()
        self.show_image_button.setText('Показать снимок')
        self.show_image_button.clicked.connect(lambda: self.image_visible_action.setChecked(True))
        toolbar.addWidget(self.show_image_button)
        self.show_image_button.hide()
        layout.insertLayout(1, toolbar)
        text_layout.removeItem(action_row)
        # Keep the image and result starting on the same baseline.
        image_layout.removeItem(image_header)
        image_heading.hide()
        format_row.setContentsMargins(0,0,0,0)
        self.result_heading.setMinimumHeight(COMPACT_HEIGHT)
        self.result_heading.setMaximumHeight(COMPACT_HEIGHT)
        image_layout.setContentsMargins(12,GAP//2,12,8)
        text_layout.setContentsMargins(12,GAP//2,12,8)
        image_tools.setSpacing(4)
        image_tools.removeWidget(self.fit_image)
        self.zoom_out = QToolButton()
        self.zoom_out.setText('−')
        self.zoom_out.setAccessibleName('Уменьшить снимок')
        self.zoom_out.clicked.connect(lambda: self.change_image_zoom(self.image_view.actual_zoom - 25))
        self.zoom_in = QToolButton()
        self.zoom_in.setText('+')
        self.zoom_in.setAccessibleName('Увеличить снимок')
        self.zoom_in.clicked.connect(lambda: self.change_image_zoom(self.image_view.actual_zoom + 25))
        image_tools.insertWidget(0,self.zoom_out)
        image_tools.insertWidget(2,self.zoom_in)
        image_tools.insertWidget(4,self.fit_image)
        # NoButtons plus Qt's styled spin subcontrols collapses the edit field to 1px.
        self.image_zoom.setStyleSheet('QSpinBox { padding: 2px 6px; } QSpinBox::up-button, QSpinBox::down-button { width: 0; height: 0; } QSpinBox QLineEdit { padding: 0; border: none; }')
        self.image_zoom.setMinimumWidth(self.image_zoom.sizeHint().width())
        self.image_view.zoom_changed.connect(self.reflect_image_zoom)
        self.hide_image_button = QToolButton()
        self.hide_image_button.setText('Скрыть')
        self.hide_image_button.setToolTip('Скрыть снимок')
        self.hide_image_button.setAccessibleName('Скрыть снимок')
        self.hide_image_button.clicked.connect(lambda: self.image_visible_action.setChecked(False))
        image_tools.addWidget(self.hide_image_button)
        self.table_heading.hide()
        self.table_heading_action = view_menu.addAction('Первая строка — заголовок')
        self.table_heading_action.setCheckable(True)
        self.table_heading_action.toggled.connect(self.table_heading.setChecked)
        self.table_heading.toggled.connect(self.table_heading_action.setChecked)
        self.table_heading_action.setVisible(False)
        self.table.currentCellChanged.connect(self.update_table_details)
        self.table.itemSelectionChanged.connect(self.update_table_details)
        for control in (self.copy_button, self.result_mode, self.syntax_language, self.format_button,
                        self.save_file_button, self.export_csv_button, self.export_xlsx_button,
                        self.editor_view, self.image_zoom, self.actual_size, self.fit_image,
                        self.zoom_in,self.zoom_out,self.hide_image_button,self.show_image_button,
                        self.review_button,self.review_cancel_button):
            control.setMinimumHeight(COMPACT_HEIGHT)
            control.setMaximumHeight(COMPACT_HEIGHT)
            control.setProperty('role', 'compact' if control is not self.copy_button else 'primary')
        self.copy_button.setStyleSheet('min-height: 32px; padding-top: 0; padding-bottom: 0;')
        self.syntax_language.setMinimumWidth(100)
        self.syntax_language.setMaximumWidth(170)
        self.syntax_language.setItemText(0,'Язык: авто')
        self.syntax_language.setItemText(self.syntax_language.count()-1,'Без проверки')
        self.result_mode.setFixedWidth(self.result_mode.sizeHint().width())
        self.save_file_button.setText('Сохранить…')
        self.export_csv_button.setText('CSV…')
        self.export_xlsx_button.setText('XLSX…')
        self.refine_label.hide()
        self.context_toggle.setText('Промпт')
        review_actions.removeWidget(self.context_toggle)
        footer.insertWidget(0,self.context_toggle)
        layout.removeItem(review_actions)
        self.refine_model.setMinimumWidth(160)
        self.review_context.setStyleSheet('padding: 4px;')
        self.review_context.setFixedHeight(68)
        prompt_row = QHBoxLayout()
        prompt_row.addWidget(self.review_context,1)
        prompt_row.addWidget(button('Развернуть',self.expand_prompt))
        context_layout.removeWidget(self.review_context)
        context_layout.addLayout(prompt_row)
        self.prompt_dialog = QDialog(self)
        self.prompt_dialog.setWindowTitle('Дополнительный промпт')
        self.prompt_dialog.resize(640,400)
        prompt_layout = QVBoxLayout(self.prompt_dialog)
        prompt_layout.setContentsMargins(DIALOG_MARGIN,DIALOG_MARGIN,DIALOG_MARGIN,DIALOG_MARGIN)
        self.prompt_editor = QPlainTextEdit()
        self.prompt_editor.setAccessibleName('Полный дополнительный промпт')
        prompt_layout.addWidget(self.prompt_editor,1)
        prompt_buttons = QHBoxLayout()
        prompt_buttons.addWidget(button('Отмена',self.prompt_dialog.reject))
        prompt_buttons.addStretch()
        prompt_buttons.addWidget(button('Применить',self.apply_prompt,True))
        prompt_layout.addLayout(prompt_buttons)
        proposed.setContentsMargins(DIALOG_MARGIN,DIALOG_MARGIN,DIALOG_MARGIN,DIALOG_MARGIN)
        proposed.setSpacing(GAP)
        self.review_panel.setMinimumSize(600,380)
        self.update_editor_font()
        fit_window_to_screen(self)

    def reflect_image_zoom(self, percent, fit):
        with QSignalBlocker(self.image_zoom), QSignalBlocker(self.fit_image):
            self.image_zoom.setRange(1 if fit else 25,400)
            self.image_zoom.setValue(round(percent))
            self.fit_image.setChecked(fit)
        self.image_zoom.setToolTip(f'{percent:.1f}% · Вписать' if fit else f'{percent:.0f}%')

    def expand_prompt(self):
        self.prompt_editor.setPlainText(self.review_context.toPlainText())
        fit_window_to_screen(self.prompt_dialog)
        self.prompt_dialog.show()

    def apply_prompt(self):
        self.review_context.setPlainText(self.prompt_editor.toPlainText())
        self.prompt_dialog.accept()

    def update_table_details(self, *_):
        if self.is_csv:
            indexes = self.table.selectedIndexes()
            selection = (f' · {len(indexes)} ячеек' if len(indexes)>1 else
                         f' · {self.table.currentRow()+1}:{self.table.currentColumn()+1}' if self.table.currentRow()>=0 else '')
            self.text_stats.setText(f'{self.table.rowCount()} × {self.table.columnCount()}{selection}')
            self.text_stats.setToolTip('')

    def set_ui_state(self, state):
        self.ui_state = state
        self.preview_split.setSizes(state.get("preview_sizes", [440, 560]))
        self.image_visible_action.setChecked(state.get("image_visible", True))
        self.set_image_visible(self.image_visible_action.isChecked())
        if hasattr(self.refine_model, 'set_ui_state'):
            self.refine_model.set_ui_state(state)

    def open_search(self):
        self.commit_table_edit()
        self.search_bar.show()
        self.update_search()
        self.search_bar.query.setFocus()
        self.search_bar.query.selectAll()

    def close_search(self):
        self.search_bar.hide()
        self.text.set_search_selections([])
        self.table.show_search_cells([])
        (self.table if self.is_csv else self.text).setFocus()

    def update_search(self, *_args, navigate=True):
        if not self.search_bar.isVisible():
            return
        query = self.search_bar.query.text()
        if self.is_csv:
            self.search_matches = [(r,c,start,end) for r in range(self.table.rowCount()) for c in range(self.table.columnCount())
                                   if self.table.item(r,c) for start,end in find_spans(self.table.item(r,c).text(),query)]
            self.table.show_search_matches(self.search_matches)
            self.text.set_search_selections([])
        else:
            text = self.text.toPlainText()
            self.search_matches = find_spans(text, query)
            selections = []
            dark = QApplication.palette().window().color().lightness() < 128
            for start,end in self.search_matches:
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(self.text.document())
                selection.cursor.setPosition(qt_offset(text,start))
                selection.cursor.setPosition(qt_offset(text,end), QTextCursor.MoveMode.KeepAnchor)
                selection.format.setBackground(QColor('#65532a' if dark else '#fff0b3'))
                selections.append(selection)
            self.text.set_search_selections(selections)
            self.table.show_search_cells([])
        self.search_index = (0 if navigate else max(0,min(self.search_index,len(self.search_matches)-1))) if self.search_matches else -1
        self.search_bar.counter.setText(f"{self.search_index+1} / {len(self.search_matches)}")
        if self.is_csv:
            self.table.active_search_match = self.search_matches[self.search_index] if self.search_index >= 0 else None
        if navigate:
            self.focus_search_match()

    def focus_search_match(self):
        if self.search_index < 0:
            return
        if self.is_csv:
            self.table.focus_search_match(self.search_matches[self.search_index])
        else:
            start,end = self.search_matches[self.search_index]
            text = self.text.toPlainText()
            cursor = self.text.textCursor()
            cursor.setPosition(qt_offset(text,start))
            cursor.setPosition(qt_offset(text,end), QTextCursor.MoveMode.KeepAnchor)
            self.text.setTextCursor(cursor)
            self.text.ensureCursorVisible()

    def step_search(self, direction):
        if self.search_matches:
            self.search_index = (self.search_index + direction) % len(self.search_matches)
            self.search_bar.counter.setText(f"{self.search_index+1} / {len(self.search_matches)}")
            self.focus_search_match()

    def set_raw_response(self, text):
        self.raw_response = text
        self.raw_editor.clear()
        self.raw_dialog.hide()
        self.raw_action.setVisible(text is not None)

    def show_raw_response(self):
        if self.raw_response is not None:
            self.raw_editor.setPlainText(self.raw_response)
            self.raw_dialog.show()
            fit_window_to_screen(self.raw_dialog)
            self.raw_dialog.raise_()

    def use_raw_response(self):
        if self.raw_response is None:
            return
        self.commit_table_edit()
        self.set_result_view('text')
        self.replace_text(self.raw_response)
        self.raw_dialog.close()
        self.status.setText("Исходный ответ применён")

    def eventFilter(self, watched, event):
        if watched in (self.review_panel,getattr(self,'comparison_split',None)) and event.type() == QEvent.Type.Resize:
            self.arrange_comparison()
        return super().eventFilter(watched, event)

    def set_comparison_orientation(self, orientation):
        self.comparison_orientation_override = orientation
        self.arrange_comparison()

    def arrange_comparison(self, *_):
        if self._arranging_comparison:
            return
        self._arranging_comparison = True
        try:
            margins = self.review_panel.layout().contentsMargins()
            footer_width = self.review_panel.width()-margins.left()-margins.right()
            required_width = (self.review_navigation.sizeHint().width()+
                              self.review_decisions.sizeHint().width()+self.review_decision_layout.spacing())
            self.review_decision_layout.setDirection(
                QHBoxLayout.Direction.TopToBottom if footer_width < required_width
                else QHBoxLayout.Direction.LeftToRight)
            self.review_footer.updateGeometry()
            self.review_panel.layout().activate()
            narrow = self.review_panel.width() < 900
            self.review_changes_split.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
            self.review_choice_scroll.setMaximumHeight(92 if narrow else 16777215)
            self.review_choice_toggle.setVisible(narrow)
            self.review_choice_scroll.setVisible(not narrow or self.review_choice_toggle.isChecked())
            self.review_choice_toggle.setArrowType(Qt.ArrowType.DownArrow if self.review_choice_toggle.isChecked() else Qt.ArrowType.RightArrow)
            # Actual available pane width also changes when the sidebar is dragged.
            pane_width = (self.comparison_split.width()-self.comparison_split.handleWidth()) / 2
            orientation = self.comparison_orientation_override or (
                Qt.Orientation.Vertical if narrow or pane_width < 300 else Qt.Orientation.Horizontal)
            if orientation != self.comparison_split.orientation():
                self.comparison_split.setOrientation(orientation)
                self.comparison_split.setSizes([1,1])
        finally:
            self._arranging_comparison = False

    def step_review_change(self, direction):
        if self.full_review_result and self.full_review_result.changes():
            self.focus_review_change((self.review_change_index + direction) % len(self.full_review_result.changes()))

    def focus_review_change(self, index):
        if self.full_review_result is None or not 0 <= index < len(self.full_review_result.changes()):
            return
        from .comparison import change_line_ranges
        self.review_change_index = index
        # Unchecked changes remain navigation targets at their unchanged position.
        delta = 0
        after_start = 0
        for number, change in enumerate(self.full_review_result.changes()):
            if number == index:
                after_start = change.start + delta
                break
            if self.review_choices[number].isChecked():
                delta += len(change.replacement) - (change.end-change.start)
        old_start = change_line_ranges(self.full_review_result)[index][0]
        before_scroll, after_scroll = self.before_text.verticalScrollBar(), self.after_text.verticalScrollBar()
        with QSignalBlocker(before_scroll), QSignalBlocker(after_scroll):
            for editor,line in ((self.before_text,old_start),(self.after_text,after_start)):
                block = editor.document().findBlockByNumber(min(line,editor.blockCount()-1))
                cursor = editor.textCursor()
                cursor.setPosition(block.position())
                editor.setTextCursor(cursor)
                editor.centerCursor()

    def remember_splitter(self, *_):
        if self.ui_state is not None and self.image_panel.isVisible():
            self.ui_state.set(preview_sizes=self.preview_split.sizes())

    def set_image_visible(self, visible):
        if visible and not self.history_mode:
            self.image_panel.show()
            if self.ui_state is not None:
                self.preview_split.setSizes(self.ui_state.get("preview_sizes", [440, 560]))
        else:
            if self.image_panel.isVisible():
                self.remember_splitter()
            self.image_panel.hide()
        if self.ui_state is not None and not self.history_mode:
            self.ui_state.set(image_visible=visible)
        self.show_image_button.setVisible(not visible and not self.history_mode)
        self.hide_image_button.setVisible(visible and not self.history_mode)

    def change_image_zoom(self, value):
        self.fit_image.setChecked(False)
        with QSignalBlocker(self.image_zoom):
            self.image_zoom.setValue(round(value))
        self.image_view.set_zoom(value)

    def save_current_result(self):
        if self.is_csv:
            self.table_save_menu.popup(self.export_csv_button.mapToGlobal(self.export_csv_button.rect().bottomLeft()))
        else:
            self.save_current_text()

    def show_whitespace(self, enabled):
        option = self.text.document().defaultTextOption()
        flags = option.flags()
        flag = QTextOption.Flag.ShowTabsAndSpaces
        option.setFlags(flags | flag if enabled else flags & ~flag)
        self.text.document().setDefaultTextOption(option)

    def update_editor_font(self, *_):
        self.text.setStyleSheet(f"font-size: {self.font_size.currentData()}px;")
        self.text.update_gutter()
        for name in ('before_text','after_text','diff','raw_editor'):
            editor = getattr(self,name,None)
            if editor is not None:
                editor.setStyleSheet(f"font-size: {self.font_size.currentData()}px;")
                if hasattr(editor,'update_gutter'):
                    editor.update_gutter()

    def update_text_details(self):
        text = self.text.toPlainText()
        if self.is_csv:
            self.update_table_details()
        else:
            cursor = self.text.textCursor()
            self.text_stats.setText(f"{len(text)} симв. · {self.text.blockCount()} стр.")
            self.text_stats.setToolTip(f"Курсор: строка {cursor.blockNumber() + 1}, столбец {cursor.positionInBlock() + 1}")
        pending = not self._matches_document(self.copied_text)
        self.copy_state.setText("Есть правки" if pending and self.copied_text is not None else "")
        self.copy_button.setEnabled(bool(text))

    def _matches_document(self, value):
        # Qt normalizes line separators on insertion; this is not a user edit.
        return value is not None and self.text.toPlainText() == value.replace('\r\n','\n').replace('\r','\n')

    def set_result_view(self, mode):
        if mode not in {"auto", "text", "table"}:
            raise ValueError("Неизвестное представление результата")
        self.commit_table_edit()
        if mode == "table" and csv_rows(self.text.toPlainText(), explicit=True) is None:
            self.result_view = "text"
            self.refresh_table()
            self.status.setText("Не удалось открыть таблицу: неверный CSV")
            return False
        self.result_view = mode
        if mode == 'auto':
            self.table_candidate = csv_rows(self.text.toPlainText(), self.format_mode == 'csv', allow_single_cell=False) is not None
        self.refresh_table()
        return mode != "table" or self.is_csv

    def apply_table_edit(self, text):
        # A structure command can reduce an automatically detected table to 1×1.
        self.result_view = "table"
        self.replace_text(text)

    def refresh_table(self):
        candidate = self.result_view == "table" or (self.result_view == "auto" and self.table_candidate)
        rows = csv_rows(self.text.toPlainText(), self.result_view == "table" or self.format_mode == "csv",
                        allow_single_cell=self.result_view == "table") if candidate else None
        self.is_csv = False
        if rows is not None:
            try:
                self.table.display_rows(rows, self.original_text)
                self.is_csv = True
            except (ValueError, RuntimeError, MemoryError):
                # The canonical text is retained even if Qt cannot render cells.
                pass
        self.result_stack.setCurrentWidget(self.table if self.is_csv else self.text)
        with QSignalBlocker(self.result_mode):
            self.result_mode.setCurrentIndex(self.result_mode.findData(self.result_view))
        self.result_heading.setText("Таблица" if self.is_csv else "Текст")
        self.syntax_language.setVisible(not self.is_csv)
        self.format_button.setVisible(not self.is_csv)
        self.format_button.setEnabled(not self.is_csv)
        self.table_heading.hide()
        self.table_heading_action.setVisible(self.is_csv)
        self.export_csv_button.setVisible(self.is_csv)
        self.export_xlsx_button.setVisible(self.is_csv)
        self.save_file_button.setVisible(not self.is_csv)
        self.save_file_button.setEnabled(bool(self.text.toPlainText()))
        if self.is_csv:
            self.syntax_timer.stop()
            self.syntax_message.hide()
        self.update_text_details()
        self.update_search(navigate=False)

    def mark_copied(self, text):
        self.copied_text = text
        self.update_text_details()
        self.status.setText("В буфере обмена" if self._matches_document(text) else "Есть несохранённые в буфере правки")

    def commit_table_edit(self):
        if self.is_csv:
            self.table.cell_delegate.commit_pending()

    def copy_result(self):
        self.commit_table_edit()
        self.copy_requested.emit(self.text.toPlainText())

    def choose_export_path(self, kind):
        dialog = QFileDialog(self, "Экспорт в " + kind.upper())
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setNameFilter(f"{kind.upper()} (*.{kind})")
        dialog.setDefaultSuffix(kind)
        self.prepare_save_dialog(dialog, "Таблица", kind)
        try:
            return dialog.selectedFiles()[0] if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def choose_text_path(self, extension):
        from .text_export import FORMATS
        dialog = QFileDialog(self, "Сохранить в…")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setNameFilters([f"{FORMATS[extension]} (*.{extension})", "Все файлы (*)"])
        dialog.setDefaultSuffix(extension)
        self.prepare_save_dialog(dialog, "Текст", extension)
        try:
            return dialog.selectedFiles()[0] if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def prepare_save_dialog(self, dialog, name, extension):
        if self.ui_state is not None:
            directory = self.ui_state.get("last_save_directory", "")
            if directory and Path(directory).is_dir():
                dialog.setDirectory(directory)
        dialog.selectFile(f"Снимок-{datetime.now():%Y-%m-%d-%H%M%S}.{extension}")

    def remember_save_directory(self, destination):
        if self.ui_state is not None:
            self.ui_state.set(last_save_directory=str(Path(destination).resolve().parent))

    def save_current_text(self):
        if self.is_csv:
            return False
        from .text_export import suggest_extension, save_text
        text = self.text.toPlainText()
        if not text:
            return False
        destination = self.choose_text_path(suggest_extension(text, self.syntax_language.currentData()))
        if not destination:
            return False
        try:
            save_text(destination, text)
        except Exception as error:
            QMessageBox.critical(self, "Не удалось сохранить файл", str(error))
            return False
        self.status.setText("Сохранено: " + Path(destination).name)
        self.remember_save_directory(destination)
        return True

    def export_current_table(self, kind, destination=None):
        if not self.is_csv:
            return False
        self.commit_table_edit()
        text = self.text.toPlainText()
        destination = destination or self.choose_export_path(kind)
        if not destination:
            return False
        try:
            from .table_export import export_table
            export_table(destination, text, kind)
        except Exception as error:
            QMessageBox.critical(self, "Не удалось экспортировать таблицу", str(error))
            return False
        self.status.setText("Сохранено: " + Path(destination).name)
        self.remember_save_directory(destination)
        return True

    def save_table_changes(self):
        dialog = QFileDialog(self, "Сохранить таблицу…")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setNameFilters(["CSV (*.csv)", "XLSX (*.xlsx)"])
        dialog.setDefaultSuffix("csv")
        dialog.filterSelected.connect(lambda value: dialog.setDefaultSuffix("xlsx" if "xlsx" in value else "csv"))
        self.prepare_save_dialog(dialog, "Снимок", "csv")
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            kind = "xlsx" if "xlsx" in dialog.selectedNameFilter() else "csv"
            return self.export_current_table(kind, dialog.selectedFiles()[0])
        finally:
            dialog.deleteLater()

    def confirm_replace_result(self):
        self.commit_table_edit()
        current = self.text.toPlainText()
        if self._matches_document(self.original_text) or self._matches_document(self.copied_text):
            return True
        decision = QMessageBox.question(self, "Несохранённые правки",
            "Сохранить правки перед продолжением?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if decision == QMessageBox.StandardButton.Discard:
            return True
        if decision == QMessageBox.StandardButton.Save:
            return self.save_table_changes() if self.is_csv else self.save_current_text()
        return False

    def schedule_syntax(self):
        self.syntax_revision += 1
        self.syntax_message.clear()
        self.syntax_message.hide()
        self.text.set_syntax_hint(None)
        self.clear_syntax_status()
        self.syntax_timer.start()
        with QSignalBlocker(self.text):
            self.code_highlighter.update_text(self.text.toPlainText(), self.syntax_language.currentData())

    def apply_format(self, text, revision):
        if revision != self.syntax_revision:
            return False
        cursor = self.text.textCursor()
        cursor.beginEditBlock()
        cursor.select(cursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()
        self.status.setText("Текст отформатирован · буфер ещё не обновлён")
        return True

    def show_syntax_hint(self, revision, hint):
        if self.is_csv or revision != self.syntax_revision:
            return
        self.syntax_message.setText(hint.label if hint else "")
        self.text.set_syntax_hint(hint)
        self.syntax_language.setToolTip(hint.label if hint else "Проверка синтаксиса — подсказка. Код не исполняется.")
        self.clear_syntax_status()
        if hint:
            warning_text = f'Синтаксис · строка {hint.line}' if hint.line else 'Синтаксис'
            self._syntax_status_state = (warning_text,hint.label,self.status.text(),
                                         self.status.toolTip(),self.status.property('role'))
            self.status.setText(warning_text)
            self.status.setProperty('role','warning')
            self.status.setToolTip(hint.label)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def clear_syntax_status(self):
        state, self._syntax_status_state = self._syntax_status_state, None
        if state is None:
            return
        warning_text, warning_tooltip, previous_text, previous_tooltip, previous_role = state
        owns_text = self.status.text() == warning_text
        owns_tooltip = self.status.toolTip() == warning_tooltip
        if owns_text:
            self.status.setText(previous_text)
        if owns_tooltip:
            self.status.setToolTip(previous_tooltip)
        if (owns_text or owns_tooltip) and self.status.property('role') == 'warning':
            self.status.setProperty('role',previous_role)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def invalidate_review(self):
        self.clear_review_error()
        self.review_result = None
        self.full_review_result = None
        self.review_panel.hide()
        self.restore_action.setEnabled(not self._matches_document(self.original_text))
        if self.copied_text is not None:
            self.status.setText("Есть несохранённые в буфере правки" if not self._matches_document(self.copied_text) else
                                "В буфере обмена")
        elif self.history_mode:
            self.status.setText("История · есть правки" if not self._matches_document(self.original_text) else "Открыто из истории")
        self.review_invalidated.emit()

    def set_review_busy(self, busy):
        self.review_button.setEnabled(not busy and not self.history_mode)
        self.review_button.setText("Уточнение…" if busy else "Уточнить")
        self.refine_model.setEnabled(not busy)
        self.review_cancel_button.setVisible(busy)

    def show_review_error(self, message, summary="Уточнение не завершено"):
        self.clear_syntax_status()
        if self._review_error_detail is None:
            self._review_error_previous = (self.status.toolTip(), self.status.accessibleDescription(),
                                           self.status.property("role"))
        self._review_error_detail = message
        self.status.setText(summary)
        self.status.setToolTip(message)
        self.status.setAccessibleDescription(message)
        self.status.setProperty("role", "error")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.review_error_text.setPlainText(message)
        self.review_error_button.setToolTip(message)
        self.review_error_button.setAccessibleDescription(message)
        self.review_error_button.show()

    def clear_review_error(self):
        detail = self._review_error_detail
        if detail is not None:
            self.clear_syntax_status()
            tooltip, description, role = self._review_error_previous
            if self.status.toolTip() == detail:
                self.status.setToolTip(tooltip)
            if self.status.accessibleDescription() == detail:
                self.status.setAccessibleDescription(description)
            if self.status.property("role") == "error":
                self.status.setProperty("role", role)
                self.status.style().unpolish(self.status)
                self.status.style().polish(self.status)
        self._review_error_detail = self._review_error_previous = None
        self.review_error_button.hide()
        self.review_error_button.setToolTip("")
        self.review_error_button.setAccessibleDescription("")
        self.review_error_text.clear()
        self.review_error_dialog.close()

    def show_review_error_details(self):
        if self._review_error_detail is not None:
            self.review_error_dialog.show()
            fit_window_to_screen(self.review_error_dialog)
            self.review_error_dialog.raise_()
            self.review_error_dialog.activateWindow()

    def hideEvent(self, event):
        if not event.spontaneous():
            self.remember_splitter()
            self.review_panel.hide()
            self.set_raw_response(None)
            self.dismissed.emit()
        super().hideEvent(event)

    def propose(self, result):
        if not self.isVisible() or self.text.toPlainText() != result.original:
            return False
        self.full_review_result = result
        while self.choices_layout.count():
            self.choices_layout.takeAt(0).widget().deleteLater()
        self.review_choices = []
        for index, change in enumerate(result.changes()):
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
            old = ''.join(result.original.splitlines(keepends=True)[change.start:change.end])
            new = ''.join(change.replacement)
            snippet = button(f"{old.strip()[:28] or '∅'} → {new.strip()[:28] or '∅'}", lambda _checked=False, index=index: self.focus_review_change(index))
            snippet.setToolTip(old + '\n→\n' + new)
            snippet.setMinimumHeight(26)
            item_layout.addWidget(snippet)
            self.choices_layout.addWidget(item)
            self.review_choices.append(choice)
        self.select_review_changes()
        self.review_change_index = -1
        self.review_choice_toggle.setText(f'Изменения · {len(self.review_choices)}')
        self.arrange_comparison()
        self.review_panel.setVisible(result.changed)
        if result.changed:
            fit_window_to_screen(self.review_panel)
        self.status.setText(f"{'Есть предложение — проверьте различия' if result.changed else 'Изменений не предложено'} · {result.elapsed:.1f} с")
        return True

    def select_review_changes(self):
        if self.full_review_result is None:
            return
        self.review_result = self.full_review_result.select(
            index for index, choice in enumerate(self.review_choices) if choice.isChecked())
        self.diff.setPlainText(self.review_result.diff())
        from .comparison import show_comparison
        show_comparison(self.before_text, self.after_text, self.review_result)
        self.accept_button.setEnabled(self.review_result.changed)

    def reject_review(self):
        self.review_result = None
        self.full_review_result = None
        self.review_panel.hide()
        self.comparison_orientation_override = None
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
        self.status.setText("Исходный текст восстановлен · буфер ещё не обновлён")

    def set_refine_models(self, models, selected=None):
        current = selected or self.refine_model.currentData()
        self.refine_model.blockSignals(True)
        self.refine_model.clear()
        for entry in models:
            source, identifier, name = entry[:3]
            self.refine_model.addItem(name, (source, identifier))
            self.refine_model.setItemData(self.refine_model.count() - 1,
                {"source": source, "price": entry[3] if len(entry) > 3 else "Локально" if source == "local" else "—"}, DETAIL_ROLE)
            self.refine_model.setItemData(self.refine_model.count() - 1, identifier, Qt.ItemDataRole.ToolTipRole)
        index = self.refine_model.findData(current)
        if current and index < 0:
            self.refine_model.addItem(current[1], current)
            index = self.refine_model.count() - 1
            self.refine_model.setItemData(index, {"unavailable": True}, DETAIL_ROLE)
        self.refine_model.setCurrentIndex(index if index >= 0 else 0)
        self.refine_model.blockSignals(False)

    def display(self, image, text, elapsed, model, format_mode="auto"):
        self.set_raw_response(None)
        self.close_search()
        self.table.reset_column_widths()
        self.history_mode = False
        self.source_image = image.copy()
        self.set_refine_visible(True)
        self.image_visible_action.setEnabled(True)
        self.set_image_visible(self.image_visible_action.isChecked())
        self.image_view.display(image)
        self.original_text = text
        self.copied_text = text
        self.format_mode = format_mode
        self.result_view = "auto"
        self.table_candidate = format_mode != "text" and csv_rows(text, format_mode == "csv", allow_single_cell=False) is not None
        self.text.setPlainText(text)
        self.update_text_details()
        self.refresh_table()
        self.image_details.setText(f"{image.width()} × {image.height()} px · оригинал сохранён")
        self.invalidate_review()
        self.status.setText(f"В буфере обмена · {elapsed:.2f} с")
        self.status.setToolTip(model)
        self.review_button.setEnabled(True)
        self.show()
        fit_window_to_screen(self)
        self.raise_()
        self.activateWindow()

    def set_refine_visible(self, visible):
        for widget in (self.refine_model, self.review_button, self.context_toggle):
            widget.setVisible(visible)
        self.context_body.setVisible(visible and self.context_toggle.isChecked())
        self.review_cancel_button.hide()

    def display_history(self, text, model=""):
        self.set_raw_response(None)
        self.close_search()
        self.table.reset_column_widths()
        self.history_mode = True
        self.source_image = None
        self.image_view.original_pixmap = QPixmap()
        self.image.clear()
        self.image_panel.hide()
        self.show_image_button.hide()
        self.hide_image_button.hide()
        self.image_visible_action.setEnabled(False)
        self.set_refine_visible(False)
        self.original_text = text
        self.copied_text = None
        self.format_mode = "auto"
        self.result_view = "auto"
        self.table_candidate = csv_rows(text) is not None
        self.text.setPlainText(text)
        self.update_text_details()
        self.refresh_table()
        self.invalidate_review()
        self.status.setText("Открыто из истории")
        self.status.setToolTip(model)
        self.show()
        fit_window_to_screen(self)
        self.raise_()
        self.activateWindow()


class DiagnosticsDialog(SystemTheme, QDialog):
    def __init__(self, parent=None, ui_state=None):
        super().__init__(parent)
        self.ui_state = ui_state
        self.report_data = None
        self.setWindowTitle("ctrlCctrlV · Диагностика")
        self.resize(640, 340)
        self.setMinimumSize(480, 300)
        self.init_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(DIALOG_MARGIN, DIALOG_MARGIN, DIALOG_MARGIN, DIALOG_MARGIN)
        layout.setSpacing(GAP)
        layout.addWidget(label("Диагностика", "title"))
        self.setToolTip("Отчёт не содержит снимков, распознанного текста и ключей")
        self.summary = label("")
        self.summary.setAccessibleName("Краткая сводка диагностики")
        layout.addWidget(self.summary)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setFont(monospace_font())
        self.report.setMinimumHeight(100)
        self.report.setAccessibleName("Диагностический отчёт приложения")
        self.details_toggle = disclosure("Подробности", self.report)
        layout.addWidget(self.details_toggle)
        layout.addWidget(self.report, 1)
        layout.addStretch()
        row = QHBoxLayout()
        row.addWidget(button("Закрыть", self.close))
        row.addStretch()
        self.save_button = button("Сохранить JSON…", self.save_report)
        self.save_button.setEnabled(False)
        row.addWidget(self.save_button)
        self.copy_button = button("Копировать отчёт", lambda: QApplication.clipboard().setText(self.report.toPlainText()), True)
        row.addWidget(self.copy_button)
        layout.addLayout(row)

    def display(self, report):
        self.report.setPlainText(report)
        self.details_toggle.setChecked(False)
        self.report.hide()
        try:
            data = json.loads(report)
        except (ValueError, TypeError):
            data = None
        self.report_data = data if isinstance(data, dict) else None
        self.save_button.setEnabled(True)
        if self.report_data is None:
            self.summary.setText("Не удалось прочитать структуру отчёта")
        else:
            runtime = data.get("ocr_runtime") or {}
            runtime = runtime if isinstance(runtime, dict) else {}
            permissions = data.get("permissions") or {}
            permissions = permissions if isinstance(permissions, dict) else {}
            names = (("screen", "захват экрана"), ("accessibility", "управление вводом"),
                     ("input_monitoring", "глобальные клавиши"))
            missing = [name for key, name in names if permissions.get(key) is False]
            unknown = [name for key, name in names if permissions.get(key) not in (True, False)]
            access = "нет доступа — " + ", ".join(missing) if missing else "доступны"
            if unknown:
                access = (access + "; " if missing else "") + "неизвестно — " + ", ".join(unknown)
            device = " · ".join(str(data[key]) for key in ('system', 'architecture') if data.get(key)) or "неизвестно"
            memory = data.get('physical_memory_bytes')
            if isinstance(memory, (int, float)) and not isinstance(memory, bool) and memory > 0:
                device += f" · {memory / (1024 ** 3):g} ГБ RAM"
            provider = data.get('provider')
            if provider == 'local':
                ready = runtime.get('ready')
                engine = 'готов' if ready is True else 'не готов' if ready is False else 'состояние неизвестно'
                identifier = data.get('selected_local_model')
                model = MODELS.get(identifier) if isinstance(identifier, str) else None
                source = f"Модель: {model.label if model else 'неизвестна'}\nДвижок: {engine}"
            else:
                source = 'OCR: OpenRouter' if provider == 'openrouter' else 'OCR: не определён'
            self.summary.setText(f"Устройство: {device}\n{source}\nДоступы: {access}")
        self.show()
        self.raise_()
        self.activateWindow()

    def set_ui_state(self, state):
        self.ui_state = state

    def save_report(self):
        data = self.report_data if self.report_data is not None else {
            "diagnostics_error": "invalid_report", "report": self.report.toPlainText()}
        directory = self.ui_state.get("last_save_directory", "") if self.ui_state else ""
        name = f"ctrlCctrlV-diagnostics-{datetime.now():%Y%m%d-%H%M%S}.json"
        filename, _ = QFileDialog.getSaveFileName(self, "Сохранить диагностику", str(Path(directory) / name),
                                                 "JSON (*.json)")
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".json")
        try:
            from .text_export import save_text
            save_text(path, json.dumps(data, ensure_ascii=False, indent=2))
        except (OSError, UnicodeError):
            QMessageBox.warning(self, "Не удалось сохранить диагностику", "Проверьте папку, свободное место и права доступа.")
            return
        if self.ui_state:
            self.ui_state.set(last_save_directory=str(path.parent))


class SetupWizard(SystemTheme, QWizard):
    """Choose an explicit OCR mode before entering the normal settings screen."""

    def __init__(self, parent=None):
        prepare_fonts()
        super().__init__(parent)
        self.setup_action = None
        self.setWindowTitle("ctrlCctrlV · Первый запуск")
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setTitleFormat(Qt.TextFormat.PlainText)
        self.init_theme()
        self.setMinimumSize(520, 240)
        self.resize(620, 300)
        self.setButtonText(QWizard.WizardButton.NextButton, "Далее")
        self.setButtonText(QWizard.WizardButton.BackButton, "Назад")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Начать")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Позже")
        choice = QWizardPage()
        choice.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        choice.setTitle("Распознавание")
        layout = QVBoxLayout(choice)
        layout.setContentsMargins(DIALOG_MARGIN, GAP, DIALOG_MARGIN, DIALOG_MARGIN)
        layout.setSpacing(GAP)
        mode_layout = layout
        mode_layout.addWidget(label("Где обрабатывать снимки", "section"))
        self.provider = QComboBox()
        self.provider.addItem("На этом компьютере", "local")
        self.provider.addItem("Через OpenRouter", "openrouter")
        self.provider.setAccessibleName("Режим OCR при первом запуске")
        mode_layout.addWidget(self.provider)
        self.mode_description = label("", "muted")
        mode_layout.addWidget(self.mode_description)
        self.hardware_body = QWidget()
        hardware_layout = QVBoxLayout(self.hardware_body)
        hardware_layout.setContentsMargins(0, 0, 0, 0)
        hardware_layout.setSpacing(GAP)
        self.acceleration = QComboBox()
        self.acceleration.addItem("GPU · Metal" if sys.platform == 'darwin' else "GPU · NVIDIA CUDA", "gpu")
        self.acceleration.addItem("CPU · без ускорения GPU", "cpu")
        self.acceleration.setAccessibleName("Ускорение при первом запуске")
        hardware_layout.addWidget(self.acceleration)
        from .hardware import GIB, memory_capacity
        import platform
        total, _ = memory_capacity()
        system = f"{platform.system()} {platform.machine()}"
        if total:
            system += f" · RAM {total / GIB:.1f} ГБ"
        hardware_layout.addWidget(label(system, "muted"))
        self.hardware_toggle = disclosure("Дополнительно", self.hardware_body)
        self.hardware_toggle.toggled.connect(lambda _: QTimer.singleShot(0, self.size_to_step))
        layout.addWidget(self.hardware_toggle)
        layout.addWidget(self.hardware_body)
        layout.addStretch()
        self.addPage(choice)
        configuration = self.configuration_page = QWizardPage()
        configuration.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        configuration.setTitle("Локальная модель")
        layout = QVBoxLayout(configuration)
        layout.setContentsMargins(DIALOG_MARGIN, GAP, DIALOG_MARGIN, DIALOG_MARGIN)
        layout.setSpacing(GAP)
        self.choice_summary = label("", "muted")
        self.choice_summary.hide()
        self.instructions = label("", "muted")
        layout.addWidget(self.instructions)
        self.local_model = QComboBox()
        self.local_model.setAccessibleName("Модель для первой загрузки")
        from .settings import Settings
        for identifier, model in MODELS.items():
            size = sum(artifact.size for artifact in model.files) / 1_000_000_000
            self.local_model.addItem(f"{model.label} · {size:.1f} ГБ", identifier)
        self.local_model.setCurrentIndex(max(0, self.local_model.findData(Settings().local_model)))
        layout.addWidget(self.local_model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API-ключ OpenRouter")
        self.key.setAccessibleName("API-ключ OpenRouter при первом запуске")
        layout.addWidget(self.key)
        self.connection_note = label("Ключ сохраняется в системном хранилище. Подключение не запускает распознавание.", "muted")
        layout.addWidget(self.connection_note)
        layout.addStretch()
        self.addPage(configuration)
        self.provider.currentIndexChanged.connect(self.update_choice)
        self.acceleration.currentIndexChanged.connect(self.update_choice)
        self.key.textChanged.connect(self.update_choice)
        self.currentIdChanged.connect(self.update_choice)
        self.currentIdChanged.connect(lambda _: QTimer.singleShot(0, self.size_to_step))
        self.update_choice()
        self.apply_system_theme()

    def apply_system_theme(self, *_):
        super().apply_system_theme()
        # QWizard's native ruler keeps its own palette instead of inheriting it.
        for child in self.findChildren(QWidget):
            child.setPalette(editor_palette())

    def showEvent(self, event):
        super().showEvent(event)
        self.size_to_step()

    def size_to_step(self):
        page = self.currentPage()
        if page is not None and self.isVisible():
            height = max(240, page.layout().sizeHint().height() + 104)
            self.resize(self.width(), height)
            fit_window_to_screen(self)

    def update_choice(self):
        local = self.provider.currentData() == "local"
        self.acceleration.setVisible(local)
        self.hardware_toggle.setVisible(local)
        self.hardware_body.setVisible(local and self.hardware_toggle.isChecked())
        self.configuration_page.setTitle("Локальная модель" if local else "Подключение OpenRouter")
        self.local_model.setVisible(local)
        self.key.setVisible(not local)
        self.connection_note.setVisible(not local)
        self.setButtonText(QWizard.WizardButton.FinishButton, "Скачать модель" if local else "Подключить OpenRouter")
        if self.currentId() == 1:
            self.button(QWizard.WizardButton.FinishButton).setEnabled(local or bool(self.key.text().strip()))
        for which in (QWizard.WizardButton.NextButton, QWizard.WizardButton.FinishButton):
            control = self.button(which)
            control.setProperty('role', 'primary' if (which == QWizard.WizardButton.FinishButton) == (self.currentId() == 1) else '')
            control.style().unpolish(control)
            control.style().polish(control)
        if local:
            mode = "GPU" if self.acceleration.currentData() == "gpu" else "CPU"
            self.mode_description.setText("Снимки остаются на этом компьютере.")
            self.choice_summary.setText("На этом компьютере · " + mode)
            self.instructions.setText("Снимки остаются на этом компьютере. Скачивание начнётся по нажатию кнопки.")
        else:
            self.mode_description.setText("Снимки отправляются в OpenRouter. Запросы оплачиваются по тарифу модели.")
            self.choice_summary.setText("Через OpenRouter · облачная модель")
            self.instructions.setText("Введите API-ключ, чтобы подключить OpenRouter.")

    def validateCurrentPage(self):
        if self.currentId() == 1:
            local = self.provider.currentData() == "local"
            if not local and not self.key.text().strip():
                self.key.setFocus()
                return False
            self.setup_action = "download" if local else "connect"
        return super().validateCurrentPage()


class BaseSettingsWindow(SystemTheme, QDialog):
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
        self.delay = DelaySpinBox()
        self.delay.setDecimals(6)
        self.delay.setRange(1, 200)
        self.delay.setValue(settings.character_delay * 1000)
        self.delay.setSuffix(" мс")
        tf.addRow("Между символами", self.delay)
        self.newline = DelaySpinBox()
        self.newline.setDecimals(6)
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
        settings.validate(getattr(self, "models", None))


# Lazy public alias also permits direct imports of workspace_ui.
def __getattr__(name):
    if name == "SettingsWindow":
        from .workspace_ui import SettingsWindow
        return SettingsWindow
    raise AttributeError(name)
