"""Offline-first, literal OCR acceptance measurement.

No clipboard/history/review actions. References stay in memory and the report
contains only sample IDs and numbers. Cloud requests require explicit execution,
a positive Decimal budget and a complete current price/context/vision contract.
"""
import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import csv
import io
import inspect
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import threading
import time
import uuid

from ctrlcctrlv.core import Cancelled, Operation


class ManifestError(ValueError):
    """Errors intentionally carry no filenames, sample content or provider bodies."""


class BudgetError(ValueError):
    pass


@dataclass(frozen=True)
class Example:
    identifier: str
    image: Path
    reference: Path
    expected: str
    kind: str
    language: str
    font_size: str
    source: str = "synthetic"
    challenging: bool = False


def _relative_file(root, value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ManifestError("Invalid relative fixture path")
    parts = PurePosixPath(value)
    if parts.is_absolute() or any(p in {"", ".", ".."} or p.rstrip(" .") != p
                                  for p in value.split("/")):
        raise ManifestError("Invalid relative fixture path")
    target = root.joinpath(*parts.parts).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ManifestError("Fixture must exist inside the manifest directory")
    return target


def load_manifest(path):
    """Read a JSON list and UTF-8 references without universal-newline conversion.

Required fields: id, image, text, kind, language, font_size. Optional source is
real|synthetic (defaults synthetic); challenging marks the separate hard group.
Only explicit source=real contributes to the external 30-example checklist.
"""
    try:
        path = Path(path).resolve()
        if path.stat().st_size > 1_000_000:
            raise ManifestError("Manifest exceeds the size limit")
        rows = json.loads(path.read_bytes().decode("utf-8"))
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            raise ManifestError("Manifest must contain 1 to 1000 examples")
        result, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise ManifestError("Example must be an object")
            if row.keys() - {"id", "image", "text", "kind", "language", "font_size", "source", "challenging"}:
                raise ManifestError("Unrecognized manifest fields are not allowed")
            identifier = row.get("id")
            if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", identifier)
                    or identifier in seen):
                raise ManifestError("Example ID must be unique and use safe ASCII characters")
            seen.add(identifier)
            kind, language, font_size = row.get("kind"), row.get("language"), row.get("font_size")
            if kind not in {"text", "code", "table"}:
                raise ManifestError("Unknown example kind")
            if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language):
                raise ManifestError("Invalid language tag")
            if font_size not in {"small", "medium", "large", "mixed"}:
                raise ManifestError("Invalid font-size label")
            source, challenging = row.get("source", "synthetic"), row.get("challenging", False)
            if source not in {"real", "synthetic"} or not isinstance(challenging, bool):
                raise ManifestError("Invalid example group")
            image = _relative_file(path.parent, row.get("image"))
            reference = _relative_file(path.parent, row.get("text"))
            if image.stat().st_size > 32_000_000 or reference.stat().st_size > 1_000_000:
                raise ManifestError("Fixture exceeds the size limit")
            from PySide6.QtGui import QImage
            decoded = QImage.fromData(image.read_bytes())
            if decoded.isNull() or decoded.width() * decoded.height() > 40_000_000:
                raise ManifestError("Fixture image is invalid or too large")
            expected = reference.read_bytes().decode("utf-8")
            # Exact Levenshtein computation is intentionally bounded, not replaced
            # silently with a similarity heuristic for enormous references.
            if len(expected) > 10_000:
                raise ManifestError("Reference exceeds the character-metric limit")
            if kind == "table":
                _csv_cells(expected)
            result.append(Example(identifier, image, reference, expected, kind,
                                  language, font_size, source, challenging))
        return result
    except ManifestError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, csv.Error):
        raise ManifestError("Manifest or fixture validation failed") from None


def _one_final_newline(text):
    if text.endswith("\r\n"):
        return text[:-2]
    return text[:-1] if text.endswith(("\n", "\r")) else text


def _character_errors(expected, actual):
    if expected == actual:
        return 0
    if (len(expected) + 1) * (len(actual) + 1) > 10_000_000:
        raise ValueError("Character metric exceeds the computation limit")
    previous = list(range(len(actual) + 1))
    for i, left in enumerate(expected, 1):
        row = [i]
        for j, right in enumerate(actual, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = row
    return previous[-1]


def _csv_cells(text):
    rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("Invalid rectangular CSV")
    return {(r, c): value for r, row in enumerate(rows) for c, value in enumerate(row)}


def literal_metrics(expected, actual, kind):
    """Unicode codepoint edit accuracy; tabs, spaces, CR/LF and blank lines stay literal.

    Accuracy is max(0, 1 - Levenshtein/max(reference_length,1)). Cell accuracy is
    the proportion of equal values at corresponding (row,column) coordinates,
    with extra/missing cells included in the denominator; no numeric conversion.
    """
    errors = _character_errors(expected, actual)
    left, right = expected.splitlines(), actual.splitlines()
    indent = lambda lines: [re.match(r"[ \t]*", line)[0] for line in lines]
    blanks = lambda lines: [(i, line) for i, line in enumerate(lines) if not line.strip(" \t")]
    metrics = dict(exact=int(expected == actual),
                   exact_ignoring_one_final_newline=int(_one_final_newline(expected) == _one_final_newline(actual)),
                   character_errors=errors, character_accuracy=max(0, 1 - errors / max(len(expected), 1)),
                   expected_characters=len(expected), output_characters=len(actual),
                   indentation_exact=int(indent(left) == indent(right)),
                   blank_lines_exact=int(blanks(left) == blanks(right)))
    if kind == "table":
        reference = _csv_cells(expected)
        try:
            observed = _csv_cells(actual)
        except (csv.Error, ValueError):
            metrics.update(csv_valid=0, csv_cells_exact=0, csv_cell_accuracy=0,
                           csv_cells_compared=len(reference))
        else:
            coordinates = reference.keys() | observed.keys()
            equal = sum(p in reference and p in observed and reference[p] == observed[p] for p in coordinates)
            metrics.update(csv_valid=1, csv_cells_exact=int(reference == observed),
                           csv_cell_accuracy=equal / len(coordinates), csv_cells_compared=len(coordinates))
    return metrics


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise BudgetError("Invalid budget or price")
        return result
    except (InvalidOperation, ValueError):
        raise BudgetError("Invalid budget or price") from None


@dataclass(frozen=True)
class PriceQuote:
    prompt: Decimal
    completion: Decimal
    image: Decimal
    request: Decimal

    def upper_bound(self, input_tokens, max_output_tokens, images):
        if any(not isinstance(n, int) or isinstance(n, bool) or n < 0
               for n in (input_tokens, max_output_tokens, images)):
            raise BudgetError("Unknown request size bound")
        return (_money(self.prompt) * input_tokens + _money(self.completion) * max_output_tokens
                + _money(self.image) * images + _money(self.request))


@dataclass(frozen=True)
class CloudPlan:
    quote: PriceQuote
    input_tokens_upper: int
    max_output_tokens: int

    @property
    def upper_bound(self):
        return self.quote.upper_bound(self.input_tokens_upper, self.max_output_tokens, 1)


def _price_cap(value):
    """Round a JSON numeric routing cap down, never above its Decimal reservation."""
    result = float(value)
    if not math.isfinite(result):
        raise BudgetError("Price cannot be represented as a routing cap")
    if Decimal(str(result)) > value:
        result = math.nextafter(result, 0)
    return result


def validate_cloud_metadata(payload, identifier, max_output_tokens):
    """Use the entire declared model context as a conservative image+text bound.

    Unknown paid dimensions or missing token prices refuse execution. Optional
    image/request fees default to zero, enforced by the adapter's routing caps.
    Cache-read discounts never reduce the full-price input reservation.
    """
    try:
        model = next(m for m in payload["data"] if m["id"] == identifier)
        if "image" not in model["architecture"]["input_modalities"]:
            raise BudgetError("Model does not declare image input")
        context = model["context_length"]
        if not isinstance(context, int) or isinstance(context, bool) or not 0 < max_output_tokens <= context:
            raise BudgetError("Unknown model context bound")
        prices = model["pricing"]
        required = {"prompt", "completion", "image", "request"}
        if any(_money(value) != 0 for key, value in prices.items()
               if key not in required | {"input_cache_read"}):
            raise BudgetError("Unsupported additional billing dimension")
        # OpenRouter documents cache reads as an alternative input-token rate
        # and omitted SKUs as unbilled. Keep explicit image/request caps even
        # when omitted, so a routed provider cannot introduce those fees.
        # https://openrouter.ai/docs/guides/overview/models
        # https://openrouter.ai/docs/guides/routing/provider-selection
        quote = PriceQuote(_money(prices["prompt"]), _money(prices["completion"]),
                           _money(prices.get("image", "0")), _money(prices.get("request", "0")))
        if "input_cache_read" in prices and _money(prices["input_cache_read"]) > quote.prompt:
            raise BudgetError("Cache-read price exceeds the reserved input rate")
        return CloudPlan(quote, context, max_output_tokens)
    except BudgetError:
        raise
    except (KeyError, StopIteration, TypeError, ValueError):
        raise BudgetError("Incomplete current model metadata") from None


class CloudProvider:
    """Stable OCRProvider.recognize API with transport-only caps/usage capture."""
    def __init__(self, model, key, max_tokens, plan, *, transport_factory=None):
        self.model, self.key, self.max_tokens, self.plan = model, key, max_tokens, plan
        self.transport_factory = transport_factory
        self.last_usage = None

    def recognize(self, png, operation, *, prompt=None):
        return self.recognize_result(png, operation, prompt=prompt).text

    def recognize_result(self, png, operation, *, prompt=None):
        import httpx
        from ctrlcctrlv.ocr import OCRProvider
        owner = self
        self.last_usage = None

        class CappedTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.inner = owner.transport_factory() if owner.transport_factory else httpx.AsyncHTTPTransport(retries=0)

            async def handle_async_request(self, request):
                body = json.loads(request.content)
                quote = owner.plan.quote
                body["provider"] = {"allow_fallbacks": False, "require_parameters": True,
                    "max_price": {"prompt": _price_cap(quote.prompt * 1_000_000),
                                  "completion": _price_cap(quote.completion * 1_000_000),
                                  "image": _price_cap(quote.image), "request": _price_cap(quote.request)}}
                request = httpx.Request(request.method, request.url,
                                        headers={k: v for k, v in request.headers.items() if k != "content-length"},
                                        json=body)
                response = await self.inner.handle_async_request(request)
                # Provider bodies are bounded just like OCRProvider._request.
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    operation.check()
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise ValueError("Response exceeds limit")
                await response.aclose()
                if response.status_code == 200:
                    try:
                        usage = json.loads(data).get("usage")
                        if isinstance(usage, dict):
                            owner.last_usage = {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "cost") if k in usage}
                    except (ValueError, TypeError):
                        pass
                # aiter_bytes() already decoded and unframed the body; retaining
                # wire headers would decode it again or describe the wrong length.
                headers = [(k, v) for k, v in response.headers.multi_items()
                           if k not in ("content-encoding", "content-length", "transfer-encoding")]
                return httpx.Response(response.status_code, headers=headers, content=bytes(data))

            async def aclose(self):
                await self.inner.aclose()

        provider = OCRProvider("https://openrouter.ai/api/v1/chat/completions", self.model,
                               self.key, self.max_tokens)
        return provider.recognize_result(png, operation, CappedTransport(), prompt=prompt)


def _app_result(provider, png, operation, capture_format):
    """Follow the application's prompt, detailed result and table formatter path.

    Inspect compatibility before the call, so an old fake/adapter accepting only
    (png, operation) never needs a TypeError retry of a potentially paid request.
    Preserve OCRProvider subclasses that override only the stable recognize API,
    as the application does.
    """
    from ctrlcctrlv.ocr import OCRProvider, OCRResult
    from ctrlcctrlv.table_ocr import recognition_prompt, table_result
    detailed = getattr(provider, "recognize_result", None)
    legacy_override = (isinstance(provider, OCRProvider)
                       and type(provider).recognize is not OCRProvider.recognize
                       and type(provider).recognize_result is OCRProvider.recognize_result)
    method = detailed if callable(detailed) and not legacy_override else provider.recognize
    parameters = inspect.signature(method).parameters.values()
    accepts_prompt = any(p.kind == inspect.Parameter.VAR_KEYWORD or
                         p.name == "prompt" and p.kind != inspect.Parameter.POSITIONAL_ONLY
                         for p in parameters)
    kwargs = {"prompt": recognition_prompt(capture_format)} if accepts_prompt else {}
    result = method(png, operation, **kwargs)
    if method is not detailed:
        result = OCRResult(result)
    operation.check()
    if not isinstance(result, OCRResult) or not isinstance(result.text, str):
        raise ValueError("Provider returned a non-text result")
    return OCRResult(table_result(result.text, capture_format), result.raw_text)


def run_benchmark(examples, *, provider=None, execute=False, cloud=False, budget=Decimal("0"),
                  estimate=None, repeats=1, clock=time.monotonic, startup_seconds=0,
                  capture_format="auto"):
    """Reserve before sending; a failed/cancelled sample never gets an automatic retry.

    Reservations remain spent even if usage is absent or a call fails/cancels.
    Explicit repeats are successful sample repetitions, not error recovery.
    """
    budget = _money(budget)
    if capture_format not in ("auto", "text", "csv"):
        raise ValueError("Invalid capture format")
    if not isinstance(repeats, int) or isinstance(repeats, bool) or not 1 <= repeats <= 20:
        raise ValueError("Repetitions must be between 1 and 20")
    real = [e for e in examples if e.source == "real"]
    groups = [sum(e.kind == kind and not e.challenging for e in real) for kind in ("text", "code", "table")]
    hard = sum(e.challenging for e in real)
    report = dict(schema_version=2, app_level_metrics=1,
                  capture_format_id=("auto", "text", "csv").index(capture_format),
                  executed=int(execute), cloud=int(cloud),
                  budget_usd=float(budget), reserved_usd=0, planned_requests=len(examples) * repeats,
                  real_examples=len(real), synthetic_examples=len(examples) - len(real),
                  real_text_examples=groups[0], real_code_examples=groups[1], real_table_examples=groups[2],
                  real_challenging_examples=hard,
                  real_corpus_complete=int(len(real) >= 30 and all(n >= needed for n, needed in zip(groups, (6, 10, 8))) and hard >= 6),
                  successes=0, failures=0, cancellations=0, budget_refusals=0, budget_overruns=0,
                  startup_seconds=startup_seconds, cold_p50_seconds=None, cold_p95_seconds=None,
                  cold_scope=2 if cloud else 1, latency_successes_only=1,
                  warm_p50_seconds=None, warm_p95_seconds=None, cold_samples=0, warm_samples=0,
                  measured_cost_usd=0 if not cloud else None, rows=[])
    if not execute:
        return report
    if provider is None:
        raise ValueError("Execution requires a provider")
    reserved, total_cost, costs_known = Decimal("0"), Decimal("0"), True
    timings = [[], []]
    attempts = 0
    stop = False
    for example in examples:
        for repeat in range(repeats):
            row = dict(id=example.identifier, repetition=repeat, success=0, failed=0, cancelled=0,
                       budget_refused=0, reserved_usd=0)
            report["rows"].append(row)
            if cloud:
                try:
                    bound = _money(estimate(example)) if estimate else None
                except BudgetError:
                    bound = None
                if budget <= 0 or bound is None or reserved + bound > budget:
                    row["budget_refused"] = 1
                    report["budget_refusals"] += 1
                    stop = True
                    break
                reserved += bound
                row["reserved_usd"] = float(bound)
                report["reserved_usd"] = float(reserved)
            operation = Operation()
            start = clock()
            elapsed = None
            try:
                png = example.image.read_bytes()
                # PNG files are passed literally; other supported image formats
                # are encoded once for the stable PNG provider contract.
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    from PySide6.QtCore import QBuffer, QIODevice
                    from PySide6.QtGui import QImage
                    buffer = QBuffer()
                    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                    if not QImage.fromData(png).save(buffer, "PNG"):
                        raise ValueError("Image encoding failed")
                    png = bytes(buffer.data())
                actual = _app_result(provider, png, operation, capture_format)
                elapsed = clock() - start
                row.update(literal_metrics(example.expected, actual.text, example.kind))
                row["raw_available"] = int(isinstance(actual.raw_text, str))
                if row["raw_available"]:
                    try:
                        row["raw_metrics"] = literal_metrics(example.expected, actual.raw_text, example.kind)
                    except ValueError:
                        # Optional diagnostic limits must not invalidate the main
                        # app-level measurement. Never retain raw content/errors.
                        row["raw_metric_refused"] = 1
                row["success"] = 1
                report["successes"] += 1
            except (Cancelled, KeyboardInterrupt):
                row["cancelled"] = 1
                report["cancellations"] += 1
                stop = True
            except Exception:
                row["failed"] = 1
                report["failures"] += 1
            if elapsed is None:
                elapsed = clock() - start
            group = int(attempts > 0)
            attempts += 1
            if row["success"]:
                timings[group].append(elapsed + (startup_seconds if group == 0 else 0))
            row.update(elapsed_seconds=elapsed, cold=int(group == 0))
            usage = getattr(provider, "last_usage", None) if cloud else None
            if cloud:
                measured = None
                if isinstance(usage, dict):
                    for key in ("prompt_tokens", "completion_tokens"):
                        value = usage.get(key)
                        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                            row[key] = value
                    try:
                        measured = _money(usage.get("cost"))
                    except BudgetError:
                        pass
                row["measured_cost_usd"] = float(measured) if measured is not None else None
                costs_known &= measured is not None
                if measured is not None:
                    total_cost += measured
                    if measured > bound:
                        report["budget_overruns"] += 1
                        stop = True
            if stop or row["failed"]:
                break
        if stop:
            break
    for group, values in zip(("cold", "warm"), timings):
        report[group + "_samples"] = len(values)
        for percent in (50, 95):
            report[f"{group}_p{percent}_seconds"] = percentile(values, percent)
    if cloud and costs_known and attempts:
        report["measured_cost_usd"] = float(total_cost)
    return report


def create_provider(args):
    """Only called after --execute; never installs a model or uses stored user keys."""
    if args.provider == "local":
        from ctrlcctrlv.catalog import MODELS
        from ctrlcctrlv.runtime import LocalRuntime
        from ctrlcctrlv.ocr import OCRProvider
        from ctrlcctrlv.settings import data_directory
        if args.model not in MODELS:
            raise ValueError("Unknown local model")
        runtime = LocalRuntime(args.data_dir or data_directory(), acceleration=args.acceleration)
        # Own only this benchmark's generated loopback secret. The application's
        # OCR and review runtimes may still be using their usual key files.
        runtime.key_filename = "benchmark-session-key-" + uuid.uuid4().hex
        started = time.monotonic()
        try:
            runtime.start(args.model, threading.Event(), lambda *args: None)
            provider = OCRProvider(runtime.endpoint + "/v1/chat/completions", args.model,
                                   runtime.key, args.max_output_tokens, True, MODELS[args.model].mode)
        except BaseException:
            runtime.stop()
            raise
        return provider, runtime.stop, None, time.monotonic() - started
    if args.budget_usd <= 0:
        raise BudgetError("Cloud execution requires a positive budget")
    import httpx
    # Current public metadata only; this is not a paid inference request.
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get("https://openrouter.ai/api/v1/models")
        response.raise_for_status()
        plan = validate_cloud_metadata(response.json(), args.model, args.max_output_tokens)
    if plan.upper_bound > args.budget_usd:
        raise BudgetError("Budget cannot cover the next request upper bound")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise BudgetError("Explicit execution requires OPENROUTER_API_KEY in the environment")
    return CloudProvider(args.model, key, args.max_output_tokens, plan), lambda: None, lambda e: plan.upper_bound, 0


def _write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".benchmark-", delete=False,
                                         mode="w", encoding="utf-8", newline="\n") as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--provider", choices=("local", "openrouter"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-usd", type=_money, default=Decimal("0"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--acceleration", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument("--capture-format", choices=("auto", "text", "csv"), default="auto")
    args = parser.parse_args(argv)
    cleanup = lambda: None
    try:
        if (len(args.model) > 192 or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.:-]*)?", args.model)):
            raise ValueError("Invalid model ID")
        if not 1 <= args.repeats <= 20 or not 1 <= args.max_output_tokens <= 131072:
            raise ValueError("Invalid request limits")
        examples = load_manifest(args.manifest)
        output = args.output.resolve()
        protected = {args.manifest.resolve(), *(e.image for e in examples), *(e.reference for e in examples)}
        if output in protected:
            raise ValueError("Report must not overwrite a fixture or manifest")
        provider, estimate, startup = None, None, 0
        if args.execute:
            provider, cleanup, estimate, startup = create_provider(args)
        report = run_benchmark(examples, provider=provider, execute=args.execute,
                               cloud=args.provider == "openrouter", budget=args.budget_usd,
                               repeats=args.repeats, estimate=estimate, startup_seconds=startup,
                               capture_format=args.capture_format)
        report["model_id"] = args.model
        _write_report(output, report)
        print(json.dumps({k: report[k] for k in ("executed", "planned_requests", "successes", "failures",
                                                "cancellations", "budget_refusals", "reserved_usd")}))
        return 3 if report["failures"] or report["cancellations"] or report["budget_refusals"] or report["budget_overruns"] else 0
    except (Exception, KeyboardInterrupt):
        # Provider error messages/tracebacks may contain paths, text or secrets.
        print("Benchmark validation or execution refused; inspect the local inputs and limits.", file=__import__("sys").stderr)
        return 2
    finally:
        try:
            cleanup()
        except Exception:
            print("Benchmark runtime cleanup failed.", file=__import__("sys").stderr)


if __name__ == "__main__":
    raise SystemExit(main())
