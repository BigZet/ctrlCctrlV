import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit
from ctrlcctrlv.ui import Preview


@pytest.mark.parametrize('text,query,expected', [
    ('😀 Код\r\nкод', 'КОД', [(2, 5), (7, 10)]),
    ('a.b a?b a.b', 'a.b', [(0, 3), (8, 11)]),
    ('Straße strasse', 'STRASSE', [(0, 6), (7, 14)]),
    ('Код', '', []), ('Код', 'нет', []),
])
def test_literal_search_returns_python_offsets(text, query, expected):
    from ctrlcctrlv.result_search import find_spans
    assert find_spans(text, query) == expected


def test_case_sensitive_search():
    from ctrlcctrlv.result_search import find_spans
    assert find_spans('Код код', 'код', True) == [(4, 7)]


@pytest.fixture
def preview():
    window = Preview()
    yield window
    window.close()
    window.deleteLater()


def test_text_search_wraps_emoji_and_does_not_edit_or_undo(preview):
    preview.display_history('😀 Код\r\nкод\t\n')
    before = preview.text.toPlainText()
    preview.open_search()
    preview.search_bar.query.setText('КОД')
    assert preview.search_bar.counter.text() == '1 / 2'
    assert preview.text.extraSelections()[0].cursor.selectedText() == 'Код'
    preview.step_search(1)
    assert preview.search_bar.counter.text() == '2 / 2'
    preview.step_search(1)
    assert preview.search_bar.counter.text() == '1 / 2'
    preview.step_search(-1)
    assert preview.search_bar.counter.text() == '2 / 2'
    QTest.keyClick(preview.search_bar.query, Qt.Key.Key_Escape)
    assert not preview.search_bar.isVisible()
    assert preview.text.toPlainText() == before
    assert not preview.text.document().isUndoAvailable()
    assert preview.copied_text is None


def test_table_search_commits_active_cell_and_uses_row_order(preview):
    preview.display_history('Name,Value\nКод,001\nкод,002\n')
    preview.table.editItem(preview.table.item(2, 0))
    preview.table.findChild(QLineEdit).setText('кодекс')
    preview.open_search()
    preview.search_bar.query.setText('код')
    assert preview.table.currentRow() == 1
    preview.step_search(1)
    assert preview.table.currentRow() == 2
    assert preview.text.toPlainText() == 'Name,Value\nКод,001\nкодекс,002\n'
    preview.close_search()
    preview.text.undo()
    assert preview.table.item(2, 0).text() == 'код'


def test_search_refresh_preserves_typing_cursor_and_undo(preview):
    preview.display_history('match end')
    preview.open_search()
    preview.search_bar.query.setText('match')
    cursor = preview.text.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    preview.text.setTextCursor(cursor)
    preview.text.insertPlainText(' match')
    position = preview.text.textCursor().position()
    QTest.qWait(180)
    assert preview.search_bar.counter.text().endswith('/ 2')
    assert preview.text.textCursor().position() == position
    preview.text.undo()
    assert preview.text.toPlainText() == 'match end'


def test_table_search_navigates_each_occurrence_inside_multiline_emoji_cell(preview):
    preview.display_history('Name,Value\n"😀 Код код\nКОД",001\nкод,002\n')
    original = preview.text.toPlainText()
    publications = []
    preview.table.csv_changed.connect(publications.append)
    preview.open_search()
    preview.search_bar.query.setText('код')
    assert preview.search_bar.counter.text() == '1 / 4'
    assert preview.table.active_search_match == (1,0,2,5)
    QApplication.instance().processEvents()
    rendered = preview.table.grab().toImage()
    active_color = '#a77a2b' if QApplication.palette().window().color().lightness() < 128 else '#f7c854'
    assert any(rendered.pixelColor(x,y).name() == active_color for y in range(rendered.height()) for x in range(rendered.width()))
    preview.step_search(1)
    assert preview.search_bar.counter.text() == '2 / 4'
    assert preview.table.active_search_match == (1,0,6,9)
    assert preview.table.currentRow() == 1
    preview.step_search(1)
    assert preview.table.active_search_match == (1,0,10,13)
    preview.step_search(1)
    assert preview.table.active_search_match == (2,0,0,3)
    preview.step_search(1)
    assert preview.table.active_search_match == (1,0,2,5)
    preview.step_search(-1)
    assert preview.table.active_search_match == (2,0,0,3)
    preview.close_search()
    assert preview.table.active_search_match is None
    assert preview.text.toPlainText() == original
    assert not preview.text.document().isUndoAvailable()
    assert publications == []


def test_active_table_occurrence_remains_visible_in_tall_clipped_cell(preview):
    cell = '\n'.join(f'match line {index}' for index in range(30))
    preview.display_history('Name,Value\n"' + cell + '",001\n')
    preview.open_search()
    preview.search_bar.query.setText('match')
    preview.step_search(-1)
    assert preview.search_bar.counter.text() == '30 / 30'
    QApplication.instance().processEvents()
    rendered = preview.table.grab().toImage()
    active_color = '#a77a2b' if QApplication.palette().window().color().lightness() < 128 else '#f7c854'
    assert any(rendered.pixelColor(x,y).name() == active_color for y in range(rendered.height()) for x in range(rendered.width()))
