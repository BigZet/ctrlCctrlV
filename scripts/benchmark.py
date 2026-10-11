"""Measure full OCR-to-clipboard latency. A timeout is recorded as a failure."""
import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import threading
import time

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QBuffer, QIODevice, QMimeData
from PySide6.QtGui import QImage

from ctrlcctrlv.catalog import MODELS
from ctrlcctrlv.core import Operation
from ctrlcctrlv.ocr import OCRProvider
from ctrlcctrlv.runtime import LocalRuntime
from ctrlcctrlv.settings import data_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--model", default="lightonocr-3-0.8b", choices=tuple(MODELS))
    parser.add_argument("--fixtures", type=Path, default=Path("artifacts/fixtures"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark.json"))
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    app = QApplication([])
    clipboard = app.clipboard()
    # Preserve all original clipboard formats, including images and rich text.
    previous = QMimeData()
    current = clipboard.mimeData()
    if current:
        for format_name in current.formats():
            previous.setData(format_name, current.data(format_name))
    runtime = LocalRuntime(args.data_dir)
    results = []
    try:
        preparing = time.monotonic()
        runtime.start(args.model, threading.Event(), lambda label, *rest: print(label, flush=True))
        preparation_elapsed = time.monotonic() - preparing
        model = MODELS[args.model]
        provider = OCRProvider(runtime.endpoint + "/v1/chat/completions", args.model, runtime.key, 4096, True, model.mode)
        fixtures = json.loads((args.fixtures / "manifest.json").read_text())[:args.limit]
        for fixture in fixtures:
            image = QImage(str(args.fixtures / fixture["image"]))
            expected = (args.fixtures / fixture["text"]).read_text()
            row = {"id": fixture["id"], "model": args.model, "passed_4s": False,
                   "clipboard_backend": app.platformName(), "preparation_elapsed": preparation_elapsed,
                   "complexity": fixture.get("complexity", "short"), "width": image.width(),
                   "height": image.height(), "expected_characters": len(expected)}
            operation = Operation()
            try:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image.save(buffer, "PNG"):
                    raise ValueError("Image encoding failed")
                png = bytes(buffer.data())
                row["encoding_elapsed"] = time.monotonic() - operation.started
                request_started = time.monotonic()
                text = provider.recognize(png, operation)
                row["request_elapsed"] = time.monotonic() - request_started
                elapsed = operation.publish(text, clipboard.setText)
                row.update(elapsed=elapsed, passed_4s=elapsed <= 4, exact=text == expected,
                    exact_ignoring_final_newline=text.rstrip("\n") == expected.rstrip("\n"),
                    character_similarity=SequenceMatcher(None, expected, text, autojunk=False).ratio(),
                    output_characters=len(text), expected_characters=len(expected))
            except Exception as error:
                row.update(elapsed=time.monotonic()-operation.started, error=type(error).__name__)
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        runtime.stop()
        clipboard.setMimeData(previous)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2))
        # Offscreen has no system clipboard; release its owned MIME object before
        # QApplication teardown (Qt can otherwise crash at process exit).
        if app.platformName() == "offscreen":
            clipboard.clear()


if __name__ == "__main__":
    main()
