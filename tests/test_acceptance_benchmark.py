"""Synthetic contract fixtures; these are not the external real-image acceptance set."""
import base64
from decimal import Decimal
import json

import pytest
import httpx

from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.ocr import OCRProvider, OCRResult
from ctrlcctrlv.table_ocr import recognition_prompt
from scripts import benchmark_acceptance as benchmark


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAACXBIWXMAAA9hAAAPYQGoP6dpAAAADElEQVQImWP4//8/AAX+Av5Y8msOAAAAAElFTkSuQmCC"
)


class DetailedProvider:
    def __init__(self, result):
        self.result = result
        self.prompts = []

    def recognize_result(self, png, operation, *, prompt):
        assert png == PNG
        operation.check()
        self.prompts.append(prompt)
        return self.result


def test_app_pipeline_converts_html_table_and_keeps_separate_private_raw_metrics(tmp_path):
    expected = 'ID,Amount\n00123,"1,25"\n00007,"0,50"\n'
    raw = '<table><tr><td>ID</td><td>Amount</td></tr><tr><td>00123</td><td>1,25</td></tr><tr><td>00007</td><td>0,50</td></tr></table>'
    examples = benchmark.load_manifest(manifest(tmp_path, expected=expected, kind="table"))
    provider = DetailedProvider(OCRResult(raw, raw))
    report = benchmark.run_benchmark(examples, provider=provider, execute=True)
    row = report["rows"][0]
    assert provider.prompts == [recognition_prompt("auto")]
    assert row["success"] == row["exact"] == row["csv_cells_exact"] == 1
    assert row["raw_available"] == 1
    assert row["raw_metrics"]["csv_cells_exact"] == 0
    assert row["raw_metrics"]["exact"] == 0
    assert row["raw_metrics"] == benchmark.literal_metrics(expected, raw, "table")
    assert report["app_level_metrics"] == 1
    serialized = json.dumps(report)
    for literal in ("<table>", "00123", "1,25", "00007", "Amount", expected):
        assert literal not in serialized


@pytest.mark.parametrize("kind", ["text", "code"])
def test_app_pipeline_preserves_text_and_cleaned_code_with_raw_metrics(tmp_path, kind):
    expected = "print('literal')\n\n"
    raw = "```python\n" + expected + "```\n"
    provider = DetailedProvider(OCRResult(expected, raw))
    examples = benchmark.load_manifest(manifest(tmp_path, expected=expected, kind=kind))
    report = benchmark.run_benchmark(examples, provider=provider, execute=True)
    assert report["rows"][0]["exact"] == 1
    assert report["rows"][0]["raw_metrics"]["exact"] == 0
    assert provider.prompts == [recognition_prompt("auto")]


def test_text_capture_format_preserves_whole_table_as_literal_text(tmp_path):
    raw = "<table><tr><td>00123</td></tr></table>"
    examples = benchmark.load_manifest(manifest(tmp_path, expected=raw, kind="text"))
    provider = DetailedProvider(OCRResult(raw, raw))
    report = benchmark.run_benchmark(examples, provider=provider, execute=True, capture_format="text")
    assert report["rows"][0]["exact"] == 1
    assert provider.prompts == [recognition_prompt("text")]


def test_legacy_ocr_provider_override_remains_the_effective_api(tmp_path):
    class LegacyProvider(OCRProvider):
        def recognize(self, png, operation, *, prompt=None):
            assert prompt == recognition_prompt("auto")
            return "legacy result"

    examples = benchmark.load_manifest(manifest(tmp_path, expected="legacy result", kind="text"))
    provider = LegacyProvider("invalid-endpoint", "fake-model", "fake-secret")
    report = benchmark.run_benchmark(examples, provider=provider, execute=True)
    assert report["rows"][0]["exact"] == 1
    assert report["rows"][0]["raw_available"] == 0


def manifest(tmp_path, *, expected="Привет\r\n\t😀\r\n\r\n", **fields):
    (tmp_path / "sample.png").write_bytes(PNG)
    (tmp_path / "reference.txt").write_bytes(expected.encode("utf-8"))
    row = dict(id="sample-01", image="sample.png", text="reference.txt", kind="code",
               language="ru", font_size="small", source="synthetic")
    row.update(fields)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    return path


def test_manifest_preserves_literal_utf8_reference_bytes(tmp_path):
    examples = benchmark.load_manifest(manifest(tmp_path))
    assert examples[0].expected == "Привет\r\n\t😀\r\n\r\n"
    assert examples[0].identifier == "sample-01"


@pytest.mark.parametrize("field,value", [
    ("image", "../outside.png"), ("text", "C:/secret.txt"),
    ("image", "\\\\server\\secret.png"), ("text", "reference.txt:stream"),
    ("id", "private/path"), ("id", 1), ("kind", "prose"),
    ("language", ""), ("font_size", None), ("source", "invented"),
])
def test_manifest_rejects_unsafe_paths_and_metadata(tmp_path, field, value):
    with pytest.raises(benchmark.ManifestError):
        benchmark.load_manifest(manifest(tmp_path, **{field: value}))


def test_manifest_rejects_duplicate_ids_invalid_utf8_and_missing_files(tmp_path):
    path = manifest(tmp_path)
    rows = json.loads(path.read_text())
    path.write_text(json.dumps(rows * 2))
    with pytest.raises(benchmark.ManifestError):
        benchmark.load_manifest(path)
    path = manifest(tmp_path)
    (tmp_path / "reference.txt").write_bytes(b"\xff")
    with pytest.raises(benchmark.ManifestError):
        benchmark.load_manifest(path)
    (tmp_path / "reference.txt").unlink()
    with pytest.raises(benchmark.ManifestError):
        benchmark.load_manifest(path)


def test_manifest_resolves_symlinks_before_accepting_path(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.txt")
    outside.write_text("outside")
    path = manifest(tmp_path, text="reference-link.txt")
    try:
        (tmp_path / "reference-link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or privileges")
    with pytest.raises(benchmark.ManifestError):
        benchmark.load_manifest(path)


def test_manifest_rejects_secret_or_unrecognized_fields(tmp_path):
    path = manifest(tmp_path, api_key="fake-secret")
    with pytest.raises(benchmark.ManifestError):
        benchmark.load_manifest(path)


@pytest.mark.parametrize("expected,actual,exact,one_newline", [
    ("x\n", "x", 0, 1), ("x\r\n", "x", 0, 1),
    ("x\n\n", "x", 0, 0), ("x\r\n", "x\n", 0, 1),
    ("x ", "x", 0, 0), ("😀", "😀", 1, 1),
])
def test_exact_metrics_only_ignore_one_final_line_ending(expected, actual, exact, one_newline):
    metrics = benchmark.literal_metrics(expected, actual, "text")
    assert metrics["exact"] == exact
    assert metrics["exact_ignoring_one_final_newline"] == one_newline


def test_character_and_layout_metrics_do_not_normalize_tabs_spaces_or_blank_lines():
    metrics = benchmark.literal_metrics("\tA\n\n", " A\n", "code")
    assert metrics["character_errors"] == 2
    assert metrics["character_accuracy"] == pytest.approx(0.5)
    assert metrics["indentation_exact"] == 0
    assert metrics["blank_lines_exact"] == 0
    assert benchmark.literal_metrics("", "x", "text")["character_accuracy"] == 0
    assert benchmark.literal_metrics("", "", "text")["character_accuracy"] == 1


def test_csv_metrics_compare_literal_cells_and_count_extras():
    expected = '001,"a,b","=SUM(A1)","line1\r\nline2"\r\n'
    actual = '1,"a,b","=SUM(A1)","line1\r\nline2",extra\n'
    metrics = benchmark.literal_metrics(expected, actual, "table")
    assert metrics["csv_valid"] == 1
    assert metrics["csv_cells_exact"] == 0
    assert metrics["csv_cell_accuracy"] == pytest.approx(3 / 5)
    assert metrics["csv_cells_compared"] == 5
    malformed = benchmark.literal_metrics(expected, '"unclosed', "table")
    assert malformed["csv_valid"] == 0
    assert malformed["csv_cell_accuracy"] == 0


def test_percentiles_use_linear_interpolation_and_keep_empty_groups_unknown():
    assert benchmark.percentile([1, 2, 3, 4], 50) == 2.5
    assert benchmark.percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)
    assert benchmark.percentile([], 95) is None


class FakeProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0
        self.last_usage = None

    def recognize(self, png, operation):
        assert png == PNG
        operation.check()
        self.calls += 1
        result = next(self.responses)
        if isinstance(result, BaseException):
            raise result
        return result


def test_dry_run_never_constructs_provider_or_consumes_budget(tmp_path):
    examples = benchmark.load_manifest(manifest(tmp_path))
    report = benchmark.run_benchmark(examples, cloud=True, provider=None)
    assert report["executed"] == 0
    assert report["budget_usd"] == 0
    assert report["reserved_usd"] == 0
    assert report["planned_requests"] == 1
    assert report["real_corpus_complete"] == 0


def test_actual_runner_computes_metrics_and_does_not_leak_fixture_or_error_data(tmp_path):
    examples = benchmark.load_manifest(manifest(tmp_path, expected="private OCR\n"))
    provider = FakeProvider(["private OCR", OperationError("secret-key user/path private OCR")])
    ticks = iter([0, 1, 2, 4])
    report = benchmark.run_benchmark(examples, provider=provider, execute=True, repeats=3,
                                     clock=lambda: next(ticks))
    assert len(report["rows"]) == 2  # Failed fixture does not get an automatic retry.
    assert report["successes"] == 1 and report["failures"] == 1
    assert report["rows"][0]["exact"] == 0
    assert report["rows"][0]["exact_ignoring_one_final_newline"] == 1
    assert report["cold_p50_seconds"] == 1
    assert report["warm_p95_seconds"] is None
    assert report["rows"][1]["elapsed_seconds"] == 2
    serialized = json.dumps(report)
    for private in ("private OCR", "secret-key", "user/path", str(tmp_path), "sample.png"):
        assert private not in serialized


def test_budget_reserves_full_image_and_max_output_cost_before_each_call(tmp_path):
    examples = benchmark.load_manifest(manifest(tmp_path, expected="ok"))
    quote = benchmark.PriceQuote(Decimal("0.001"), Decimal("0.002"),
                                  image=Decimal("0.03"), request=Decimal("0.01"))
    # 10 input tokens (including image tokens), 20 maximum output, one image: $0.09.
    assert quote.upper_bound(10, 20, 1) == Decimal("0.09")
    provider = FakeProvider(["ok", "must not be requested"])
    report = benchmark.run_benchmark(examples, provider=provider, execute=True, cloud=True,
                                     repeats=2, budget=Decimal("0.179"),
                                     estimate=lambda example: quote.upper_bound(10, 20, 1))
    assert provider.calls == 1
    assert report["reserved_usd"] == 0.09
    assert report["budget_refusals"] == 1
    assert report["successes"] == 1
    assert report["measured_cost_usd"] is None


@pytest.mark.parametrize("budget,estimate", [
    (Decimal("0"), Decimal("0.01")), (Decimal("1"), None),
    (Decimal("1"), Decimal("NaN")), (Decimal("1"), Decimal("-1")),
])
def test_unknown_or_unsafe_estimate_never_sends_cloud_request(tmp_path, budget, estimate):
    examples = benchmark.load_manifest(manifest(tmp_path))
    provider = FakeProvider(["unused"])
    report = benchmark.run_benchmark(examples, provider=provider, execute=True, cloud=True,
                                     budget=budget, estimate=lambda example: estimate)
    assert provider.calls == 0
    assert report["budget_refusals"] == 1


def test_cancelled_cloud_request_keeps_reservation_and_stops_without_paid_retry(tmp_path):
    examples = benchmark.load_manifest(manifest(tmp_path))
    provider = FakeProvider([Cancelled("secret image"), "unused"])
    report = benchmark.run_benchmark(examples, provider=provider, execute=True, cloud=True,
                                     repeats=3, budget=Decimal("1"),
                                     estimate=lambda example: Decimal("0.4"))
    assert provider.calls == 1
    assert report["cancellations"] == 1
    assert report["reserved_usd"] == 0.4


def test_cli_defaults_to_offline_validation_and_redacts_invalid_input(tmp_path, monkeypatch, capsys):
    path = manifest(tmp_path)
    output = tmp_path / "report.json"
    monkeypatch.setattr(benchmark, "create_provider", lambda *a, **k: pytest.fail("executed"))
    assert benchmark.main(["--manifest", str(path), "--provider", "openrouter", "--model",
                           "org/vision", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["executed"] == 0 and report["budget_usd"] == 0
    assert str(tmp_path) not in capsys.readouterr().out
    missing = tmp_path / "secret-folder" / "private.json"
    assert benchmark.main(["--manifest", str(missing), "--provider", "local", "--model",
                           "lightonocr-3-0.8b", "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert str(missing) not in captured.err


def cloud_metadata():
    return {"data": [{"id": "org/vision", "architecture": {"input_modalities": ["text", "image"]},
                      "context_length": 100, "pricing": {"prompt": "0.001", "completion": "0.002",
                                                         "image": "0.03", "request": "0.01"}}]}


def test_cloud_metadata_uses_whole_context_as_input_bound_including_image_tokens():
    plan = benchmark.validate_cloud_metadata(cloud_metadata(), "org/vision", 20)
    assert plan.upper_bound == Decimal("0.18")


@pytest.mark.parametrize("omitted", [("image",), ("request",), ("image", "request")])
def test_cloud_metadata_missing_optional_fees_still_reserves_all_input_and_output(omitted):
    metadata = cloud_metadata()
    prices = metadata["data"][0]["pricing"]
    for key in omitted:
        del prices[key]
    plan = benchmark.validate_cloud_metadata(metadata, "org/vision", 20)
    expected = {("image",): "0.15", ("request",): "0.17", ("image", "request"): "0.14"}
    assert plan.upper_bound == Decimal(expected[omitted])
    assert plan.quote.prompt == Decimal("0.001")


@pytest.mark.parametrize("cache_price", ["0", "0.0005", "0.001"])
def test_cloud_metadata_cache_read_discount_never_reduces_reservation(cache_price):
    metadata = cloud_metadata()
    metadata["data"][0]["pricing"]["input_cache_read"] = cache_price
    plan = benchmark.validate_cloud_metadata(metadata, "org/vision", 20)
    assert plan.upper_bound == Decimal("0.18")


@pytest.mark.parametrize("fee,value", [("input_cache_read", "0.0011"),
                                     ("input_cache_read", "NaN"),
                                     ("input_cache_read", "-0.0001"),
                                     ("input_cache_read", None),
                                     ("input_cache_write", "0.0005"),
                                     ("future_paid_fee", "0.01"),
                                     ("overrides", [{"prompt": "0.02"}])])
def test_cloud_metadata_unknown_or_non_discount_billing_still_refuses(fee, value):
    metadata = cloud_metadata()
    metadata["data"][0]["pricing"][fee] = value
    with pytest.raises(benchmark.BudgetError):
        benchmark.validate_cloud_metadata(metadata, "org/vision", 20)


def test_current_qwen_cache_metadata_caps_transport_and_stops_at_full_reserved_budget(tmp_path):
    metadata = {"data": [{"id": "qwen/qwen3-vl-8b-instruct",
        "architecture": {"input_modalities": ["text", "image"]}, "context_length": 262144,
        "pricing": {"prompt": "0.00000025", "completion": "0.00000075",
                    "input_cache_read": "0.00000012"}}]}
    plan = benchmark.validate_cloud_metadata(metadata, "qwen/qwen3-vl-8b-instruct", 1024)
    assert plan.upper_bound == Decimal("0.066304")
    payloads = []

    def receive(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": '{"text":"ok"}'}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 5, "cost": 0.000005}})

    provider = benchmark.CloudProvider("qwen/qwen3-vl-8b-instruct", "fake-secret", 1024, plan,
                                       transport_factory=lambda: httpx.MockTransport(receive))
    examples = benchmark.load_manifest(manifest(tmp_path, expected="ok"))
    report = benchmark.run_benchmark(examples, execute=True, cloud=True, provider=provider,
                                     repeats=8, budget=Decimal("0.5"), estimate=lambda example: plan.upper_bound)
    assert report["successes"] == len(payloads) == 7
    assert report["reserved_usd"] == 0.464128
    assert report["budget_refusals"] == 1
    assert report["budget_overruns"] == 0
    for payload in payloads:
        assert payload["max_tokens"] == 1024
        assert payload["provider"] == {"allow_fallbacks": False, "require_parameters": True,
            "max_price": {"prompt": 0.25, "completion": 0.75, "image": 0, "request": 0}}


@pytest.mark.parametrize("change", ["no-vision", "unknown-image-price", "extra-billing", "no-context"])
def test_incomplete_cloud_metadata_refuses_execution(change):
    metadata = cloud_metadata()
    model = metadata["data"][0]
    if change == "no-vision":
        model["architecture"]["input_modalities"] = ["text"]
    elif change == "unknown-image-price":
        model["pricing"]["image"] = None
    elif change == "extra-billing":
        model["pricing"]["image_output"] = "1"
    else:
        model["context_length"] = None
    with pytest.raises(benchmark.BudgetError):
        benchmark.validate_cloud_metadata(metadata, "org/vision", 20)


def test_cloud_transport_uses_real_ocr_contract_with_caps_and_numeric_usage(tmp_path):
    examples = benchmark.load_manifest(manifest(tmp_path, expected="ok"))
    plan = benchmark.validate_cloud_metadata(cloud_metadata(), "org/vision", 20)
    payloads = []

    def receive(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                    "message": {"content": '{"text":"ok"}'}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 5, "cost": 0.025}})

    provider = benchmark.CloudProvider("org/vision", "fake-secret", 20, plan,
                                        transport_factory=lambda: httpx.MockTransport(receive))
    report = benchmark.run_benchmark(examples, execute=True, cloud=True, provider=provider,
                                     budget=Decimal("0.18"), estimate=lambda example: plan.upper_bound)
    assert report["successes"] == 1
    assert report["measured_cost_usd"] == 0.025
    assert report["rows"][0]["prompt_tokens"] == 4
    assert report["reserved_usd"] == 0.18
    assert payloads[0]["max_tokens"] == 20
    assert payloads[0]["provider"]["allow_fallbacks"] is False
    assert payloads[0]["provider"]["max_price"] == {
        "prompt": 1000, "completion": 2000, "image": 0.03, "request": 0.01}
    assert "fake-secret" not in json.dumps(report)


@pytest.mark.parametrize("encoding,chunked", [("gzip", False), ("gzip", True),
                                               ("deflate", True), (None, True)])
def test_cloud_stream_decodes_once_before_numeric_usage_and_literal_metrics(tmp_path, encoding, chunked):
    import gzip
    import zlib

    examples = benchmark.load_manifest(manifest(tmp_path, expected="ok"))
    plan = benchmark.validate_cloud_metadata(cloud_metadata(), "org/vision", 20)
    body = json.dumps({"choices": [{"finish_reason": "stop",
        "message": {"content": '{"text":"ok"}'}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 5, "cost": 0.025}}).encode()
    wire = gzip.compress(body) if encoding == "gzip" else zlib.compress(body) if encoding == "deflate" else body
    calls = []

    class WireChunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            chunks = (wire[:5], wire[5:17], wire[17:]) if chunked else (wire,)
            for chunk in chunks:
                yield chunk

    def receive(request):
        calls.append(1)
        headers = {"content-type": "application/json"}
        if encoding:
            headers["content-encoding"] = encoding
        headers.update({"transfer-encoding": "chunked"} if chunked else {"content-length": str(len(wire))})
        return httpx.Response(200, headers=headers, stream=WireChunks())

    provider = benchmark.CloudProvider("org/vision", "fake-secret", 20, plan,
                                       transport_factory=lambda: httpx.MockTransport(receive))
    report = benchmark.run_benchmark(examples, execute=True, cloud=True, provider=provider,
                                     budget=Decimal("0.18"), estimate=lambda example: plan.upper_bound)
    assert len(calls) == 1
    assert report["successes"] == 1
    assert report["rows"][0]["exact"] == 1
    assert report["failures"] == 0
    assert report["measured_cost_usd"] == 0.025
    assert report["rows"][0]["prompt_tokens"] == 4
    assert report["rows"][0]["completion_tokens"] == 5
    assert "fake-secret" not in json.dumps(report)


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_cloud_http_errors_are_not_retried_or_echoed(tmp_path, status):
    examples = benchmark.load_manifest(manifest(tmp_path))
    plan = benchmark.validate_cloud_metadata(cloud_metadata(), "org/vision", 20)
    calls = []

    def receive(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "secret OCR personal/path"}})

    provider = benchmark.CloudProvider("org/vision", "fake-secret", 20, plan,
                                        transport_factory=lambda: httpx.MockTransport(receive))
    report = benchmark.run_benchmark(examples, execute=True, cloud=True, provider=provider,
                                     repeats=3, budget=Decimal("1"), estimate=lambda example: plan.upper_bound)
    assert len(calls) == 1
    assert report["failures"] == 1
    assert report["reserved_usd"] == 0.18
    assert "secret OCR" not in json.dumps(report)


def test_cli_refuses_fixture_overwrite_before_creating_local_provider(tmp_path, monkeypatch):
    path = manifest(tmp_path)
    monkeypatch.setattr(benchmark, "create_provider", lambda *a: pytest.fail("must not execute"))
    original = (tmp_path / "reference.txt").read_bytes()
    assert benchmark.main(["--manifest", str(path), "--provider", "local", "--model",
                           "lightonocr-3-0.8b", "--output", str(tmp_path / "reference.txt"),
                           "--execute"]) == 2
    assert (tmp_path / "reference.txt").read_bytes() == original


def test_zero_budget_cloud_cli_refuses_before_any_network_or_key_lookup(tmp_path, monkeypatch):
    path = manifest(tmp_path)
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("network lookup"))
    original_get = benchmark.os.environ.get
    def environment_get(key, *args):
        if key == "OPENROUTER_API_KEY":
            pytest.fail("key lookup")
        return original_get(key, *args)
    monkeypatch.setattr(benchmark.os.environ, "get", environment_get)
    assert benchmark.main(["--manifest", str(path), "--provider", "openrouter", "--model",
                           "org/vision", "--output", str(tmp_path / "report.json"), "--execute"]) == 2


def test_invalid_model_path_cannot_enter_report(tmp_path):
    path = manifest(tmp_path)
    output = tmp_path / "report.json"
    assert benchmark.main(["--manifest", str(path), "--provider", "openrouter", "--model",
                           "C:/Users/private", "--output", str(output)]) == 2
    assert not output.exists()


def test_local_runtime_owns_unique_session_key_and_does_not_install(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ctrlcctrlv import runtime as runtime_module
    runtimes = []

    class FakeRuntime:
        def __init__(self, directory, *, acceleration):
            self.key_filename = "session-key"
            self.endpoint, self.key = "http://127.0.0.1:1", "generated-fake-key"
            runtimes.append(self)

        def start(self, model, cancel, progress):
            assert self.key_filename != "session-key"

        def stop(self):
            pass

    monkeypatch.setattr(runtime_module, "LocalRuntime", FakeRuntime)
    args = SimpleNamespace(provider="local", model="lightonocr-3-0.8b", data_dir=tmp_path,
                           acceleration="cpu", max_output_tokens=20)
    for _ in range(2):
        provider, cleanup, estimate, elapsed = benchmark.create_provider(args)
        assert provider.local and provider.mode == "transcription"
        assert estimate is None and elapsed >= 0
        cleanup()
    assert runtimes[0].key_filename != runtimes[1].key_filename


def test_cloud_cap_rounding_never_increases_declared_price():
    assert Decimal(str(benchmark._price_cap(Decimal("0.123456789123456789")))) <= Decimal("0.123456789123456789")


def test_cloud_usage_reservation_overrun_stops_next_request(tmp_path):
    examples = benchmark.load_manifest(manifest(tmp_path, expected="ok"))
    provider = FakeProvider(["ok", "must not run"])
    provider.last_usage = {"cost": "0.3"}
    report = benchmark.run_benchmark(examples, execute=True, cloud=True, provider=provider,
                                     repeats=2, budget=Decimal("1"), estimate=lambda example: Decimal("0.2"))
    assert provider.calls == 1
    assert report["budget_overruns"] == 1
    assert report["measured_cost_usd"] == 0.3


def test_inference_latency_excludes_character_metric_computation(tmp_path, monkeypatch):
    examples = benchmark.load_manifest(manifest(tmp_path, expected="ok"))
    now = [0]

    class TimedProvider(FakeProvider):
        def recognize(self, png, operation):
            now[0] += 1
            return super().recognize(png, operation)

    actual_metrics = benchmark.literal_metrics
    def expensive_metrics(*args):
        now[0] += 100
        return actual_metrics(*args)
    monkeypatch.setattr(benchmark, "literal_metrics", expensive_metrics)
    report = benchmark.run_benchmark(examples, execute=True, provider=TimedProvider(["ok"]),
                                     clock=lambda: now[0], startup_seconds=5)
    assert report["rows"][0]["elapsed_seconds"] == 1
    assert report["cold_p50_seconds"] == 6


def test_cloud_timeout_is_recorded_without_repeating_paid_attempt(tmp_path):
    examples = benchmark.load_manifest(manifest(tmp_path))
    plan = benchmark.validate_cloud_metadata(cloud_metadata(), "org/vision", 20)
    calls = []
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("private response")
    provider = benchmark.CloudProvider("org/vision", "fake-secret", 20, plan,
                                        transport_factory=lambda: httpx.MockTransport(timeout))
    report = benchmark.run_benchmark(examples, execute=True, cloud=True, provider=provider,
                                     repeats=3, budget=Decimal("1"), estimate=lambda example: plan.upper_bound)
    assert len(calls) == 1
    assert report["failures"] == 1 and report["reserved_usd"] == 0.18
    assert "private response" not in json.dumps(report)


def test_failed_initial_attempt_does_not_turn_next_success_into_cold_sample(tmp_path):
    path = manifest(tmp_path, expected="ok")
    rows = json.loads(path.read_text())
    second = dict(rows[0], id="sample-02")
    path.write_text(json.dumps(rows + [second]))
    examples = benchmark.load_manifest(path)
    ticks = iter([0, 1, 2, 4])
    report = benchmark.run_benchmark(examples, execute=True,
                    provider=FakeProvider([OperationError("failed"), "ok"]), clock=lambda: next(ticks))
    assert report["cold_samples"] == 0 and report["cold_p50_seconds"] is None
    assert report["warm_samples"] == 1 and report["warm_p50_seconds"] == 2
    assert report["rows"][1]["cold"] == 0


def test_dry_run_complete_manifest_only_claims_corpus_composition(tmp_path):
    from dataclasses import replace
    example = benchmark.load_manifest(manifest(tmp_path))[0]
    examples = [replace(example, identifier=f"example-{i:02}", source="real", kind=kind,
                        challenging=challenging)
                for i, (kind, challenging) in enumerate(
                    [("text", False)] * 6 + [("code", False)] * 10 +
                    [("table", False)] * 8 + [("text", True)] * 6)]
    report = benchmark.run_benchmark(examples, execute=False)
    assert report["real_corpus_complete"] == 1
    assert "real_acceptance_complete" not in report
    assert report["executed"] == 0 and report["successes"] == 0
    assert report["rows"] == [] and report["cold_p50_seconds"] is None
