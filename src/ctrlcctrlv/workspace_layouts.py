"""Small layout primitives shared by the workspace's logical control groups."""
from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import QBoxLayout, QLayout, QSizePolicy, QWidget


class AdaptiveRow(QWidget):
    """Keep logical groups in a row, or stack them when their hints do not fit.

    Children keep their natural heights and widths, shrinking horizontally only
    when an individual group is wider than the available space. The row's
    minimum width is the largest individual minimum, never their sum.
    """

    def __init__(self, *widgets: QWidget, spacing=8, parent=None):
        super().__init__(parent)
        self._reflowing = False
        self._signature = None
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(spacing)
        # The default constraint would propagate the initial horizontal sum as
        # a hard widget minimum before it gets a chance to stack.
        self._box.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._groups = tuple(widgets)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reflow)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        for widget in widgets:
            self._box.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            widget.installEventFilter(self)
        self._box.addStretch(1)

    def _items(self):
        return [self._box.itemAt(index) for index in range(len(self._groups))
                if not self._box.itemAt(index).isEmpty()]

    def _natural_width(self, items):
        return sum(item.sizeHint().width() for item in items) + self._box.spacing() * max(0, len(items) - 1)

    def heightForWidth(self, width):
        items = self._items()
        heights = [item.heightForWidth(min(width, item.sizeHint().width()))
                   if item.hasHeightForWidth() else item.sizeHint().height()
                   for item in items]
        if self._natural_width(items) <= width:
            return max(heights, default=0)
        return sum(heights) + self._box.spacing() * max(0, len(items) - 1)

    def sizeHint(self):
        items = self._items()
        return QSize(self._natural_width(items), self.heightForWidth(self.width()))

    def minimumSizeHint(self):
        items = self._items()
        width = max((item.minimumSize().width() for item in items), default=0)
        return QSize(width, self.heightForWidth(max(width, self.width())))

    def _schedule_reflow(self):
        if not self._reflowing and not self._timer.isActive():
            self._timer.start(0)

    def _reflow(self):
        if self._reflowing:
            return
        self._reflowing = True
        try:
            items = self._items()
            direction = (QBoxLayout.Direction.LeftToRight
                         if self._natural_width(items) <= self.width()
                         else QBoxLayout.Direction.TopToBottom)
            signature = (direction, tuple((item.widget(), item.sizeHint(), item.minimumSize())
                                          for item in items))
            if signature != self._signature:
                self._signature = signature
                self._box.setDirection(direction)
                vertical_alignment = (Qt.AlignmentFlag.AlignVCenter
                                      if direction == QBoxLayout.Direction.LeftToRight
                                      else Qt.AlignmentFlag.AlignTop)
                for widget in self._groups:
                    self._box.setAlignment(widget, Qt.AlignmentFlag.AlignLeft | vertical_alignment)
                self._box.invalidate()
                self.updateGeometry()
                self._box.activate()
        finally:
            self._reflowing = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def showEvent(self, event):
        super().showEvent(event)
        self._reflow()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide,
                            QEvent.Type.FontChange, QEvent.Type.StyleChange,
                            QEvent.Type.LayoutRequest):
            self._schedule_reflow()
        return super().eventFilter(watched, event)

    def event(self, event):
        result = super().event(event)
        # Parent font/style changes and layout requests can arrive after show.
        if hasattr(self, '_timer') and event.type() in (
                QEvent.Type.LayoutRequest, QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._schedule_reflow()
        return result
