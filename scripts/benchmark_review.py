"""Compare separate OCR checking against synthetic ground truth, without clipboard access."""
import argparse
import json
from pathlib import Path
import threading
import time

from ctrlcctrlv.catalog import MODELS
from ctrlcctrlv.app import diagnostic_print
from ctrlcctrlv.core import Operation
from ctrlcctrlv.ocr import OCRProvider
from ctrlcctrlv.review import ReviewProvider
from ctrlcctrlv.runtime import LocalRuntime
from ctrlcctrlv.settings import data_directory
from scripts.accuracy import compare_errors


def edit_distance(a, b):
    row = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        next_row = [i]
        for j, right in enumerate(b, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (left != right)))
        row = next_row
    return row[-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=data_directory())
    parser.add_argument("--ocr-model", default="lightonocr-3-0.8b", choices=tuple(MODELS))
    parser.add_argument("--review-model", default="qwen3-vl-4b", choices=tuple(k for k, m in MODELS.items() if m.mode == "json"))
    parser.add_argument("--fixtures", type=Path, default=Path("artifacts/fixtures"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/review-benchmark.json"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--step", type=int, default=4)
    args = parser.parse_args()
    fixtures = json.loads((args.fixtures / "manifest.json").read_text(encoding="utf-8"))[::args.step][:args.limit]
    runtime = LocalRuntime(args.data_dir, session="review")
    cancel = threading.Event()
    rows, inputs = [], []
    progress = lambda label, *_: diagnostic_print(label)
    try:
        runtime.start(args.ocr_model, cancel, progress)
        ocr = OCRProvider(runtime.endpoint + "/v1/chat/completions", args.ocr_model, runtime.key,
                          local=True, mode=MODELS[args.ocr_model].mode)
        for fixture in fixtures:
            png = (args.fixtures / fixture["image"]).read_bytes()
            expected = (args.fixtures / fixture["text"]).read_text(encoding="utf-8")
            op = Operation()
            try:
                original = ocr.recognize(png, op)
                inputs.append((fixture, png, expected, original, time.monotonic() - op.started))
            except Exception as error:
                rows.append({"id": fixture["id"], "ocr_error": type(error).__name__})
        runtime.stop()
        runtime.start(args.review_model, cancel, progress)
        checker = ReviewProvider(OCRProvider(runtime.endpoint + "/v1/chat/completions",
                                             args.review_model, runtime.key, local=True))
        for fixture, png, expected, original, ocr_elapsed in inputs:
            row = {"id": fixture["id"], "ocr_model": args.ocr_model, "review_model": args.review_model,
                   "ocr_elapsed": ocr_elapsed, "before_errors": edit_distance(expected, original),
                   "before_errors_ignoring_final_newline": edit_distance(expected.rstrip("\n"), original.rstrip("\n"))}
            try:
                result = checker.inspect(png, original)
                row.update(review_elapsed=result.elapsed, changed=result.changed,
                           after_errors=edit_distance(expected, result.proposed),
                           after_errors_ignoring_final_newline=edit_distance(expected.rstrip("\n"), result.proposed.rstrip("\n")))
                row["positional_errors"] = compare_errors(expected, original, result.proposed)
                row["positional_errors_ignoring_final_newline"] = compare_errors(
                    expected.rstrip("\n"), original.rstrip("\n"), result.proposed.rstrip("\n"))
            except Exception as error:
                row["review_error"] = type(error).__name__
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        runtime.stop()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
