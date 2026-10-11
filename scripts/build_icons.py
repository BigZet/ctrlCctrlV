"""Reproducible vector drawing for application and macOS template icons."""
from pathlib import Path
import subprocess
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen


ASSETS = Path(__file__).resolve().parents[1] / "src" / "ctrlcctrlv" / "assets"


def draw(size, template=False):
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 1024, size / 1024)
    if not template:
        gradient = QLinearGradient(100, 50, 920, 950)
        gradient.setColorAt(0, QColor("#168BA5"))
        gradient.setColorAt(1, QColor("#214AB3"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(24, 24, 976, 976), 216, 216)
    color = QColor("black" if template else "white")
    pen = QPen(color, 58 if template else 44, Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Four capture corners surrounding a clipboard and transfer arrow.
    for x, y, dx, dy in ((188, 218, 1, 1), (836, 218, -1, 1),
                          (188, 806, 1, -1), (836, 806, -1, -1)):
        path = QPainterPath(QPointF(x + dx * 112, y))
        path.lineTo(x, y)
        path.lineTo(x, y + dy * 112)
        painter.drawPath(path)
    painter.drawRoundedRect(QRectF(338, 298, 348, 420), 40, 40)
    painter.setBrush(QColor("black" if template else "#18799F"))
    painter.drawRoundedRect(QRectF(420, 264, 184, 80), 24, 24)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    arrow = QPainterPath(QPointF(416, 516))
    arrow.lineTo(604, 516)
    painter.drawPath(arrow)
    arrow = QPainterPath(QPointF(536, 448))
    arrow.lineTo(604, 516)
    arrow.lineTo(536, 584)
    painter.drawPath(arrow)
    painter.end()
    return image


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    draw(1024).save(str(ASSETS / "app.png"))
    draw(64, template=True).save(str(ASSETS / "menu.png"))
    for name, points, color in (
        ("check", [(7, 17), (13, 23), (25, 9)], "white"),
        ("chevron", [(7, 12), (16, 21), (25, 12)], "#8494ac"),
    ):
        glyph = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
        glyph.fill(Qt.GlobalColor.transparent)
        painter = QPainter(glyph)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(*point)
        painter.drawPath(path)
        painter.end()
        glyph.save(str(ASSETS / (name + ".png")))
    with tempfile.TemporaryDirectory() as temporary:
        iconset = Path(temporary) / "App.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                suffix = "@2x" if scale == 2 else ""
                draw(size * scale).save(str(iconset / f"icon_{size}x{size}{suffix}.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "app.icns")], check=True)


if __name__ == "__main__":
    main()
