"""Editable CSV cells; parsing never modifies the original transcription."""
import csv
import io
import ast
import re

from collections.abc import Sequence
from PySide6.QtCore import Qt, QSignalBlocker, Signal, QRectF
from PySide6.QtGui import QKeySequence, QColor, QBrush, QTextDocument, QTextCursor, QTextCharFormat, QAbstractTextDocumentLayout
from PySide6.QtWidgets import QApplication, QAbstractItemView, QHeaderView, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QMenu, QStyleOptionViewItem, QStyle

from .result_search import qt_offset
from .theme import colours


def csv_rows(text, explicit=False, *, allow_single_cell=True):
    if not explicit:
        if text.lstrip().startswith(("{", "[")) or re.search(
                r"(?im)^\s*(?:SELECT|INSERT\s+INTO|CREATE\s+TABLE|UPDATE|DELETE\s+FROM)\s", text):
            return None
        if re.match(r"\s*(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*\s*\(", text):
            return None
        try:
            tree = ast.parse(text)
            if any(not isinstance(statement, ast.Expr) or isinstance(statement.value, ast.Call)
                   for statement in tree.body):
                return None
        except (SyntaxError, ValueError, RecursionError):
            pass
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (csv.Error, ValueError):
        return None
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        return None
    # Auto mode must not turn ordinary prose into a one-column spreadsheet.
    if len(rows[0]) < 2 and (not explicit or (len(rows) < 2 and not allow_single_cell)):
        return None
    if not explicit and len(rows) < 2:
        return None
    return rows


class CellDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self.active_editor = None

    def createEditor(self, parent, option, index):
        self.active_editor = super().createEditor(parent, option, index)
        return self.active_editor

    def destroyEditor(self, editor, index):
        if self.active_editor is editor:
            self.active_editor = None
        super().destroyEditor(editor, index)

    def commit_pending(self):
        editor = self.active_editor
        if editor is not None:
            self.commitData.emit(editor)
            self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)

    def paint(self, painter, option, index):
        table = self.parent()
        spans = table.search_cell_spans.get((index.row(),index.column()), [])
        if not spans:
            super().paint(painter,option,index)
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled,index)
        text, styled.text = styled.text, ''
        style = table.style()
        rectangle = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText,styled,table)
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem,styled,painter,table)
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(styled.font)
        document.setPlainText(text)
        text_option = document.defaultTextOption()
        text_option.setAlignment(styled.displayAlignment & Qt.AlignmentFlag.AlignHorizontal_Mask)
        document.setDefaultTextOption(text_option)
        document.setTextWidth(max(1,rectangle.width()))
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette = styled.palette
        dark = QApplication.palette().window().color().lightness() < 128
        selections = []
        for start,end in spans:
            selection = QAbstractTextDocumentLayout.Selection()
            selection.cursor = QTextCursor(document)
            selection.cursor.setPosition(qt_offset(text,start))
            selection.cursor.setPosition(qt_offset(text,end),QTextCursor.MoveMode.KeepAnchor)
            active = table.active_search_match == (index.row(),index.column(),start,end)
            selection.format = QTextCharFormat()
            selection.format.setBackground(QColor(('#a77a2b' if active else '#65532a') if dark else
                                                  ('#f7c854' if active else '#fff0b3')))
            selection.format.setForeground(QColor('#ffffff' if dark else '#202936'))
            selection.format.setFontUnderline(active)
            selections.append(selection)
        context.selections = selections
        height = document.size().height()
        offset_y = max(0,(rectangle.height()-height)/2)
        active = table.active_search_match
        if active is not None and active[:2] == (index.row(),index.column()):
            cursor_position = qt_offset(text,active[2])
            block = document.findBlock(cursor_position)
            line = block.layout().lineForTextPosition(cursor_position-block.position())
            if line.isValid():
                bottom = document.documentLayout().blockBoundingRect(block).top() + line.y() + line.height()
                offset_y = min(offset_y,rectangle.height()-bottom)
        y = rectangle.top() + offset_y
        painter.save()
        painter.setClipRect(rectangle)
        painter.translate(rectangle.left(),y)
        context.clip = QRectF(0,max(0,-offset_y),rectangle.width(),rectangle.height())
        document.documentLayout().draw(painter,context)
        painter.restore()


class CsvEditor(QTableWidget):
    csv_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAccessibleName("Распознанная таблица для редактирования")
        self.cell_delegate = CellDelegate(self)
        self.setItemDelegate(self.cell_delegate)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.itemChanged.connect(self.publish_cells)
        self.line_ending = "\n"
        self.first_row_heading = False
        self._manual_widths = {}
        self._sizing = False
        self._search_cells = set()
        self.search_cell_spans = {}
        self.active_search_match = None
        self.horizontalHeader().sectionResized.connect(self.remember_width)

    def reset_column_widths(self):
        self._manual_widths.clear()

    def remember_width(self, column, old, new):
        if not self._sizing:
            self._manual_widths[column] = new

    def arrange_columns(self):
        if not self.columnCount():
            return
        self._sizing = True
        try:
            metrics = self.fontMetrics()
            widths, flexible = [], []
            for c in range(self.columnCount()):
                values = [self.item(r,c).text() if self.item(r,c) else '' for r in range(self.rowCount())]
                widths.append(self._manual_widths.get(c, min(280, max(90, max(
                    (metrics.horizontalAdvance(line) + 28 for value in values for line in value.split('\n')), default=90)))))
                content = values[1:] if self.first_row_heading else values
                if c not in self._manual_widths and any(v.strip() and not re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?', v.strip()) for v in content):
                    flexible.append(c)
            extra = max(0, self.viewport().width() - sum(widths))
            for c in flexible:
                widths[c] += min(240, extra // max(1, len(flexible)))
            for c, width in enumerate(widths):
                self.setColumnWidth(c, width)
        finally:
            self._sizing = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange_columns()

    def selected_cells(self):
        return [(index.row(), index.column()) for index in self.selectedIndexes()]

    def show_search_cells(self, cells):
        self.search_cell_spans = {}
        self.active_search_match = None
        with QSignalBlocker(self):
            for row, column in self._search_cells:
                item = self.item(row, column)
                if item:
                    item.setBackground(QBrush())
            self._search_cells = set(cells)
            dark = QApplication.palette().window().color().lightness() < 128
            for row, column in self._search_cells:
                item = self.item(row, column)
                if item:
                    item.setBackground(QColor('#65532a' if dark else '#fff0b3'))

    def show_search_matches(self, matches, active=None):
        self.show_search_cells((row,column) for row,column,start,end in matches)
        for row,column,start,end in matches:
            self.search_cell_spans.setdefault((row,column),[]).append((start,end))
        self.active_search_match = active
        self.viewport().update()

    def focus_search_match(self, match):
        self.active_search_match = match
        if match is not None:
            row,column,start,end = match
            self.setCurrentCell(row,column)
            self.scrollToItem(self.item(row,column))
        self.viewport().update()

    def _finish_command(self, row, column):
        with QSignalBlocker(self):
            if not self.rowCount() or not self.columnCount():
                self.setRowCount(1)
                self.setColumnCount(1)
                self.setItem(0, 0, QTableWidgetItem(''))
            self.setCurrentCell(max(0, min(row, self.rowCount()-1)), max(0, min(column, self.columnCount()-1)))
        self.set_first_row_heading(self.first_row_heading)
        self.publish_cells()

    def insert_rows(self, before: int, count: int = 1) -> None:
        self.cell_delegate.commit_pending()
        if count < 1:
            return
        before = max(0, min(before, self.rowCount()))
        with QSignalBlocker(self):
            for row in range(before, before + count):
                self.insertRow(row)
                for column in range(self.columnCount()):
                    self.setItem(row, column, QTableWidgetItem(''))
        self._finish_command(before, max(0, self.currentColumn()))

    def insert_columns(self, before: int, count: int = 1) -> None:
        self.cell_delegate.commit_pending()
        if count < 1:
            return
        before = max(0, min(before, self.columnCount()))
        with QSignalBlocker(self):
            for column in range(before, before + count):
                self.insertColumn(column)
                for row in range(self.rowCount()):
                    self.setItem(row, column, QTableWidgetItem(''))
        self._manual_widths.clear()
        self._finish_command(max(0, self.currentRow()), before)

    def delete_rows(self, indices: Sequence[int]) -> None:
        self.cell_delegate.commit_pending()
        indices = sorted({index for index in indices if 0 <= index < self.rowCount()}, reverse=True)
        if not indices:
            return
        column = self.currentColumn()
        with QSignalBlocker(self):
            for index in indices:
                self.removeRow(index)
        self._finish_command(min(indices), column)

    def delete_columns(self, indices: Sequence[int]) -> None:
        self.cell_delegate.commit_pending()
        indices = sorted({index for index in indices if 0 <= index < self.columnCount()}, reverse=True)
        if not indices:
            return
        row = self.currentRow()
        with QSignalBlocker(self):
            for index in indices:
                self.removeColumn(index)
        self._manual_widths.clear()
        self._finish_command(row, min(indices))

    def clear_selection(self) -> None:
        self.cell_delegate.commit_pending()
        cells = self.selected_cells()
        if not cells:
            return
        with QSignalBlocker(self):
            for row, column in cells:
                self.setItem(row, column, QTableWidgetItem(''))
        self._finish_command(self.currentRow(), self.currentColumn())

    def contextMenuEvent(self, event):
        if self.cell_delegate.active_editor is not None:
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        # The table's transparent-child rule must not reach this popup surface.
        menu.setStyleSheet(f"QMenu {{ background: {colours()['panel']}; }}")
        row, column = max(0,self.currentRow()), max(0,self.currentColumn())
        cells = self.selected_cells() or [(row,column)]
        for title, callback in (
            ('Вставить строку выше', lambda: self.insert_rows(row)),
            ('Вставить строку ниже', lambda: self.insert_rows(row+1)),
            ('Вставить столбец слева', lambda: self.insert_columns(column)),
            ('Вставить столбец справа', lambda: self.insert_columns(column+1)),
            ('Удалить строки', lambda: self.delete_rows([r for r,c in cells])),
            ('Удалить столбцы', lambda: self.delete_columns([c for r,c in cells])),
            ('Очистить ячейки', self.clear_selection)):
            menu.addAction(title, callback)
        menu.exec(event.globalPos())

    def set_first_row_heading(self, enabled):
        self.first_row_heading = enabled
        with QSignalBlocker(self):
            for column in range(self.columnCount()):
                item = self.item(0, column)
                if item:
                    font = item.font()
                    font.setBold(enabled)
                    item.setFont(font)

    def copy_selection(self):
        indexes = self.selectedIndexes()
        if not indexes:
            return
        selected = {(index.row(), index.column()) for index in indexes}
        top, bottom = min(r for r, _ in selected), max(r for r, _ in selected)
        left, right = min(c for _, c in selected), max(c for _, c in selected)
        stream = io.StringIO(newline="")
        csv.writer(stream, delimiter="\t", lineterminator="\n").writerows(
            [self.item(r, c).text() if (r, c) in selected and self.item(r, c) else ""
             for c in range(left, right + 1)] for r in range(top, bottom + 1))
        QApplication.clipboard().setText(stream.getvalue())

    def paste_selection(self):
        try:
            rows = list(csv.reader(io.StringIO(QApplication.clipboard().text(), newline=""),
                                   delimiter="\t", strict=True))
        except csv.Error:
            return
        if not rows:
            return
        row, column = max(0, self.currentRow()), max(0, self.currentColumn())
        width = max(map(len, rows))
        if not width:
            return
        with QSignalBlocker(self):
            self.setRowCount(max(self.rowCount(), row + len(rows)))
            self.setColumnCount(max(self.columnCount(), column + width))
            for r, values in enumerate(rows, row):
                for offset in range(width):
                    self.setItem(r, column + offset, QTableWidgetItem(values[offset] if offset < len(values) else ""))
        self.set_first_row_heading(self.first_row_heading)
        # One publication creates one canonical-text undo block for the rectangle.
        self.publish_cells()

    def keyPressEvent(self, event):
        if self.cell_delegate.active_editor is None:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.clear_selection()
                event.accept()
                return
            if event.matches(QKeySequence.StandardKey.Copy):
                self.copy_selection()
                event.accept()
                return
            if event.matches(QKeySequence.StandardKey.Paste):
                self.paste_selection()
                event.accept()
                return
        super().keyPressEvent(event)

    def display_rows(self, rows, original):
        self.line_ending = "\r\n" if "\r\n" in original else "\n"
        if self.rowCount() == len(rows) and self.columnCount() == len(rows[0]):
            if all(self.item(r, c) is not None and self.item(r, c).text() == value
                   for r, row in enumerate(rows) for c, value in enumerate(row)):
                return
        with QSignalBlocker(self):
            current = self.currentRow(), self.currentColumn()
            self.setRowCount(len(rows))
            self.setColumnCount(len(rows[0]))
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    self.setItem(r, c, QTableWidgetItem(value))
                    if len(value) < 32 and re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?', value.strip()):
                        self.item(r,c).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.arrange_columns()
            self.resizeRowsToContents()
            for r in range(self.rowCount()):
                self.setRowHeight(r, min(140, max(38, self.rowHeight(r))))
            if current[0] >= 0 and current[1] >= 0:
                self.setCurrentCell(min(current[0], self.rowCount()-1), min(current[1], self.columnCount()-1))
        self.set_first_row_heading(self.first_row_heading)

    def publish_cells(self, *_):
        stream = io.StringIO(newline="")
        csv.writer(stream, lineterminator=self.line_ending).writerows(
            [self.item(r, c).text() if self.item(r, c) else "" for c in range(self.columnCount())]
            for r in range(self.rowCount()))
        self.csv_changed.emit(stream.getvalue())
