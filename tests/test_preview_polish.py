import csv
import io
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidgetSelectionRange, QLineEdit, QMessageBox, QFileDialog, QDialog

from ctrlcctrlv.review import ReviewResult
from ctrlcctrlv.syntax import SyntaxHint
from ctrlcctrlv.ui import Preview


@pytest.fixture
def preview():
    app = QApplication.instance() or QApplication([])
    window = Preview()
    window.display(QImage(600, 300, QImage.Format.Format_RGB32), 'a = 1\nb = 2\n', .1, 'test')
    app.processEvents()
    yield window
    window.syntax_timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()


class State:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, **values):
        self.values.update(values)
        return True


def test_editing_and_syntax_hint_do_not_resize_editor(preview):
    app = QApplication.instance()
    height = preview.text.height()
    preview.text.insertPlainText('!')
    preview.show_syntax_hint(preview.syntax_revision, SyntaxHint('Python', 1, 'bad syntax'))
    app.processEvents()
    assert preview.text.height() == height
    assert preview.text.syntax_hint.line == 1
    assert not preview.copy_state.isVisible()
    assert not preview.syntax_message.isVisible()
    preview.restore_action.trigger()
    assert preview.text.toPlainText() == 'a = 1\nb = 2\n'


def test_image_zoom_visibility_and_splitter_preferences(preview):
    state = State(preview_sizes=[280, 700], image_visible=False)
    preview.set_ui_state(state)
    assert not preview.image_panel.isVisible()
    preview.image_visible_action.setChecked(True)
    preview.image_zoom.setValue(200)
    assert not preview.fit_image.isChecked()
    assert preview.image.pixmap().deviceIndependentSize().width() == 1200
    assert preview.source_image.width() == 600
    preview.preview_split.setSizes([340, 640])
    preview.preview_split.splitterMoved.emit(340, 1)
    assert state.get('preview_sizes') == preview.preview_split.sizes()
    preview.image_visible_action.setChecked(False)
    assert state.get('image_visible') is False


def test_history_is_editable_without_claiming_clipboard_or_refinement(preview):
    preview.display_history('old text', 'old model')
    assert preview.source_image is None
    assert preview.copied_text is None
    assert not preview.image_panel.isVisible()
    assert not preview.review_button.isVisible()
    assert not preview.refine_model.isVisible()
    assert 'буфер' not in preview.status.text().lower()
    preview.text.insertPlainText('edited ')
    assert preview.text.toPlainText() == 'edited old text'


def test_table_rectangle_copy_paste_expansion_and_single_undo(preview):
    original = 'Name,Qty\nPaper,1\n'
    preview.display(QImage(10, 10, QImage.Format.Format_RGB32), original, .1, 'test')
    preview.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 1), True)
    QTest.keyClick(preview.table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == 'Name\tQty\nPaper\t1\n'
    preview.table.setCurrentCell(1, 1)
    QApplication.clipboard().setText('2\t"two\nlines"\n3\t4\n')
    QTest.keyClick(preview.table, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert list(csv.reader(io.StringIO(preview.text.toPlainText()))) == [
        ['Name', 'Qty', ''], ['Paper', '2', 'two\nlines'], ['', '3', '4']]
    preview.text.undo()
    assert preview.text.toPlainText() == original
    assert preview.table.rowCount() == 2 and preview.table.columnCount() == 2


def test_heading_style_keeps_first_row_editable_and_in_export(preview):
    preview.display(QImage(10, 10, QImage.Format.Format_RGB32), 'Name,Qty\nPaper,1\n', .1, 'test')
    assert not preview.table_heading.isChecked()
    preview.table_heading.setChecked(True)
    assert preview.table.item(0, 0).font().bold()
    preview.table.item(0, 0).setText('Product')
    assert preview.text.toPlainText().startswith('Product,Qty\n')
    preview.table_heading.setChecked(False)
    assert not preview.table.item(0, 0).font().bold()


def test_review_comparison_keeps_selection_and_character_highlights(preview):
    preview.propose(ReviewResult('a = 1\nb = 2\n', 'a = 10\nb = 2\nc = 3\n', 'test', .1))
    assert preview.before_text.toPlainText() == 'a = 1\nb = 2\n'
    assert preview.after_text.toPlainText() == 'a = 10\nb = 2\nc = 3\n'
    assert preview.after_text.extraSelections()
    assert not preview.diff.isVisible()
    preview.technical_diff.setChecked(True)
    assert preview.diff.isVisible()
    preview.review_choices[0].setChecked(False)
    assert preview.after_text.toPlainText() == 'a = 1\nb = 2\nc = 3\n'
    assert preview.review_result.proposed == preview.after_text.toPlainText()


def test_successful_save_remembers_directory(preview, monkeypatch, tmp_path):
    state = State()
    preview.set_ui_state(state)
    path = tmp_path / 'saved.txt'
    monkeypatch.setattr(preview, 'choose_text_path', lambda _: str(path))
    preview.save_shortcut.activated.emit()
    assert path.read_text() == 'a = 1\nb = 2\n'
    assert state.get('last_save_directory') == str(tmp_path)


def test_active_cell_editor_keeps_its_own_paste_and_undo(preview):
    preview.display(QImage(10, 10, QImage.Format.Format_RGB32), 'Name,Qty\nPaper,1\n', .1, 'test')
    preview.table.editItem(preview.table.item(1, 0))
    editor = preview.table.findChild(QLineEdit)
    editor.selectAll()
    QApplication.clipboard().setText('Book')
    QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert editor.text() == 'Book'
    assert preview.table.item(1, 0).text() == 'Paper'
    QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert editor.text() == 'Paper'
    assert preview.table.rowCount() == 2


def test_gutter_hint_click_selects_reported_line_without_changing_text(preview, monkeypatch):
    import ctrlcctrlv.ui as ui
    hint = SyntaxHint('Python', 2, 'bad syntax')
    preview.show_syntax_hint(preview.syntax_revision, hint)
    block = preview.text.document().findBlockByNumber(1)
    top = preview.text.blockBoundingGeometry(block).translated(preview.text.contentOffset()).top()
    details = []
    monkeypatch.setattr(ui.QToolTip, 'showText', lambda position, text, widget: details.append(text))
    QTest.mouseClick(preview.text.gutter, Qt.MouseButton.LeftButton, pos=QPoint(5, round(top + 7)))
    assert preview.text.textCursor().blockNumber() == 1
    assert details == [hint.label]
    assert preview.text.toPlainText() == 'a = 1\nb = 2\n'
    preview.text.insertPlainText('#')
    assert preview.text.syntax_hint is None


def test_review_characters_after_crlf_and_emoji_are_highlighted_correctly(preview):
    from ctrlcctrlv.comparison import show_comparison
    result = ReviewResult('🙂\r\na = 1\r\n', '🙂\r\na = 2\r\n', 'test', .1)
    show_comparison(preview.before_text, preview.after_text, result)
    assert len(preview.before_text.extraSelections()) == 2
    assert len(preview.after_text.extraSelections()) == 2
    assert [selection.cursor.selectedText() for selection in preview.before_text.extraSelections() if selection.cursor.hasSelection()] == ['1']
    assert [selection.cursor.selectedText() for selection in preview.after_text.extraSelections() if selection.cursor.hasSelection()] == ['2']


def test_review_scrolls_both_panes_together(preview):
    original = '\n'.join(f'line {n}' for n in range(120))
    preview.text.setPlainText(original)
    preview.propose(ReviewResult(original, original.replace('line 60', 'line sixty'), 'test', .1))
    QApplication.instance().processEvents()
    preview.before_text.verticalScrollBar().setValue(50)
    assert preview.after_text.verticalScrollBar().value() == 50
    preview.after_text.verticalScrollBar().setValue(30)
    assert preview.before_text.verticalScrollBar().value() == 30


def test_table_save_shortcut_offers_both_export_formats(preview):
    preview.display(QImage(10, 10, QImage.Format.Format_RGB32), 'Name,Qty\nPaper,1\n', .1, 'test')
    preview.save_shortcut.activated.emit()
    assert preview.table_save_menu.isVisible()
    assert [action.text() for action in preview.table_save_menu.actions()] == ['Сохранить CSV…', 'Сохранить XLSX…']
    preview.table_save_menu.hide()


def test_save_dialog_starts_in_last_directory_with_timestamp_and_cancel_keeps_state(preview, monkeypatch, tmp_path):
    import re
    import ctrlcctrlv.ui as ui
    state = State(last_save_directory=str(tmp_path))
    preview.set_ui_state(state)
    def cancel(dialog):
        assert dialog.directory().absolutePath() == tmp_path.as_posix()
        assert re.fullmatch(r'Снимок-\d{4}-\d{2}-\d{2}-\d{6}\.py',
                            Path(dialog.selectedFiles()[0]).name)
        return ui.QDialog.DialogCode.Rejected
    monkeypatch.setattr(ui.QFileDialog, 'exec', cancel)
    preview.save_current_text()
    assert state.get('last_save_directory') == str(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('table', [False, True])
def test_compact_preview_keeps_toolbar_labels_and_controls_fully_visible(preview, table):
    if table:
        preview.display(QImage(600, 300, QImage.Format.Format_RGB32), 'Name,Qty\nPaper,1\n', .1, 'test')
    preview.resize(840, 620)
    QApplication.instance().processEvents()
    assert preview.width() == 840 and preview.height() == 620
    controls = [preview.fit_image, preview.image_zoom, preview.actual_size]
    controls += ([preview.export_csv_button, preview.export_xlsx_button] if table else
                 [preview.syntax_language, preview.format_button, preview.save_file_button])
    for control in controls:
        assert control.isVisible()
        assert control.width() >= control.sizeHint().width(), control.text() if hasattr(control, 'text') else control.accessibleName()
        assert control.parentWidget().rect().contains(control.geometry())


@pytest.mark.parametrize('decision, allowed', [(QMessageBox.StandardButton.Cancel, False),
                                             (QMessageBox.StandardButton.Discard, True)])
def test_replace_guard_handles_hidden_draft_without_changing_it(preview, monkeypatch, decision, allowed):
    preview.replace_text('unsaved draft')
    preview.hide()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: decision)
    assert preview.confirm_replace_result() is allowed
    assert preview.text.toPlainText() == 'unsaved draft'


@pytest.mark.parametrize('saved', [False, True])
def test_replace_guard_only_allows_save_after_successful_write(preview, monkeypatch, tmp_path, saved):
    preview.replace_text('unsaved draft')
    target = tmp_path / 'draft.txt' if saved else tmp_path / 'missing' / 'draft.txt'
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: None)
    monkeypatch.setattr(preview, 'choose_text_path', lambda _: str(target))
    assert preview.confirm_replace_result() is saved
    assert target.exists() is saved
    assert preview.text.toPlainText() == 'unsaved draft'
    if saved:
        assert target.read_text() == 'unsaved draft'


def test_replace_guard_cancelled_save_preserves_draft(preview, monkeypatch):
    preview.replace_text('unsaved draft')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(preview, 'choose_text_path', lambda _: None)
    assert preview.confirm_replace_result() is False
    assert preview.text.toPlainText() == 'unsaved draft'


def test_replace_guard_does_not_prompt_for_original_or_already_copied_edits(preview, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: pytest.fail('No unsaved draft'))
    assert preview.confirm_replace_result() is True
    preview.replace_text('already copied')
    preview.mark_copied('already copied')
    assert preview.confirm_replace_result() is True


@pytest.mark.parametrize('kind', ['csv', 'xlsx'])
def test_replace_guard_commits_active_table_cell_and_saves_chosen_format(preview, monkeypatch, tmp_path, kind):
    preview.display(QImage(10, 10, QImage.Format.Format_RGB32), 'Name,Qty\nPaper,1\n', .1, 'test')
    preview.table.editItem(preview.table.item(1, 1))
    preview.table.findChild(QLineEdit).setText('00123')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Save)
    target = tmp_path / ('draft.' + kind)
    def accept(dialog):
        assert dialog.nameFilters() == ['CSV (*.csv)', 'XLSX (*.xlsx)']
        dialog.selectNameFilter(kind.upper() + ' (*.' + kind + ')')
        dialog.filterSelected.emit(dialog.selectedNameFilter())
        dialog.selectFile(str(target))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QFileDialog, 'exec', accept)
    assert preview.confirm_replace_result() is True
    assert target.exists()
    assert preview.text.toPlainText() == 'Name,Qty\nPaper,00123\n'
    assert preview.original_text == 'Name,Qty\nPaper,1\n'
    if kind == 'csv':
        assert '00123' in target.read_text(encoding='utf-8-sig')
