"""Build labelled evidence sheets from existing UI renders; never rerenders widgets."""
import argparse
import json
import os
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
app = QApplication([])
from ctrlcctrlv.fonts import prepare_fonts
prepare_fonts()
parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
parser.add_argument('states', nargs='+')
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--logical-size', action='store_true')
args = parser.parse_args()
manifest_paths = list(args.directory.glob('manifest-*.json'))
if len(manifest_paths) != 1:
    raise SystemExit('Exactly one manifest is required')
manifest = json.loads(manifest_paths[0].read_text(encoding='utf-8'))
frames = {(frame['state'], frame['theme']): frame for frame in manifest['frames']}
def canvas(width, height):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor('#d7dfeb'))
    return image
panels, sources = [], []
for state in args.states:
    row = []
    for theme in ('light', 'dark'):
        frame = frames[(state, theme)]
        source = args.directory / frame['screenshot']
        image = QImage(str(source))
        image.setDevicePixelRatio(1)
        if args.logical_size:
            scale = frame['device_pixel_ratio']
            image = image.scaled(round(image.width() / scale), round(image.height() / scale), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        panel = canvas(image.width(), image.height() + 26)
        painter = QPainter(panel)
        painter.drawImage(0, 26, image)
        painter.setPen(QColor('#101827'))
        painter.setFont(QFont('Segoe UI', 9))
        painter.drawText(8, 18, source.name)
        painter.end()
        row.append(panel)
        sources.append(str(source.resolve()))
    joined = canvas(sum(p.width() for p in row) + 8, max(p.height() for p in row))
    painter = QPainter(joined)
    x = 0
    for panel in row:
        painter.drawImage(x, 0, panel)
        x += panel.width() + 8
    painter.end()
    panels.append(joined)
sheet = canvas(max(p.width() for p in panels), sum(p.height() for p in panels) + 8 * (len(panels) - 1))
painter = QPainter(sheet)
y = 0
for panel in panels:
    painter.drawImage(0, y, panel)
    y += panel.height() + 8
painter.end()
args.output.parent.mkdir(parents=True, exist_ok=True)
sheet.save(str(args.output))
args.output.with_suffix('.json').write_text(json.dumps(dict(sources=sources, presentation='logical_resample' if args.logical_size else 'native_pixels'), indent=2), encoding='utf-8')
print(args.output)
