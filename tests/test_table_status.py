"""Preview metrics follow the visible editor, including canonical-text undo."""
import pytest
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.ui import Preview


CSV = 'Code,Amount,Note\n001,"12,50","first\nsecond"\n002,7,plain\n'
INSERTED_CSV = 'Code,Amount,Note\n001,"12,50","first\nsecond"\n,,\n002,7,plain\n'


@pytest.fixture
def preview():
    window = Preview()
    window.display_history(CSV)
    window.table.setCurrentCell(1, 0)
    yield window
    window.syntax_timer.stop()
    window.close()
    window.deleteLater()


def test_history_multiline_csv_insert_undo_redo_keeps_visible_table_metrics(preview):
    preview.table.insert_rows(2)
    assert preview.text.toPlainText() == INSERTED_CSV
    assert (preview.table.rowCount(), preview.table.columnCount()) == (4, 3)
    assert preview.text_stats.text().startswith('4 × 3')
    assert preview.status.text() == 'История · есть правки'

    # The table's actual undo action delegates to the canonical text document.
    preview.table_undo.activated.emit()
    QApplication.instance().processEvents()
    assert preview.text.toPlainText() == CSV
    assert preview.is_csv and preview.result_stack.currentWidget() is preview.table
    assert (preview.table.rowCount(), preview.table.columnCount()) == (3, 3)
    assert preview.text_stats.text().startswith('3 × 3')
    assert 'Курсор: строка' not in preview.text_stats.toolTip()
    assert preview.status.text() == 'Открыто из истории'

    preview.table_redo.activated.emit()
    QApplication.instance().processEvents()
    assert preview.text.toPlainText() == INSERTED_CSV
    assert (preview.table.rowCount(), preview.table.columnCount()) == (4, 3)
    assert preview.text_stats.text().startswith('4 × 3')
    assert preview.status.text() == 'История · есть правки'


def test_mark_copied_keeps_table_metrics_and_copy_controls(preview):
    # mark_copied updates bookkeeping only; this test never uses the clipboard.
    preview.mark_copied(CSV)
    assert preview.text_stats.text() == '3 × 3 · 2:1'
    assert preview.copy_button.isEnabled()
    assert preview.copy_state.text() == ''

    preview.table.insert_rows(2)
    preview.mark_copied(CSV)
    assert preview.text_stats.text().startswith('4 × 3')
    assert preview.copy_button.isEnabled()
    assert preview.copy_state.text() == 'Есть правки'
    preview.mark_copied(INSERTED_CSV)
    assert preview.text_stats.text().startswith('4 × 3')
    assert preview.copy_state.text() == ''


def test_switch_from_table_to_text_refreshes_character_metrics(preview):
    assert preview.set_result_view('text')
    assert not preview.is_csv
    assert preview.result_stack.currentWidget() is preview.text
    assert preview.text.toPlainText() == CSV
    assert preview.text_stats.text() == '56 симв. · 5 стр.'
    assert 'Курсор: строка' in preview.text_stats.toolTip()
    assert preview.copy_button.isEnabled()


def test_invalid_csv_fallback_refreshes_text_metrics_and_copy_controls(preview):
    assert preview.set_result_view('table')
    preview.replace_text('Name,Qty\n"broken')
    QApplication.instance().processEvents()
    assert not preview.is_csv
    assert preview.result_stack.currentWidget() is preview.text
    assert preview.text.toPlainText() == 'Name,Qty\n"broken'
    assert preview.text_stats.text() == '16 симв. · 2 стр.'
    assert preview.copy_button.isEnabled()
    assert preview.status.text() == 'История · есть правки'
    preview.replace_text('')
    assert preview.text_stats.text() == '0 симв. · 1 стр.'
    assert not preview.copy_button.isEnabled()
