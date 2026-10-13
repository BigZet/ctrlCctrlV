import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from ctrlcctrlv.capture_adjust import AdjustableSelectionMixin


class CaptureWidget(AdjustableSelectionMixin, QWidget):
    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, ratio=1, adjust=True):
        super().__init__()
        self.resize(200, 150)
        self.snapshot = QPixmap(round(200 * ratio), round(150 * ratio))
        self.snapshot.fill(Qt.GlobalColor.white)
        self.snapshot.setDevicePixelRatio(ratio)
        self.origin = self.position = None
        self.init_capture_adjustment(adjust)
        self.images = []
        self.cancellations = []
        self.selected.connect(self.images.append)
        self.cancelled.connect(lambda: self.cancellations.append(True))

    def selection(self):
        return QRect(self.origin, self.position).normalized() if self.origin is not None else QRect()

    def mousePressEvent(self, event):
        self.handle_adjust_press(event)

    def mouseMoveEvent(self, event):
        self.handle_adjust_move(event)

    def mouseReleaseEvent(self, event):
        self.handle_adjust_release(event)

    def keyPressEvent(self, event):
        self.handle_adjust_key(event)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def capture(app):
    widget = CaptureWidget()
    yield widget
    widget.close()


def drag(widget, start, end):
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(*start))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(*end))


@pytest.mark.parametrize("ratio", [1, 1.5, 2])
def test_adjustment_waits_for_enter_and_crops_physical_pixels(app, ratio):
    widget = CaptureWidget(ratio)
    drag(widget, (10, 20), (49, 59))
    assert widget.images == []
    assert widget.selection() == QRect(10, 20, 40, 40)
    QTest.keyClick(widget, Qt.Key.Key_Return)
    assert len(widget.images) == 1
    assert widget.images[0].size().width() == round(40 * ratio)
    assert widget.images[0].size().height() == round(40 * ratio)
    assert widget.images[0].devicePixelRatio() == 1
    QTest.keyClick(widget, Qt.Key.Key_Return)
    assert len(widget.images) == 1


def test_move_clamps_rectangle_to_screen_without_changing_size(capture):
    drag(capture, (20, 20), (79, 69))
    drag(capture, (40, 40), (240, 200))
    assert capture.selection() == QRect(140, 100, 60, 50)
    drag(capture, (165, 125), (-90, -50))
    assert capture.selection() == QRect(0, 0, 60, 50)
    assert capture.images == []


def test_cropped_pixels_start_at_scaled_selection_origin(app):
    widget = CaptureWidget(2)
    source = QImage(400, 300, QImage.Format.Format_RGB32)
    source.fill(Qt.GlobalColor.white)
    source.setPixelColor(20, 40, QColor("red"))
    source.setPixelColor(99, 119, QColor("blue"))
    widget.snapshot = QPixmap.fromImage(source)
    widget.snapshot.setDevicePixelRatio(2)
    drag(widget, (10, 20), (49, 59))
    QTest.keyClick(widget, Qt.Key.Key_Return)
    assert widget.images[0].pixelColor(0, 0) == QColor("red")
    assert widget.images[0].pixelColor(79, 79) == QColor("blue")


def test_enter_during_drag_does_not_accept_and_hover_preserves_selection(capture):
    drag(capture, (20, 20), (79, 69))
    QTest.mousePress(capture, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
    QTest.keyClick(capture, Qt.Key.Key_Return)
    assert capture.images == []
    QTest.mouseRelease(capture, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))
    QTest.mouseMove(capture, QPoint(120, 100))
    assert capture.selection() == QRect(30, 30, 60, 50)


def test_adjusted_selection_paints_visible_corner_and_edge_handles(capture):
    drag(capture, (20, 20), (79, 69))
    image = QImage(200, 150, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    capture.paint_adjustment(painter)
    painter.end()
    assert image.pixelColor(20, 20).alpha() > 0
    assert image.pixelColor(49, 20).alpha() > 0
    assert image.pixelColor(49, 45).alpha() == 0


@pytest.mark.parametrize("start,end,expected", [
    ((20, 20), (10, 10), QRect(10, 10, 70, 60)),
    ((79, 20), (89, 10), QRect(20, 10, 70, 60)),
    ((20, 69), (10, 79), QRect(10, 20, 70, 60)),
    ((79, 69), (89, 79), QRect(20, 20, 70, 60)),
    ((20, 45), (10, 45), QRect(10, 20, 70, 50)),
    ((79, 45), (89, 45), QRect(20, 20, 70, 50)),
    ((45, 20), (45, 10), QRect(20, 10, 60, 60)),
    ((45, 69), (45, 79), QRect(20, 20, 60, 60)),
])
def test_all_edges_and_corners_resize(capture, start, end, expected):
    drag(capture, (20, 20), (79, 69))
    drag(capture, start, end)
    assert capture.selection() == expected


def test_resize_keeps_minimum_size_and_stays_inside_screen(capture):
    drag(capture, (20, 20), (79, 69))
    drag(capture, (79, 69), (5, 5))
    assert capture.selection() == QRect(20, 20, 3, 3)
    drag(capture, (20, 20), (-20, -20))
    assert capture.rect().contains(capture.selection())


@pytest.mark.parametrize("cancel", ["escape", "right"])
def test_cancel_never_accepts_pending_selection(capture, cancel):
    drag(capture, (20, 20), (79, 69))
    if cancel == "escape":
        QTest.keyClick(capture, Qt.Key.Key_Escape)
    else:
        QTest.mouseClick(capture, Qt.MouseButton.RightButton)
    QTest.keyClick(capture, Qt.Key.Key_Return)
    assert capture.images == []
    assert capture.cancellations == [True]


def test_tiny_selection_can_be_redrawn_before_confirming(capture):
    drag(capture, (20, 20), (21, 21))
    QTest.keyClick(capture, Qt.Key.Key_Return)
    assert capture.images == []
    assert capture.cancellations == []
    drag(capture, (30, 30), (49, 49))
    QTest.keyClick(capture, Qt.Key.Key_Enter)
    assert len(capture.images) == 1


def test_disabled_adjustment_does_not_consume_legacy_events(app):
    widget = CaptureWidget(adjust=False)
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from PySide6.QtCore import QEvent, QPointF
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(10, 10), QPointF(10, 10),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    assert not widget.handle_adjust_press(event)
    assert not widget.handle_adjust_move(event)
    assert not widget.handle_adjust_release(event)
    assert not widget.handle_adjust_key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                                  Qt.KeyboardModifier.NoModifier))
    assert widget.origin is None
