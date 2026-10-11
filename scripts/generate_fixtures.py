"""Generate 20 reproducible editor-style screenshots with literal ground truth."""
import argparse
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter
from PySide6.QtWidgets import QApplication

SAMPLES = {
    "python": 'def greet(name):\n    # Приветствие\n    if name == "Женя":\n        print(f"Hello, {name}!")\n\n    return {"ok": True, "value": None}\n',
    "sql": 'SELECT id, name, COUNT(*) AS total\nFROM users\nWHERE name = \'Иван\' AND active = 1\nGROUP BY id, name;\n',
    "json": '{\n    "name": "Тест",\n    "enabled": true,\n    "values": [0, 1, 2],\n    "path": "C:\\\\data"\n}\n',
    "shell": '#!/bin/sh\nvalue="a|b"\nprintf \'%s\\n\' "$value"\n# [] {} () _ | : ; , .\n',
    "text": 'Перенос текста из удалённого редактора.\nEnglish and русский в одной строке.\n\n    Строка с четырьмя пробелами.\n',
}


def extended_samples():
    samples = {}
    for complexity, count in (("medium", 6), ("long", 20)):
        python = 'def collect(values):\n    result = []\n'
        for index in range(count):
            python += f'    # Проверка {index + 1}\n    if len(values) > {index}:\n        result.append(("item_{index}", values[{index}]))\n'
        python += '    return result\n'
        sql = 'SELECT\n'
        sql += ',\n'.join(f"    SUM(CASE WHEN category = 'group_{i}' THEN amount ELSE 0 END) AS total_{i}" for i in range(count))
        sql += "\nFROM transactions\nWHERE active = 1\n    AND owner = 'Иван'\nGROUP BY account_id;\n"
        document = {"name": "Тест", "enabled": True, "items": {f"item_{i}": {"value": i, "active": i % 2 == 0} for i in range(count)}}
        samples[f"python-{complexity}"] = python
        samples[f"sql-{complexity}"] = sql
        samples[f"json-{complexity}"] = json.dumps(document, ensure_ascii=False, indent=4) + "\n"
    return samples


def generate(directory, expanded=False):
    directory.mkdir(parents=True, exist_ok=True)
    manifest = []
    samples = dict(SAMPLES)
    if expanded:
        samples.update(extended_samples())
    for name, text in samples.items():
        for dark in (False, True):
            for size in ((16,) if name not in SAMPLES else (14, 20)):
                identifier = f"{name}-{'dark' if dark else 'light'}-{size}"
                lines = text.split("\n")
                font = QFont("Menlo")
                font.setPixelSize(size)
                metrics = QFontMetricsF(font)
                width = max(1000, int(max(metrics.horizontalAdvance(line) for line in lines)) + 48)
                image = QImage(width, max(180, (len(lines) + 2) * (size + 8)), QImage.Format.Format_RGB32)
                image.fill(QColor("#1e1e1e" if dark else "#ffffff"))
                painter = QPainter(image)
                painter.setFont(font)
                painter.setPen(QColor("#eeeeee" if dark else "#161616"))
                for row, line in enumerate(lines):
                    painter.drawText(24, 30 + row * (size + 8), line)
                painter.end()
                image.save(str(directory / (identifier + ".png")))
                (directory / (identifier + ".txt")).write_text(text, encoding="utf-8")
                manifest.append({"id": identifier, "image": identifier + ".png", "text": identifier + ".txt",
                                 "complexity": name.split("-")[-1] if name not in SAMPLES else "short",
                                 "width": image.width(), "height": image.height(), "font_pixels": size,
                                 "characters": len(text), "lines": len(text.splitlines())})
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, nargs="?", default=Path("artifacts/fixtures"))
    parser.add_argument("--expanded", action="store_true")
    args = parser.parse_args()
    app = QApplication([])
    print(f"Generated {len(generate(args.directory, args.expanded))} fixtures in {args.directory}")
