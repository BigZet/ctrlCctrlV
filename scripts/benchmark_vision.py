"""Experimental macOS Vision comparison; never used by the application's OCR.

Requires optional pyobjc-framework-Vision. Layout estimates use only recognized
boxes and strings, never fixture text, font metrics or known line boundaries.
"""
import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import platform
from statistics import median
import time


def reconstruct(blocks):
    if not blocks:
        return ""
    blocks = sorted(blocks, key=lambda row: (row["y"], row["x"]))
    heights = [row["height"] for row in blocks]
    typical_height = median(heights)
    lines = []
    for block in blocks:
        if lines and abs(block["y"] - median(row["y"] for row in lines[-1])) < typical_height * 0.5:
            lines[-1].append(block)
        else:
            lines.append([block])
    centers = [median(row["y"] for row in line) for line in lines]
    spacing = median(b - a for a, b in zip(centers, centers[1:])) if len(centers) > 1 else typical_height * 1.4
    cells = [row["width"] / len(row["text"]) for row in blocks if len(row["text"]) >= 8]
    cell = median(cells) if cells else typical_height * 0.6
    origin = min(row["x"] for row in blocks)
    output = []
    for index, line in enumerate(lines):
        if index:
            output.extend([""] * max(0, round((centers[index] - centers[index - 1]) / spacing) - 1))
        assembled = ""
        for block in sorted(line, key=lambda row: row["x"]):
            column = max(0, round((block["x"] - origin) / max(cell, 0.01)))
            assembled += " " * max(0, column - len(assembled)) + block["text"]
        output.append(assembled)
    return "\n".join(output)


def recognize(path, level, scale=1):
    import objc
    import Vision
    from Foundation import NSData, NSURL
    with objc.autorelease_pool():
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(level)
        request.setUsesLanguageCorrection_(False)
        languages, error = request.supportedRecognitionLanguagesAndReturnError_(None)
        if error:
            raise RuntimeError(str(error))
        selected = [value for value in ("en-US", "ru-RU") if value in languages]
        request.setRecognitionLanguages_(selected)
        if scale == 1:
            handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path.resolve())), {})
        else:
            from PySide6.QtCore import QBuffer, QIODevice, Qt
            from PySide6.QtGui import QImage
            image = QImage(str(path)).scaled(QImage(str(path)).size() * scale,
                                            Qt.AspectRatioMode.IgnoreAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "PNG"):
                raise RuntimeError("Image encoding failed")
            payload = bytes(buffer.data())
            data = NSData.dataWithBytes_length_(payload, len(payload))
            handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            raise RuntimeError(str(error))
        blocks = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            box = observation.boundingBox()
            blocks.append({"text": str(candidates[0].string()), "x": box.origin.x,
                           "y": 1 - box.origin.y - box.size.height / 2,
                           "width": box.size.width, "height": box.size.height})
        return reconstruct(blocks), blocks, list(languages), int(request.revision())


def main():
    import Vision
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=Path("artifacts/fixtures-expanded"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/vision.json"))
    parser.add_argument("--level", choices=("accurate", "fast"), default="accurate")
    parser.add_argument("--scale", type=int, choices=(1, 2, 3, 4), default=1)
    args = parser.parse_args()
    level = Vision.VNRequestTextRecognitionLevelAccurate if args.level == "accurate" else Vision.VNRequestTextRecognitionLevelFast
    fixtures = json.loads((args.fixtures / "manifest.json").read_text())
    results = []
    for fixture in fixtures:
        expected = (args.fixtures / fixture["text"]).read_text()
        started = time.monotonic()
        actual, blocks, languages, revision = recognize(args.fixtures / fixture["image"], level, args.scale)
        elapsed = time.monotonic() - started
        row = {"id": fixture["id"], "complexity": fixture.get("complexity", "short"),
               "elapsed": elapsed, "passed_4s": elapsed <= 4,
               "exact_ignoring_final_newline": actual.rstrip("\n") == expected.rstrip("\n"),
               "similarity": SequenceMatcher(None, expected, actual, autojunk=False).ratio(),
               "expected": expected, "actual": actual, "blocks": blocks}
        results.append(row)
        print(f'{row["id"]}: {elapsed:.3f}s, exact={row["exact_ignoring_final_newline"]}', flush=True)
    report = {"engine": "macOS Vision", "system": platform.mac_ver()[0], "level": args.level,
              "revision": revision, "supported_languages": languages, "language_correction": False, "scale": args.scale,
              "timing_scope": "image file decode + recognition + geometric reconstruction; no clipboard",
              "layout": "estimated from recognized boxes only; no fixture geometry",
              "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
