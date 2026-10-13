"""Literal result search; offsets belong to Python strings, never Qt cursors."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QLabel, QToolButton


def find_spans(text: str, query: str, case_sensitive: bool = False) -> list[tuple[int, int]]:
    if not query:
        return []
    if case_sensitive:
        haystack, needle = text, query
        starts = ends = None
    else:
        # Case folding can expand one code point (ß → ss). Keep original offsets.
        folded, starts, ends = [], [], []
        for index, char in enumerate(text):
            value = char.casefold()
            folded.append(value)
            starts.extend([index] * len(value))
            ends.extend([index + 1] * len(value))
        haystack, needle = ''.join(folded), query.casefold()
    spans, position = [], 0
    while needle:
        start = haystack.find(needle, position)
        if start < 0:
            break
        end = start + len(needle)
        span = (start, end) if case_sensitive else (starts[start], ends[end - 1])
        if not spans or spans[-1] != span:
            spans.append(span)
        position = end
    return spans


def qt_offset(text: str, position: int) -> int:
    """QTextDocument normalizes CRLF and counts supplementary chars as two units."""
    prefix = text[:position].replace('\r\n', '\n')
    return len(prefix.encode('utf-16-le')) // 2


class SearchBar(QWidget):
    query_changed = Signal(str)
    step_requested = Signal(int)
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.query = QLineEdit()
        self.query.setPlaceholderText('Найти в результате')
        self.query.setAccessibleName('Поиск в результате')
        self.query.textChanged.connect(self.query_changed)
        self.query.installEventFilter(self)
        row.addWidget(self.query, 1)
        self.counter = QLabel('0 / 0')
        row.addWidget(self.counter)
        for title, name, callback in (('↑', 'Предыдущее совпадение', lambda: self.step_requested.emit(-1)),
                                      ('↓', 'Следующее совпадение', lambda: self.step_requested.emit(1)),
                                      ('×', 'Закрыть поиск', self.closed.emit)):
            control = QToolButton()
            control.setText(title)
            control.setAccessibleName(name)
            control.clicked.connect(callback)
            row.addWidget(control)

    def eventFilter(self, watched, event):
        if event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.closed.emit()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.step_requested.emit(-1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                return True
        return super().eventFilter(watched, event)
