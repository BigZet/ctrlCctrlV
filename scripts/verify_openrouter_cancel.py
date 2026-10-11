"""Cancel one small OpenRouter request after its body has been sent.

Uses the production OCR transport and a synthetic fixture. Never writes to the
clipboard. Cancellation may still be billed by the remote provider.
"""
import argparse
import asyncio
import ctypes
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time

import httpx
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.app import diagnostic_print
from ctrlcctrlv.core import Cancelled, Operation, OperationError
from ctrlcctrlv.settings import Credentials
from scripts.verify_openrouter import ENDPOINT, MODEL, MeteredProvider


class CancelAfterSend(httpx.AsyncBaseTransport):
    def __init__(self, operation):
        self.operation = operation
        self.inner = httpx.AsyncHTTPTransport(retries=0, http2=False)
        self.body_sent = False
        self.sent_at = None
        self.cancel_at = None
        self.headers_received = False
        self.headers_received_before_cancel = False
        self.closed = False
        self.cancel_timer = None
        self.watchdog = None
        self.reason = None

    def cancel(self, reason):
        if self.cancel_at is None:
            self.cancel_at = time.monotonic()
            self.reason = reason
            self.headers_received_before_cancel = self.headers_received
            self.operation.cancel()

    async def trace(self, event, info):
        # Ignore info: it can contain headers, authorization and request data.
        if event == "http11.send_request_body.complete":
            self.body_sent = True
            self.sent_at = time.monotonic()
            self.cancel_timer = asyncio.get_running_loop().call_later(
                0.15, self.cancel, "after_body_sent")
        elif event == "http11.receive_response_headers.complete":
            self.headers_received = True

    async def handle_async_request(self, request):
        if str(request.url) != ENDPOINT or request.method != "POST":
            raise OperationError("Unexpected diagnostic destination")
        request.extensions["trace"] = self.trace
        self.watchdog = asyncio.get_running_loop().call_later(
            15, self.cancel, "startup_watchdog")
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        for timer in (self.cancel_timer, self.watchdog):
            if timer:
                timer.cancel()
        await self.inner.aclose()
        self.closed = True


def key_usage(client, key):
    response = client.get("https://openrouter.ai/api/v1/key",
                          headers={"Authorization": "Bearer " + key})
    response.raise_for_status()
    return Decimal(str(response.json()["data"]["usage"]))


def clipboard_snapshot(clipboard):
    mime = clipboard.mimeData()
    return {name: bytes(mime.data(name)) for name in mime.formats()} if mime else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/openrouter-cancel.json"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This native clipboard diagnostic requires Windows")
    os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication([])
    clipboard = app.clipboard()
    before = clipboard_snapshot(clipboard)
    sequence = ctypes.WinDLL("user32").GetClipboardSequenceNumber
    sequence.restype = ctypes.c_uint32
    previous_sequence = sequence()
    report = {"schema": 1, "model": MODEL, "generation_limit": 1,
              "max_output_tokens": 512, "clipboard_backend": app.platformName(),
              "fixture": "python-light-20", "checks": {},
              "generation_cost_usd": None}
    checks = report["checks"]
    operation = Operation()
    transport = CancelAfterSend(operation)
    try:
        key = Credentials().read()
        if not key:
            raise OperationError("OpenRouter key is not configured")
        png = Path("artifacts/fixtures/python-light-20.png").read_bytes()
        if len(png) > 100000:
            raise OperationError("Fixture exceeds the small paid-check scope")
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()
            model = next(m for m in response.json()["data"] if m["id"] == MODEL)
            if (not 0 <= Decimal(model["pricing"]["prompt"]) <= Decimal("0.0000005") or
                    not 0 <= Decimal(model["pricing"]["completion"]) <= Decimal("0.000001")):
                raise OperationError("Model no longer satisfies the inexpensive policy")
            usage_before = key_usage(client, key)
            provider = MeteredProvider(ENDPOINT, MODEL, key, max_tokens=512)
            started = time.monotonic()
            try:
                provider.recognize(png, operation, transport=transport)
            except Cancelled:
                finished = time.monotonic()
                checks["cancelled_exception"] = True
                report["request_elapsed"] = finished - started
                report["cancellation_latency"] = finished - transport.cancel_at if transport.cancel_at else None
            else:
                checks["cancelled_exception"] = False
            checks["body_sent_before_cancellation"] = transport.body_sent and transport.reason == "after_body_sent"
            checks["cancelled_within_one_second"] = bool(
                report.get("cancellation_latency") is not None and report["cancellation_latency"] < 1)
            checks["transport_closed"] = transport.closed
            checks["complete_response_not_received"] = not provider.records
            report["headers_received_before_cancel"] = transport.headers_received_before_cancel
            report["cancel_reason"] = transport.reason
            app.processEvents()
            checks["system_clipboard_unchanged"] = (sequence() == previous_sequence and
                                                      clipboard_snapshot(clipboard) == before)
            # The closed response has no generation ID / usage. Observe the
            # key's aggregate ledger without calling it an exact request cost.
            observations = []
            for index in range(3):
                if index:
                    time.sleep(2)
                observations.append(float(key_usage(client, key) - usage_before))
            report["observed_key_usage_delta_usd"] = observations
            report["billing_note"] = "aggregate_observations_are_not_final_generation_cost"
    except Exception as error:
        report["error_type"] = type(error).__name__
        checks["operation_succeeded"] = False
    finally:
        # If preflight failed, no request owns this transport's loop/resources.
        if not transport.closed:
            asyncio.run(transport.aclose())
        report["passed"] = bool(checks) and all(checks.values())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        diagnostic_print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
