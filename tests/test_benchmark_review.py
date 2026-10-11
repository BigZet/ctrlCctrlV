import json
from types import SimpleNamespace

from ctrlcctrlv.review import ReviewResult
from scripts import benchmark_review


def test_review_benchmark_keeps_utf8_and_records_successful_metrics(tmp_path, monkeypatch, capsys):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    expected = "    print('Привет')\n\n"
    original = expected.lstrip(" ")
    (fixtures / "case.txt").write_text(expected, encoding="utf-8")
    (fixtures / "case.png").write_bytes(b"synthetic PNG")
    (fixtures / "manifest.json").write_text(json.dumps([
        {"id": "unicode", "image": "case.png", "text": "case.txt"}
    ]), encoding="utf-8")
    output = tmp_path / "result.json"
    calls = []
    runtime = SimpleNamespace(endpoint="http://127.0.0.1:1", key="synthetic",
                              start=lambda identifier, *a: calls.append(identifier),
                              stop=lambda: calls.append("stop"))
    monkeypatch.setattr(benchmark_review, "LocalRuntime", lambda *a, **k: runtime)
    monkeypatch.setattr(benchmark_review, "OCRProvider", lambda *a, **k:
                        SimpleNamespace(recognize=lambda *a: original))
    monkeypatch.setattr(benchmark_review, "ReviewProvider", lambda *a:
                        SimpleNamespace(inspect=lambda png, text: ReviewResult(text, expected, "checker", 0.1)))
    monkeypatch.setattr("sys.argv", ["benchmark_review", "--fixtures", str(fixtures),
                                     "--output", str(output), "--data-dir", str(tmp_path), "--limit", "1"])
    benchmark_review.main()
    rows = json.loads(output.read_text(encoding="utf-8"))
    assert len(rows) == 1 and "ocr_error" not in rows[0] and "review_error" not in rows[0]
    assert rows[0]["before_errors"] == 4 and rows[0]["after_errors"] == 0
    assert rows[0]["ocr_elapsed"] >= 0 and rows[0]["review_elapsed"] == 0.1
    assert calls == ["lightonocr-3-0.8b", "stop", "qwen3-vl-4b", "stop"]
    assert expected not in capsys.readouterr().out
