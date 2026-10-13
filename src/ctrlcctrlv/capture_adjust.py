"""Optional capture correction for the existing selection overlay.

The host supplies snapshot, origin/position, selection(), selected/cancelled,
and the QWidget methods. Event handlers return whether they consumed the event;
when disabled, the host's quick-capture behavior continues unchanged.
"""

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPen
from .capture_region import physical_rect


class AdjustableSelectionMixin:
    def init_capture_adjustment(self, adjust=False):
        self.capture_adjust = bool(adjust)
        self._adjust_drag = None
        self._adjust_ready = False
        self._adjust_finished = False
        self._adjust_anchor = QPoint()
        self._adjust_initial = QRect()

    def adjustment_help_text(self):
        if self._adjust_ready:
            return "Переместите область или её границы · Enter — захват · Esc — отмена"
        return "Выделите область · Enter — захват · Esc или правая кнопка — отмена"

    def _adjust_point(self, point):
        bounds = self.rect()
        return QPoint(max(bounds.left(), min(point.x(), bounds.right())),
                      max(bounds.top(), min(point.y(), bounds.bottom())))

    def _adjust_hit(self, point):
        area = self.selection()
        if not self._adjust_ready or not area.adjusted(-6, -6, 6, 6).contains(point):
            return "draw"
        edges = ""
        if abs(point.x() - area.left()) <= 6:
            edges += "l"
        elif abs(point.x() - area.right()) <= 6:
            edges += "r"
        if abs(point.y() - area.top()) <= 6:
            edges += "t"
        elif abs(point.y() - area.bottom()) <= 6:
            edges += "b"
        return edges or ("move" if area.contains(point) else "draw")

    def _adjust_cursor(self, operation):
        shape = {
            "move": Qt.CursorShape.SizeAllCursor,
            "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
            "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
            "lt": Qt.CursorShape.SizeFDiagCursor, "rb": Qt.CursorShape.SizeFDiagCursor,
            "rt": Qt.CursorShape.SizeBDiagCursor, "lb": Qt.CursorShape.SizeBDiagCursor,
        }.get(operation, Qt.CursorShape.CrossCursor)
        self.setCursor(shape)

    def handle_adjust_press(self, event):
        if not self.capture_adjust:
            return False
        if self._adjust_finished:
            return True
        if event.button() == Qt.MouseButton.RightButton:
            self._adjust_cancel()
        elif event.button() == Qt.MouseButton.LeftButton:
            point = self._adjust_point(event.position().toPoint())
            self._adjust_drag = self._adjust_hit(point)
            self._adjust_anchor = point
            self._adjust_initial = self.selection()
            if self._adjust_drag == "draw":
                self.origin = self.position = point
                self._adjust_ready = False
            self._adjust_cursor(self._adjust_drag)
            self.update()
        return True

    def _adjust_update_drag(self, point):
        point = self._adjust_point(point)
        operation = self._adjust_drag
        if operation == "draw":
            self.position = point
        elif operation:
            area = QRect(self._adjust_initial)
            bounds = self.rect()
            delta = point - self._adjust_anchor
            if operation == "move":
                area.moveLeft(max(bounds.left(), min(area.left() + delta.x(),
                                                    bounds.right() - area.width() + 1)))
                area.moveTop(max(bounds.top(), min(area.top() + delta.y(),
                                                  bounds.bottom() - area.height() + 1)))
            else:
                if "l" in operation:
                    area.setLeft(max(bounds.left(), min(area.left() + delta.x(), area.right() - 2)))
                if "r" in operation:
                    area.setRight(min(bounds.right(), max(area.right() + delta.x(), area.left() + 2)))
                if "t" in operation:
                    area.setTop(max(bounds.top(), min(area.top() + delta.y(), area.bottom() - 2)))
                if "b" in operation:
                    area.setBottom(min(bounds.bottom(), max(area.bottom() + delta.y(), area.top() + 2)))
            self.origin, self.position = area.topLeft(), area.bottomRight()
        self.update()

    def handle_adjust_move(self, event):
        if not self.capture_adjust:
            return False
        if not self._adjust_finished:
            point = event.position().toPoint()
            if self._adjust_drag:
                self._adjust_update_drag(point)
            else:
                self._adjust_cursor(self._adjust_hit(point))
        return True

    def handle_adjust_release(self, event):
        if not self.capture_adjust:
            return False
        if (not self._adjust_finished and event.button() == Qt.MouseButton.LeftButton
                and self._adjust_drag):
            self._adjust_update_drag(event.position().toPoint())
            self._adjust_drag = None
            area = self.selection()
            self._adjust_ready = area.width() >= 3 and area.height() >= 3
            self._adjust_cursor(self._adjust_hit(event.position().toPoint()))
            self.update()
        return True

    def _adjust_cancel(self):
        self._adjust_finished = True
        self._adjust_drag = None
        self.cancelled.emit()

    def handle_adjust_key(self, event):
        if not self.capture_adjust:
            return False
        if self._adjust_finished:
            return True
        if event.key() == Qt.Key.Key_Escape:
            self._adjust_cancel()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._adjust_ready and self._adjust_drag is None:
                area = self.selection().intersected(self.rect())
                ratio = self.snapshot.devicePixelRatio()
                physical = physical_rect(area, ratio)
                image = self.snapshot.copy(physical).toImage()
                image.setDevicePixelRatio(1)
                self._adjust_finished = True
                self.selected.emit(image)
        return True

    def paint_adjustment(self, painter):
        if not self.capture_adjust or not self._adjust_ready:
            return
        area = self.selection()
        points = [area.topLeft(), area.topRight(), area.bottomLeft(), area.bottomRight(),
                  QPoint(area.center().x(), area.top()), QPoint(area.center().x(), area.bottom()),
                  QPoint(area.left(), area.center().y()), QPoint(area.right(), area.center().y())]
        painter.save()
        painter.setPen(QPen(QColor("#163651"), 1))
        painter.setBrush(QColor("#d7efff"))
        for point in points:
            painter.drawRect(QRect(point.x() - 3, point.y() - 3, 7, 7))
        painter.restore()
