from types import SimpleNamespace
import pytest

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


@pytest.mark.parametrize("ratio", [1, 1.25, 1.5, 2])
def test_windows_selection_preserves_pixels_at_fractional_scaling(ratio):
    app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(round(200 * ratio), round(160 * ratio))
    pixmap.fill(QColor("#245b85"))
    pixmap.setDevicePixelRatio(ratio)
    screen = SimpleNamespace(geometry=lambda: QRect(-200, -160, 200, 160))
    overlay = SelectionOverlay(screen, pixmap)
    images = []
    overlay.selected.connect(images.append)
    def event(x, y):
        return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton, position=lambda: QPointF(x, y))
    try:
        overlay.mousePressEvent(event(20, 40))
        overlay.mouseReleaseEvent(event(60, 80))
        assert images[0].width() == round(41 * ratio)
        assert images[0].height() == round(41 * ratio)
        assert images[0].devicePixelRatio() == 1
        assert images[0].pixelColor(0, 0) == QColor("#245b85")
    finally:
        overlay.close()
        overlay.deleteLater()
