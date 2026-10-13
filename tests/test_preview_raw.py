from ctrlcctrlv.ui import Preview


def test_raw_is_readonly_explicit_undoable_and_session_only():
    window = Preview()
    window.display_history('processed')
    window.set_raw_response('```python\nraw\n```')
    window.show_raw_response()
    assert window.raw_editor.isReadOnly()
    assert window.text.toPlainText() == 'processed'
    window.use_raw_response()
    assert window.text.toPlainText() == '```python\nraw\n```'
    assert window.result_view == 'text'
    assert window.original_text == 'processed'
    assert window.copied_text is None
    window.text.undo()
    assert window.text.toPlainText() == 'processed'
    window.hide()
    assert window.raw_response is None
    assert window.raw_editor.toPlainText() == ''
    assert not window.raw_action.isVisible()
    window.display_history('other')
    assert window.raw_response is None
    window.close()
    window.deleteLater()
