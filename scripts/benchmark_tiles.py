"""Experimental upper bound: split synthetic fixtures at KNOWN line boundaries.

This is not a production OCR strategy: real images do not provide font/line
metadata, and blank lines and indentation still need reliable reconstruction.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
import json
from pathlib import Path
import threading
import time

from PySide6.QtCore import QBuffer, QIODevice, QMimeData
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.catalog import MODELS
from ctrlcctrlv.core import Operation
from ctrlcctrlv.ocr import OCRProvider
from ctrlcctrlv.runtime import LocalRuntime
from ctrlcctrlv.settings import data_directory


def encode(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise ValueError("Cannot encode tile")
    return bytes(buffer.data())


def known_tiles(image, fixture, count):
    rows = fixture["lines"]
    count = min(count, rows)
    cuts = [0]
    for index in range(1, count):
        # generate_fixtures draws baseline y=30+row*(font+8). This is a gap
        # between lines, derived from generator metadata, not detected by OCR.
        cuts.append((rows * index // count) * (fixture["font_pixels"] + 8) + 12)
    cuts.append(image.height())
    return [image.copy(0, start, image.width(), end - start) for start, end in zip(cuts, cuts[1:])]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--fixtures", type=Path, default=Path("artifacts/fixtures-expanded"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark-tiles.json"))
    parser.add_argument("--model", choices=tuple(MODELS), default="lightonocr-3-0.8b")
    parser.add_argument("--slots", type=int, default=4)
    args = parser.parse_args()
    app = QApplication([])
    clipboard = app.clipboard()
    previous = QMimeData()
    current = clipboard.mimeData()
    if current:
        for format_name in current.formats():
            previous.setData(format_name, current.data(format_name))
    runtime = LocalRuntime(args.data_dir, session="review", slots=args.slots)
    results = []
    try:
        runtime.start(args.model, threading.Event(), lambda label, *_: print(label, flush=True))
        provider = OCRProvider(runtime.endpoint + "/v1/chat/completions", args.model, runtime.key,
                               local=True, mode=MODELS[args.model].mode)
        white = QImage(96, 96, QImage.Format.Format_RGB32)
        white.fill(0xffffffff)
        png = encode(white)
        with ThreadPoolExecutor(max_workers=args.slots) as pool:
            list(pool.map(lambda _: provider.recognize(png, Operation(budget=120)), range(args.slots)))
        fixtures = json.loads((args.fixtures / "manifest.json").read_text())
        for fixture in (f for f in fixtures if f["complexity"] != "short"):
            image = QImage(str(args.fixtures / fixture["image"]))
            expected = (args.fixtures / fixture["text"]).read_text()
            row = {"id": fixture["id"], "model": args.model, "slots": args.slots,
                   "oracle_line_boundaries": True, "clipboard_backend": app.platformName(),
                   "complexity": fixture["complexity"], "passed_4s": False}
            operation = Operation()
            try:
                tiles = known_tiles(image, fixture, args.slots)
                def recognize(tile):
                    operation.check()
                    return provider.recognize(encode(tile), operation)
                with ThreadPoolExecutor(max_workers=args.slots) as pool:
                    pieces = list(pool.map(recognize, tiles))
                # Known boundaries each represent an actual line break. Preserve
                # returned blank lines; do not silently trim or repair content.
                text = "".join(piece if piece.endswith("\n") else piece + "\n" for piece in pieces[:-1]) + pieces[-1]
                elapsed = operation.publish(text, clipboard.setText)
                row.update(elapsed=elapsed, passed_4s=elapsed <= 4, tiles=len(tiles),
                    exact=text == expected, exact_ignoring_final_newline=text.rstrip("\n") == expected.rstrip("\n"),
                    character_similarity=SequenceMatcher(None, expected, text, autojunk=False).ratio())
            except Exception as error:
                operation.cancel()
                row.update(elapsed=time.monotonic() - operation.started, error=type(error).__name__)
            results.append(row)
            print(json.dumps(row), flush=True)
    finally:
        runtime.stop()
        clipboard.setMimeData(previous)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2))
        if app.platformName() == "offscreen":
            clipboard.clear()


if __name__ == "__main__":
    main()
