import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtWidgets import QMessageBox
from ctrlcctrlv.ui import Preview


@pytest.fixture
def preview():
    window = Preview()
    yield window
    window.close()
    window.deleteLater()


def display(window, text, mode='auto'):
    window.display(QImage(50, 20, QImage.Format.Format_RGB32), text, .1, 'test', mode)


def test_prose_can_be_switched_to_text_without_ocr(preview):
    original = 'Hello, world.\nGoodbye, friend.\n'
    display(preview, original)
    requested = []
    preview.review_requested.connect(lambda: requested.append(True))
    assert preview.set_result_view('text')
    preview.replace_text(original + 'More, prose.\n')
    assert not preview.is_csv
    assert preview.copied_text == original
    assert preview.original_text == original
    assert requested == []
    preview.text.undo()
    assert preview.text.toPlainText() == original


def test_view_switch_keeps_current_edits_and_undo(preview):
    original = 'Name,Value\n"two\nlines",001\n'
    display(preview, original)
    preview.table.editItem(preview.table.item(1, 1))
    preview.table.findChild(QLineEdit).setText('002')
    assert preview.set_result_view('text')
    assert preview.text.toPlainText() == 'Name,Value\n"two\nlines",002\n'
    assert preview.set_result_view('table')
    preview.text.undo()
    assert preview.table.item(1, 1).text() == '001'
    preview.text.redo()
    assert preview.table.item(1, 1).text() == '002'


def test_manual_table_accepts_one_cell_and_rejects_invalid_csv(preview):
    display(preview, '001', 'text')
    assert preview.set_result_view('table')
    assert preview.table.item(0, 0).text() == '001'
    assert preview.format_mode == 'text'
    preview.set_result_view('text')
    preview.replace_text('"unterminated')
    assert not preview.set_result_view('table')
    assert not preview.is_csv
    assert preview.text.toPlainText() == '"unterminated'
    assert 'CSV' in preview.status.text()
    display(preview, '')
    assert preview.result_view == 'auto'
    assert not preview.is_csv


def test_view_switch_does_not_create_undo_or_reserialize_crlf(preview):
    display(preview, 'a,b\r\n"x,y",\r\n')
    before = preview.text.toPlainText()
    assert not preview.text.document().isUndoAvailable()
    preview.set_result_view('text')
    preview.set_result_view('table')
    assert preview.text.toPlainText() == before
    assert not preview.text.document().isUndoAvailable()


def test_auto_switch_parses_current_document_instead_of_original(preview):
    display(preview, 'ordinary prose', 'text')
    preview.set_result_view('text')
    preview.replace_text('Name,Qty\nPaper,001\n')
    assert preview.set_result_view('auto')
    assert preview.is_csv
    assert preview.table.item(1,1).text() == '001'
    assert preview.original_text == 'ordinary prose'
    assert preview.format_mode == 'text'


def test_crlf_view_switch_does_not_make_untouched_result_unsaved(preview,monkeypatch):
    original = 'Name,Value\r\n"two\r\nlines",001\r\n'
    display(preview,original)
    preview.set_result_view('text')
    preview.set_result_view('table')
    monkeypatch.setattr(QMessageBox,'question',lambda *args: pytest.fail('Untouched CRLF result is not a draft'))
    assert preview.confirm_replace_result()
    assert preview.original_text == preview.copied_text == original


@pytest.mark.parametrize('source',['001','print("hello")'])
def test_automatic_csv_remains_conservative_but_manual_one_cell_is_available(preview,source):
    display(preview,source,'csv')
    assert not preview.is_csv
    assert preview.text.toPlainText() == source
    assert preview.set_result_view('table')
    assert preview.table.item(0,0).text() == source
    assert preview.set_result_view('auto')
    assert not preview.is_csv
    assert preview.text.toPlainText() == source
    assert not preview.text.document().isUndoAvailable()
