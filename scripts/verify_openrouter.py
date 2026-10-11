"""Small paid OpenRouter check using a synthetic fixture and OS credentials.

Two generations at most, no retries or model fallback. Reports contain only
allowlisted metrics; the original system clipboard is restored if still owned.
"""
import argparse
import ctypes
from dataclasses import dataclass, field
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time

import httpx
from PySide6.QtCore import QBuffer, QIODevice, QMimeData
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.app import diagnostic_print
from ctrlcctrlv.core import Cancelled, Operation, OperationError
from ctrlcctrlv.ocr import OCRProvider
from ctrlcctrlv.review import ReviewProvider
from ctrlcctrlv.settings import Credentials
from scripts.benchmark_review import edit_distance

MODEL = "qwen/qwen3-vl-8b-instruct"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


@dataclass(frozen=True)
class MeteredProvider(OCRProvider):
    records: list = field(default_factory=list, compare=False, repr=False)

    async def _request(self, payload, operation, transport):
        # Routing changes price, not the app's prompt, parsing or transport.
        payload = dict(payload, provider={"sort": "price", "max_price": {
            "prompt": 0.5, "completion": 1.0}})
        body = await super()._request(payload, operation, transport)
        response = json.loads(body)
        usage = response.get("usage", {})
        self.records.append({
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cost_usd": usage.get("cost"),
            "finish_reason": response.get("choices", [{}])[0].get("finish_reason"),
        })
        return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("artifacts/fixtures"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/openrouter-check.json"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This system-clipboard diagnostic currently requires Windows")
    os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication([])
    clipboard = app.clipboard()
    previous = QMimeData()
    current = clipboard.mimeData()
    if current:
        for name in current.formats():
            previous.setData(name, current.data(name))
    sequence = None
    sequence_number = None
    if sys.platform == "win32":
        sequence_number = ctypes.WinDLL("user32").GetClipboardSequenceNumber
        sequence_number.restype = ctypes.c_uint32
    report = {"schema": 1, "model": MODEL, "generation_limit": 2,
              "budget_usd": 0.05, "clipboard_backend": app.platformName(),
              "checks": {}, "requests": []}
    checks = report["checks"]
    started = time.monotonic()
    try:
        key = Credentials().read()
        if not key:
            raise OperationError("OpenRouter key is not configured")
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()
            model = next(m for m in response.json()["data"] if m["id"] == MODEL)
            price = model["pricing"]
            if ("image" not in model["architecture"]["input_modalities"] or
                    not 0 <= Decimal(price["prompt"]) <= Decimal("0.0000005") or
                    not 0 <= Decimal(price["completion"]) <= Decimal("0.000001")):
                raise OperationError("Model no longer satisfies the inexpensive vision policy")
            report["pricing_usd_per_million"] = {
                "input": float(Decimal(price["prompt"]) * 1000000),
                "output": float(Decimal(price["completion"]) * 1000000)}
        manifest = json.loads((args.fixtures / "manifest.json").read_text(encoding="utf-8"))
        fixture = next(item for item in manifest if item["id"] == "python-light-20")
        image = QImage(str(args.fixtures / fixture["image"]))
        expected = (args.fixtures / fixture["text"]).read_text(encoding="utf-8")
        if image.isNull() or image.width() > 1024 or image.height() > 512 or len(expected) > 512:
            raise OperationError("Fixture exceeds the small paid-check scope")
        report["fixture"] = fixture["id"]
        report["image_size"] = [image.width(), image.height()]
        provider = MeteredProvider(ENDPOINT, MODEL, key, max_tokens=512)
        report["requests"] = provider.records
        operation = Operation()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise OperationError("PNG encoding failed")
        png = bytes(buffer.data())
        original = provider.recognize(png, operation)
        elapsed = operation.publish(original, clipboard.setText)
        sequence = sequence_number() if sequence_number else None
        checks["ocr_published_to_system_clipboard"] = clipboard.text() == original
        report["ocr"] = {"elapsed": elapsed, "passed_4s": elapsed <= 4,
                         "expected_characters": len(expected), "output_characters": len(original),
                         "exact": original == expected,
                         "exact_ignoring_final_newline": original.rstrip("\n") == expected.rstrip("\n"),
                         "character_errors": edit_distance(expected, original)}
        cost = provider.records[-1]["cost_usd"]
        if cost is None or Decimal(str(cost)) >= Decimal("0.025"):
            raise OperationError("Cannot safely authorize the second paid request")
        result = ReviewProvider(provider).inspect(png, original).prepare()
        checks["review_original_preserved"] = result.original == original
        checks["review_proposal_nonempty"] = bool(result.proposed.strip())
        checks["review_did_not_publish"] = clipboard.text() == original
        checks["review_selection_works"] = result.select([]).proposed == original
        report["review"] = {"elapsed": result.elapsed, "changed": result.changed,
                            "output_characters": len(result.proposed),
                            "character_errors": edit_distance(expected, result.proposed),
                            "exact": result.proposed == expected,
                            "exact_ignoring_final_newline": result.proposed.rstrip("\n") == expected.rstrip("\n")}
        cancelled = Operation()
        cancelled.cancel()
        requests_before = len(provider.records)
        try:
            provider.recognize(png, cancelled)
        except Cancelled:
            checks["cancel_before_request_preserves_clipboard"] = clipboard.text() == original
            checks["cancel_before_request_avoids_generation"] = len(provider.records) == requests_before
        else:
            checks["cancel_before_request_avoids_generation"] = False
        # Deliberately invalid authorization cannot charge the user's key.
        invalid = MeteredProvider(ENDPOINT, MODEL, "invalid-diagnostic-key", max_tokens=512)
        try:
            invalid.recognize(png, Operation())
        except OperationError as error:
            checks["invalid_auth_is_rejected"] = "HTTP 401" in str(error)
            checks["invalid_auth_preserves_clipboard"] = clipboard.text() == original
        else:
            checks["invalid_auth_is_rejected"] = False
        total = sum(Decimal(str(row["cost_usd"])) for row in provider.records)
        report["total_cost_usd"] = float(total)
        checks["within_budget"] = total <= Decimal("0.05")
    except Exception as error:
        report["error_type"] = type(error).__name__
        checks["operation_succeeded"] = False
    finally:
        costs = [row["cost_usd"] for row in report["requests"]]
        if costs and all(cost is not None for cost in costs):
            report["total_cost_usd"] = float(sum(Decimal(str(cost)) for cost in costs))
        else:
            report["total_cost_usd"] = None
        if sequence is not None and sequence_number() == sequence:
            clipboard.setMimeData(previous)
            report["clipboard_restored"] = True
        else:
            report["clipboard_restored"] = False
            report["clipboard_restore_skipped"] = "not_modified_or_ownership_changed"
        report["elapsed"] = time.monotonic() - started
        report["passed"] = bool(checks) and all(checks.values())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        diagnostic_print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
