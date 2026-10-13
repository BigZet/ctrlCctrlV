"""Comparison scrolling must move the displayed document, not just its thumb."""
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPlainTextEdit, QWidget

from ctrlcctrlv.comparison import synchronize_scroll
from ctrlcctrlv.ui import CodeTextEdit


@pytest.fixture
def panes():
    window = QWidget()
    layout = QHBoxLayout(window)
    before, after = CodeTextEdit(), CodeTextEdit()
    content = '\n'.join(f'record {number:03d} ' + 'value ' * 180 for number in range(1, 241))
    for editor in (before, after):
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText(content)
        layout.addWidget(editor)
    window.resize(960, 360)
    window.show()
    QApplication.instance().processEvents()
    synchronize_scroll(before, after)
    yield before, after
    window.close()
    window.deleteLater()


@pytest.mark.parametrize('source_index', [0, 1])
def test_vertical_scroll_moves_other_panes_visible_records(panes, source_index):
    source, target = panes[source_index], panes[1 - source_index]
    source.verticalScrollBar().setValue(106)
    QApplication.instance().processEvents()
    assert source.verticalScrollBar().value() == target.verticalScrollBar().value() == 106
    assert source.firstVisibleBlock().text().startswith('record 107 ')
    assert target.firstVisibleBlock().text().startswith('record 107 ')
    assert target.cursorForPosition(QPoint(2, 2)).blockNumber() == 106


@pytest.mark.parametrize('source_index', [0, 1])
def test_horizontal_scroll_moves_other_panes_visible_columns(panes, source_index):
    source, target = panes[source_index], panes[1 - source_index]
    source.horizontalScrollBar().setValue(180)
    QApplication.instance().processEvents()
    assert source.horizontalScrollBar().value() == target.horizontalScrollBar().value() == 180
    source_column = source.cursorForPosition(QPoint(2, 8)).positionInBlock()
    assert source_column > 0
    assert target.cursorForPosition(QPoint(2, 8)).positionInBlock() == source_column
    assert target.contentOffset().x() == source.contentOffset().x()
