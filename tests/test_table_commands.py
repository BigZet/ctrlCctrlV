import csv
import io
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTableWidgetSelectionRange, QLineEdit
from PySide6.QtWidgets import QApplication
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from ctrlcctrlv.ui import Preview


@pytest.fixture
def preview():
    window = Preview()
    window.display_history('Name,Qty\nPaper,001\nBook,002\n')
    yield window
    window.close()
    window.deleteLater()


@pytest.mark.parametrize('command,args,expected', [
    ('insert_rows', (1,), [['Name','Qty'],['',''],['Paper','001'],['Book','002']]),
    ('insert_columns', (1,), [['Name','','Qty'],['Paper','','001'],['Book','','002']]),
    ('delete_rows', ([0,2],), [['Paper','001']]),
    ('delete_columns', ([0,1],), [['']]),
])
def test_structural_command_publishes_once_and_undoes_once(preview, command, args, expected):
    published = []
    preview.table.csv_changed.connect(published.append)
    original = preview.text.toPlainText()
    getattr(preview.table, command)(*args)
    assert list(csv.reader(io.StringIO(preview.text.toPlainText()))) == expected
    assert len(published) == 1
    preview.text.undo()
    assert preview.text.toPlainText() == original
    preview.text.redo()
    assert list(csv.reader(io.StringIO(preview.text.toPlainText()))) == expected


def test_delete_all_rows_retains_explicit_empty_cell(preview):
    preview.set_result_view('table')
    preview.table.delete_rows([0,1,2])
    assert preview.text.toPlainText() == '""\n'
    assert preview.is_csv
    assert preview.table.rowCount() == preview.table.columnCount() == 1
    preview.text.undo()
    assert preview.table.rowCount() == 3


def test_delete_selection_is_one_command_and_active_editor_keeps_delete(preview):
    preview.table.setRangeSelected(QTableWidgetSelectionRange(1, 0, 2, 1), True)
    QTest.keyClick(preview.table, Qt.Key.Key_Delete)
    assert preview.text.toPlainText() == 'Name,Qty\n,\n,\n'
    preview.text.undo()
    preview.table.editItem(preview.table.item(1, 1))
    editor = preview.table.findChild(QLineEdit)
    editor.selectAll()
    QTest.keyClick(editor, Qt.Key.Key_Delete)
    assert editor.text() == ''
    assert preview.table.item(1, 1).text() == '001'


def test_structural_command_commits_pending_cell(preview):
    preview.table.editItem(preview.table.item(1, 1))
    preview.table.findChild(QLineEdit).setText('0003')
    preview.table.insert_rows(2)
    assert preview.text.toPlainText() == 'Name,Qty\nPaper,0003\n,\nBook,002\n'
    preview.text.undo()
    assert preview.text.toPlainText() == 'Name,Qty\nPaper,0003\nBook,002\n'


@pytest.mark.parametrize('kind',['csv','xlsx'])
def test_structural_edit_export_preserves_literal_cells(preview,tmp_path,kind):
    preview.display_history('Code,Amount,Note,Formula\n001,"12,50","quote ""here""\nline 2",=SUM(A1:A2)\n')
    preview.table.insert_rows(1)
    path = tmp_path / ('edited.'+kind)
    assert preview.export_current_table(kind,path)
    expected = [['Code','Amount','Note','Formula'],['','','',''],
                ['001','12,50','quote "here"\nline 2','=SUM(A1:A2)']]
    if kind == 'csv':
        with path.open(encoding='utf-8-sig',newline='') as stream:
            assert list(csv.reader(stream)) == expected
    else:
        ns = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with ZipFile(path) as archive:
            strings = [''.join(item.itertext()) for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            assert not sheet.findall('.//s:f',ns)
            actual = [[strings[int(cell.find('s:v',ns).text)] for cell in row] for row in sheet.find('s:sheetData',ns)]
            assert actual == expected
    preview.text.undo()
    assert preview.table.rowCount() == 2


def test_manual_column_width_survives_edits_and_resets_for_new_result(preview):
    preview.resize(960,500)
    QApplication.instance().processEvents()
    preview.table.setColumnWidth(0,210)
    preview.table.item(1,1).setText('003')
    assert preview.table.columnWidth(0) == 210
    preview.display_history('Name,Qty\nNew,1\n')
    QApplication.instance().processEvents()
    assert preview.table.columnWidth(0) > 210
