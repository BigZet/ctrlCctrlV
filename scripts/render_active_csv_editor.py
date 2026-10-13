"""Synthetic active-cell background evidence; no clipboard or provider calls."""
import argparse
import json
import os
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLineEdit
from ctrlcctrlv import ui, theme
from ctrlcctrlv.ui import Preview


def active_preview(dark):
    app = QApplication.instance()
    palette = QPalette(app.palette())
    palette.setColor(QPalette.ColorRole.Window, QColor('#111318' if dark else '#f5f6f8'))
    app.setPalette(palette)
    window = Preview()
    window.display_history('Name,Qty\nPaper,002\n')
    window.set_result_view('table')
    window.resize(840,620)
    app.processEvents()
    table = window.table
    table.setColumnWidth(1,240)
    item = table.item(1,1)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    table.setCurrentCell(1,1)
    table.editItem(item)
    app.processEvents()
    editor = table.cell_delegate.active_editor
    assert isinstance(editor,QLineEdit)
    editor.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    editor.setText('003')
    editor.deselect()
    editor.setCursorPosition(3)
    app.processEvents()
    return window,editor


def inspect_background(window,editor):
    table = window.table
    image = table.viewport().grab().toImage()
    ratio=image.devicePixelRatio()
    box=editor.geometry()
    rect=QRect(round((box.right()-64)*ratio),round((box.center().y()-7)*ratio),round(48*ratio),round(14*ratio))
    panel=QColor(theme.colours()['panel'])
    total=rect.width()*rect.height()
    panel_pixels=sum(image.pixelColor(x,y).rgb()==panel.rgb() for x in range(rect.left(),rect.right()+1) for y in range(rect.top(),rect.bottom()+1))
    return dict(panel_fraction=panel_pixels/total, stored_value=table.item(1,1).text(), editor_value=editor.text(), background=panel.name(), probe_rect=[rect.x(),rect.y(),rect.width(),rect.height()])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    app=QApplication([])
    original=ui.fit_window_to_screen
    ui.fit_window_to_screen=lambda window,available=None: original(window,QRect(0,0,1400,900))
    for dark in (False,True):
        name='dark' if dark else 'light'
        window,editor=active_preview(dark)
        window.grab().save(str(args.output/f'active-editor-{name}.png'))
        data=inspect_background(window,editor)
        exported=args.output/f'committed-{name}.csv'
        assert window.export_current_table('csv',exported)
        data['exported_text']=exported.read_text(encoding='utf-8-sig')
        window.grab().save(str(args.output/f'committed-editor-{name}.png'))
        (args.output/f'active-editor-{name}.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
        print(name,json.dumps(data))
        window.close()
        window.deleteLater()
        app.processEvents()

if __name__=='__main__':
    main()
