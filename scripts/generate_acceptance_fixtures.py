"""Generate a separate, explicitly synthetic OCR acceptance corpus.

Six texts, ten code fragments, eight cell tables and six challenging examples.
Uses the current machine's monospace font and records actual geometry. It does
not call OCR, read credentials, capture the display or publish to the clipboard.
"""
import argparse
import csv
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import sys


@dataclass(frozen=True)
class Sample:
    identifier: str
    kind: str
    text: str = ""
    cells: tuple[tuple[str, ...], ...] = ()
    language: str = "en"
    pixels: int = 20
    foreground: str = "#202020"
    background: str = "#ffffff"
    challenging: bool = False

    def reference(self):
        if self.kind != "table":
            return self.text
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\r\n").writerows(self.cells)
        return buffer.getvalue()


def _samples():
    text = [
        ("ru", "Привет! Это синтетический пример обычного текста.\nВторая строка сохраняет знаки: запятая, точка; двоеточие.\n"),
        ("en", "The small clock stood beside the window.\nA quiet street appeared beyond the glass.\n\nThe next paragraph begins after an empty line.\n"),
        ("en", 'Quoted values: "alpha", \'beta\', and (gamma).\nSymbols remain literal: + - * / = < > & %.\n'),
        ("ru", "Расписание\nПонедельник: 09:30 — 18:00\nВторник: 10:00 — 17:45\n\nПерерыв: 13:15–14:00.\n"),
        ("en", "Contact: reader@example.net\nWebsite: https://example.net/docs?v=2&lang=en\nReference: AB-00123; amount: 1,234.50.\n"),
        ("ru", "Точность важнее красивого форматирования.\nСлова Ёж, ёлка и подъезд сохраняют буквы ё и ъ.\nЧисла: 00123; 1,25; -0.75; 10 000.\n"),
    ]
    samples = [Sample(f"text-{i:02}", "text", text=value, language=language)
               for i, (language, value) in enumerate(text, 1)]
    code = [
        ("ru", 'def greet(name: str) -> str:\n    value = f"Привет, {name}!"\n    return value\n\nprint(greet("мир"))\n'),
        ("en", "SELECT category, COUNT(*) AS total\nFROM orders\nWHERE status = 'ready' AND amount >= 1.25\nGROUP BY category\nORDER BY total DESC;\n"),
        ("en", '{\n  "id": "00123",\n  "enabled": true,\n  "tags": ["alpha", "beta"],\n  "amount": "1,25"\n}\n'),
        ("en", "const values = [1, 2, 3];\n\nfunction square(value) {\n  return value * value;\n}\n\nconsole.log(values.map(square));\n"),
        ("en", "type Item = { id: string; active: boolean };\n\nconst item: Item = { id: '00123', active: true };\nif (item.active) {\n  console.log(item.id);\n}\n"),
        ("en", '#!/bin/sh\nset -eu\n\nfor name in alpha beta; do\n    printf \'%s\\n\' "$name"\ndone\n'),
        ("en", '[project]\nname = "sample-project"\nversion = "0.1.0"\n\n[tool.settings]\nenabled = true\nlimit = 25\n'),
        ("en", "items:\n  - id: '00123'\n    title: alpha\n    active: true\n  - id: '00456'\n    title: beta\n    active: false\n"),
        ("en", ".panel {\n  display: grid;\n  gap: 8px;\n  color: #202020;\n}\n\n.panel:focus { outline: 2px solid #2463eb; }\n"),
        ("en", 'public static int Sum(int[] values)\n{\n    int total = 0;\n    foreach (int value in values)\n    {\n        total += value;\n    }\n    return total;\n}\n'),
    ]
    samples.extend(Sample(f"code-{i:02}", "code", text=value, language=language, pixels=18)
                   for i, (language, value) in enumerate(code, 1))
    tables = [
        (("id", "value", "status"), ("00123", "1,25", "ready"), ("00456", "0,50", "wait")),
        (("name", "quantity", "unit"), ("alpha", "10", "pcs"), ("beta", "02", "pcs")),
        (("label", "literal"), ("formula", "=SUM(A1:A3)"), ("prefix", "+001"), ("minus", "-002")),
        (("title", "message"), ("quote", 'He said "hello"'), ("comma", "alpha,beta")),
        (("name", "note"), ("alpha", "first line\nsecond line"), ("beta", "single line")),
        (("код", "название", "остаток"), ("0007", "ёлка", "12"), ("0042", "бумага", "05")),
        (("key", "empty", "value"), ("A", "", "001"), ("B", "", "002")),
        (("date", "time", "zone"), ("2026-10-12", "09:05", "UTC+03"), ("2026-10-13", "17:45", "UTC+03")),
    ]
    samples.extend(Sample(f"table-{i:02}", "table", cells=cells,
                          language="ru" if i == 6 else "en", pixels=18)
                   for i, cells in enumerate(tables, 1))
    samples.extend([
        Sample("hard-01", "text", text="Смешанные буквы: AА BВ CС EЕ HН KК MМ OО PР TТ XХ.\nКириллица: съешь ещё этих мягких французских булок.\n", language="ru", pixels=12, challenging=True),
        Sample("hard-02", "code", text='def nested(flag):\n\tif flag:\n\t\tprint("Привет")\n\telse:\n\t\tprint("пока")\n\n', language="ru", pixels=12, challenging=True),
        Sample("hard-03", "text", text="Low contrast: 00123, 1.25, (alpha) [beta] {gamma}.\nSmall differences: I l 1 | O 0; rn m; , . : ;\n", pixels=16, foreground="#858585", background="#eeeeee", challenging=True),
        Sample("hard-04", "text", text="Привет, мир! 😀 🚀\nСтатус: готово ✅; ошибка ❌; ожидание ⏳.\n", language="ru", pixels=20, challenging=True),
        Sample("hard-05", "code", text="SELECT id, note\nFROM items\nWHERE id IN ('00123', '00456')\n  AND note <> ''\nORDER BY id;\n", pixels=12, foreground="#aaaaaa", background="#242424", challenging=True),
        Sample("hard-06", "table", cells=(("код", "заметка", "значение"), ("00123", 'слово "ёж"', "1,25"), ("00456", "строка 1\nстрока 2", "=SUM(A1:A3)")), language="ru", pixels=14,
               foreground="#909090", background="#f5f5f5", challenging=True),
    ])
    return samples


def _font(requested=None):
    from PySide6.QtGui import QFont, QFontDatabase, QFontInfo
    from ctrlcctrlv.fonts import monospace_font, prepare_fonts
    prepare_fonts()
    families = QFontDatabase.families()
    if requested:
        if requested not in families:
            raise ValueError("Requested monospace font is not installed")
        font = QFont(requested)
    else:
        font = monospace_font()
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(20)
    resolved = QFontInfo(font)
    if not resolved.family() or not resolved.fixedPitch():
        raise ValueError("An installed monospace font is required")
    return font


def _render(sample, base_font):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
    font = QFont(base_font)
    font.setPixelSize(sample.pixels)
    metrics = QFontMetrics(font)
    padding, spacing = 24, max(2, sample.pixels // 6)
    line_height = metrics.lineSpacing() + spacing
    if sample.kind == "table":
        widths = [max(metrics.horizontalAdvance(line) for row in sample.cells for line in row[c].split("\n")) + padding * 2
                  for c in range(len(sample.cells[0]))]
        heights = [max(len(value.split("\n")) for value in row) * line_height + padding
                   for row in sample.cells]
        width, height = sum(widths) + 1, sum(heights) + 1
    else:
        lines = [line.expandtabs(4) for line in sample.text.split("\n")]
        width = max(500, max(metrics.horizontalAdvance(line) for line in lines) + padding * 2)
        height = len(lines) * line_height + padding * 2
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(sample.background))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(QColor(sample.foreground))
    if sample.kind == "table":
        y = 0
        for row, cell_height in zip(sample.cells, heights):
            x = 0
            for value, cell_width in zip(row, widths):
                for index, line in enumerate(value.split("\n")):
                    painter.drawText(x + padding, y + padding // 2 + metrics.ascent() + index * line_height, line)
                painter.setPen(QColor("#c0c0c0"))
                painter.drawRect(x, y, cell_width, cell_height)
                painter.setPen(QColor(sample.foreground))
                x += cell_width
            y += cell_height
    else:
        for index, line in enumerate(lines):
            painter.drawText(padding, padding + metrics.ascent() + index * line_height, line)
    painter.end()
    return image


def generate(directory, *, overwrite=False, font_family=None):
    from PySide6.QtWidgets import QApplication
    # A standalone generator needs a GUI font database, but opens no window.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    directory = Path(directory)
    samples = _samples()
    targets = [directory / (sample.identifier + extension) for sample in samples for extension in (".png", ".txt")]
    targets.extend(directory / name for name in ("manifest.json", "generation.json", "README.md"))
    if not overwrite and any(path.exists() for path in targets):
        raise FileExistsError("Synthetic fixture targets already exist; explicit overwrite is required")
    font = _font(font_family)
    return _write_fixtures(directory, samples, font, overwrite)


def _write_fixtures(directory, samples, font, overwrite):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QFontInfo
    directory.mkdir(parents=True, exist_ok=True)
    manifest, images = [], []
    for sample in samples:
        image = _render(sample, font)
        encoded = QBuffer()
        encoded.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(encoded, "PNG"):
            raise ValueError("Synthetic image encoding failed")
        mode = "wb" if overwrite else "xb"
        with (directory / (sample.identifier + ".png")).open(mode) as stream:
            stream.write(bytes(encoded.data()))
        with (directory / (sample.identifier + ".txt")).open(mode) as stream:
            stream.write(sample.reference().encode("utf-8"))
        manifest.append(dict(id=sample.identifier, image=sample.identifier + ".png",
                             text=sample.identifier + ".txt", kind=sample.kind, language=sample.language,
                             font_size="small" if sample.pixels <= 14 else "medium", source="synthetic",
                             challenging=sample.challenging))
        images.append(dict(id=sample.identifier, width=image.width(), height=image.height(), font_pixels=sample.pixels,
                           dots_per_meter_x=image.dotsPerMeterX(), dots_per_meter_y=image.dotsPerMeterY(),
                           device_pixel_ratio=image.devicePixelRatio()))
    metadata = dict(synthetic_examples=len(samples), font_family=QFontInfo(font).family(),
                    fixed_pitch=QFontInfo(font).fixedPitch(), tab_width_spaces=4, images=images)
    for name, data in (("manifest.json", manifest), ("generation.json", metadata)):
        with (directory / name).open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    readme = (
        "# Synthetic OCR acceptance fixtures\n\n"
        "All 30 examples are generated synthetic material: 6 texts, 10 code snippets, "
        "8 cell tables and 6 challenging cases. They do not count as real-image acceptance.\n\n"
        "References are literal UTF-8. Tables use csv.writer with CRLF row terminators, "
        "including quoted commas/quotes and literal embedded line feeds. PNG tables render "
        "cell values in a grid, rather than their CSV encoding. Tabs in images occupy four "
        "monospace spaces; references preserve actual tab characters, making tab/space "
        "differences visible in the literal metrics. Final reference line endings cannot "
        "be inferred perfectly from a screenshot; both literal and one-final-newline "
        "metrics are reported.\n\n"
        "generation.json records the actual installed monospace font, pixel sizes and "
        "image dimensions. Font fallback for emoji is provided by the host Qt/font stack. "
        "Regeneration on another OS can change pixels; compare platforms only after "
        "copying the same generated fixtures.\n\n"
        "Generate with: .venv/Scripts/python.exe scripts/generate_acceptance_fixtures.py "
        "--output artifacts/ui-polish/benchmark-synthetic\n\n"
        "Existing generated files require explicit --overwrite. The generator calls no "
        "OCR/cloud service, accesses no keys and captures no user screen.\n"
    )
    with (directory / "README.md").open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
        stream.write(readme)
    return metadata


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/ui-polish/benchmark-synthetic"))
    parser.add_argument("--font-family")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    metadata = generate(args.output, overwrite=args.overwrite, font_family=args.font_family)
    print(json.dumps({"synthetic_examples": metadata["synthetic_examples"],
                      "fixed_pitch": int(metadata["fixed_pitch"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
