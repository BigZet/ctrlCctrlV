"""Verify two installed models, original preservation and OCR restoration.

Uses a synthetic fixture; no clipboard, capture, cloud or user text.
"""
import argparse
import json
from pathlib import Path
import threading
import time

from ctrlcctrlv.app import diagnostic_print
from ctrlcctrlv.catalog import MODELS
from ctrlcctrlv.core import Operation, OperationError
from ctrlcctrlv.hardware import InsufficientMemory
from ctrlcctrlv.ocr import OCRProvider
from ctrlcctrlv.review import ReviewProvider, local_review_session
from ctrlcctrlv.runtime import LocalRuntime


def provider(runtime, identifier):
    return OCRProvider(runtime.endpoint + "/v1/chat/completions", identifier, runtime.key,
                       local=True, mode=MODELS[identifier].mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, default=Path("artifacts/fixtures"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/review-lifecycle.json"))
    parser.add_argument("--acceleration", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument("--expect-sequential", action="store_true")
    parser.add_argument("--expect-memory-rejection", action="store_true")
    args = parser.parse_args()
    primary = LocalRuntime(args.data_dir, acceleration=args.acceleration)
    reviewer = LocalRuntime(args.data_dir, session="review", acceleration=args.acceleration)
    cancel = threading.Event()
    checks = {}
    report = {"schema": 1, "checks": checks, "acceleration": args.acceleration}
    errors = []
    started = time.monotonic()
    progress = lambda label, *_: diagnostic_print(label)
    try:
        # These are the shipped defaults. This is an integration scenario, not
        # a comparison of new model candidates or a claim of recognition quality.
        fixture = json.loads((args.fixtures / "manifest.json").read_text(encoding="utf-8"))[0]
        png = (args.fixtures / fixture["image"]).read_bytes()
        report.update(fixture=fixture["id"], ocr_model="lightonocr-3-0.8b", review_model="qwen3-vl-4b")
        if not reviewer.installed(report["review_model"]):
            raise OperationError("Install the reviewer model before running this check")
        primary.start(report["ocr_model"], cancel, progress)
        report["primary_preflight"] = primary.hardware
        original = provider(primary, report["ocr_model"]).recognize(png, Operation())
        report["original_characters"] = len(original)
        try:
            with local_review_session(primary, reviewer, report["review_model"], args.acceleration,
                                      cancel, progress, errors) as active:
                checks["reviewer_ready"] = active is reviewer and reviewer.ready
                report["primary_unloaded_during_review"] = not primary.ready
                if args.expect_sequential:
                    checks["sequential_load"] = not primary.ready
                result = ReviewProvider(provider(active, report["review_model"])).inspect(png, original).prepare()
                checks["original_preserved"] = result.original == original
                checks["nonempty_proposal"] = bool(result.proposed.strip())
                report.update(proposed_characters=len(result.proposed), review_elapsed=result.elapsed)
            if args.expect_memory_rejection:
                checks["expected_memory_rejection"] = False
        except InsufficientMemory:
            if not args.expect_memory_rejection:
                raise
            checks["expected_memory_rejection"] = True
        report["reviewer_preflight"] = reviewer.hardware
        checks["reviewer_unloaded"] = not reviewer.ready and reviewer.process is None
        checks["primary_ready_after_review"] = primary.ready and primary.model_id == report["ocr_model"] and not errors
        if primary.ready:
            restored = provider(primary, report["ocr_model"]).recognize(png, Operation())
            checks["restored_ocr_responds_literally"] = restored == original
    except Exception as error:
        report["error_type"] = type(error).__name__
        checks["operation_succeeded"] = False
    finally:
        for runtime in (reviewer, primary):
            try:
                runtime.stop()
            except Exception:
                checks["cleanup_succeeded"] = False
        checks["owned_engines_stopped"] = primary.process is None and reviewer.process is None
        report["elapsed"] = time.monotonic() - started
        report["passed"] = bool(checks) and all(checks.values())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        diagnostic_print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
