"""Table popups must paint an opaque, readable surface in both themes."""
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QContextMenuEvent, QPalette
from PySide6.QtWidgets import QApplication, QMenu

from ctrlcctrlv import csv_editor
from ctrlcctrlv.ui import Preview


def contrast(first, second):
    def luminance(color):
        channels = [color.redF(), color.greenF(), color.blueF()]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                  for value in channels]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    values = sorted((luminance(first), luminance(second)))
    return (values[1] + .05) / (values[0] + .05)


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_context_menu_paints_readable_background_and_preserves_row_action(monkeypatch, theme):
    app = QApplication.instance()
    original_palette = QPalette(app.palette())
    palette = QPalette(original_palette)
    palette.setColor(QPalette.ColorRole.Window, QColor('#151a23' if theme == 'dark' else '#f5f7fa'))
    app.setPalette(palette)
    window = Preview()
    try:
        window.display_history('Name,Qty\nPaper,001\nBook,002\n')
        window.resize(840, 620)
        app.processEvents()
        captured = []

        # Only the blocking popup loop is replaced; construction, inheritance,
        # polishing, paint and command callbacks all use production widgets.
        class CapturedMenu(QMenu):
            def exec(self, position):
                captured.append(self)

        monkeypatch.setattr(csv_editor, 'QMenu', CapturedMenu)
        window.table.setCurrentCell(1, 1)
        window.table.cell_delegate.commit_pending()
        assert window.table.cell_delegate.active_editor is None
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(10, 10), QPoint(10, 10))
        window.table.contextMenuEvent(event)
        assert len(captured) == 1
        menu = captured[0]
        menu.ensurePolished()
        image = menu.grab().toImage()
        padding = image.pixelColor(7, image.height() // 2)
        foreground = menu.palette().color(QPalette.ColorRole.WindowText)
        assert padding.alpha() == 255, 'Table ancestor CSS must not make the popup transparent'
        assert contrast(padding, foreground) >= 4.5
        view_menu = window.editor_view.menu()
        view_menu.ensurePolished()
        assert padding == view_menu.palette().color(QPalette.ColorRole.Window)
        assert len(menu.actions()) == 7
        published = []
        window.table.csv_changed.connect(published.append)
        menu.actions()[1].trigger()
        assert published == ['Name,Qty\nPaper,001\n,\nBook,002\n']
        window.text.undo()
        assert window.text.toPlainText() == 'Name,Qty\nPaper,001\nBook,002\n'
    finally:
        window.close()
        window.deleteLater()
        app.setPalette(original_palette)
