import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication
from scripts.render_active_csv_editor import active_preview, inspect_background


@pytest.mark.parametrize('dark',[False,True])
def test_active_editor_hides_original_right_aligned_value_and_exports_edit(dark,tmp_path):
    app=QApplication.instance()
    previous=QPalette(app.palette())
    window,editor=active_preview(dark)
    try:
        evidence=inspect_background(window,editor)
        assert evidence['stored_value']=='002'
        assert evidence['editor_value']=='003'
        assert evidence['panel_fraction'] >= .95, evidence
        destination=tmp_path/'edited.csv'
        assert window.export_current_table('csv',destination)
        assert destination.read_text(encoding='utf-8-sig')=='Name,Qty\nPaper,003\n'
        assert window.table.item(1,1).text()=='003'
    finally:
        window.close()
        window.deleteLater()
        app.setPalette(previous)
