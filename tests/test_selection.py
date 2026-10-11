from types import SimpleNamespace

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.ui import SelectionOverlay


def test_retina_selection_crops_physical_pixels_and_clamps_to_screen():
    app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(200, 160)
    pixmap.fill(QColor("#245b85"))
    pixmap.setDevicePixelRatio(2)
    screen = SimpleNamespace(geometry=lambda: QRect(-100, 0, 100, 80))
    overlay = SelectionOverlay(screen, pixmap)
    images = []
    overlay.selected.connect(images.append)
    def event(x, y):
        return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton,
                               position=lambda: QPointF(x, y))
    try:
        overlay.mousePressEvent(event(20, 10))
        overlay.mouseReleaseEvent(event(120, 90))
        assert len(images) == 1
        assert images[0].width() == 160
        assert images[0].height() == 140
        assert images[0].devicePixelRatio() == 1
        assert images[0].pixelColor(159, 139) == QColor("#245b85")
    finally:
        overlay.close()
        overlay.deleteLater()
        app.sendPostedEvents()
