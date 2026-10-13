"""Small, palette-aware workspace visuals without changing item data."""
from datetime import datetime
from math import cos, sin, pi

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .theme import colours


def _draw_navigation(painter, name):
    if name == "work":
        for x, y, dx, dy in ((3, 3, 4, 4), (17, 3, -4, 4),
                             (3, 17, 4, -4), (17, 17, -4, -4)):
            painter.drawLine(QPointF(x, y), QPointF(x + dx, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + dy))
        painter.drawLine(QPointF(6, 10), QPointF(14, 10))
    elif name == "models":
        for y in (4, 8, 12):
            path = QPainterPath(QPointF(3, y + 2))
            path.lineTo(10, y - 1)
            path.lineTo(17, y + 2)
            path.lineTo(10, y + 5)
            path.closeSubpath()
            painter.drawPath(path)
    elif name == "settings":
        painter.drawEllipse(QRectF(5, 5, 10, 10))
        painter.drawEllipse(QRectF(8, 8, 4, 4))
        for step in range(8):
            angle = step * pi / 4
            painter.drawLine(QPointF(10 + 5 * cos(angle), 10 + 5 * sin(angle)),
                             QPointF(10 + 7.5 * cos(angle), 10 + 7.5 * sin(angle)))
    else:  # history
        painter.drawEllipse(QRectF(3, 3, 14, 14))
        painter.drawLine(QPointF(10, 6), QPointF(10, 10))
        painter.drawLine(QPointF(10, 10), QPointF(13, 12))


def navigation_icon(name: str) -> QIcon:
    """Return a 20px icon with native-resolution 1x, 2x and 3x variants."""
    if name not in {"work", "models", "settings", "history"}:
        raise ValueError(f"Unknown navigation icon: {name}")
    c = colours()
    icon = QIcon()
    for mode, colour in ((QIcon.Mode.Normal, c["muted"]),
                         (QIcon.Mode.Active, c["accent"]),
                         (QIcon.Mode.Selected, c["accent"]),
                         (QIcon.Mode.Disabled, c["muted"])):
        for ratio in (1, 2, 3):
            pixmap = QPixmap(20 * ratio, 20 * ratio)
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if mode == QIcon.Mode.Disabled:
                painter.setOpacity(0.55)
            painter.setPen(QPen(QColor(colour), 1.5, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            _draw_navigation(painter, name)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


class HistoryItemDelegate(QStyledItemDelegate):
    """Paint history hierarchy; the model remains the accessible source of text."""

    def paint(self, painter, option, index):
        painter.save()
        painter.setClipRect(option.rect)
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, option.widget)
        c = colours()
        entry = index.data(Qt.ItemDataRole.UserRole)
        rect = option.rect.adjusted(10, 0, -10, 0)
        small = QFont(option.font)
        small.setPixelSize(11)
        small.setWeight(QFont.Weight.Normal)
        painter.setFont(small)
        painter.setPen(QColor(c["muted"]))
        if entry is None:
            small.setWeight(QFont.Weight.DemiBold)
            painter.setFont(small)
            text = QFontMetrics(small).elidedText(str(index.data() or ""), Qt.TextElideMode.ElideRight, max(0, rect.width()))
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        else:
            kinds = {"ocr": "OCR", "edit": "Правки OCR", "typing": "Печать"}
            try:
                date = datetime.fromisoformat(entry.created).astimezone().strftime("%H:%M")
            except (ValueError, TypeError):
                date = ""
            metadata = " · ".join(part for part in (date, kinds.get(entry.kind, entry.kind)) if part)
            metadata = QFontMetrics(small).elidedText(metadata, Qt.TextElideMode.ElideRight, max(0, rect.width()))
            painter.drawText(rect.adjusted(0, 7, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, metadata)
            body = QFont(option.font)
            body.setPixelSize(13)
            body.setWeight(QFont.Weight.Normal)
            painter.setFont(body)
            painter.setPen(QColor(c["text"]))
            excerpt = entry.text[:240].replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ↵ ")
            if len(entry.text) > 240:
                excerpt += "…"
            excerpt = QFontMetrics(body).elidedText(excerpt, Qt.TextElideMode.ElideRight, max(0, rect.width()))
            painter.drawText(rect.adjusted(0, 25, 0, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, excerpt)
        painter.restore()
