"""Direct-copy integration matrix using real OCR parsing and mock HTTP only."""
import csv
import io
import json

import httpx
import pytest
from PySide6.QtGui import QImage

from ctrlcctrlv.core import Operation
from ctrlcctrlv.ocr import OCRProvider
from ctrlcctrlv.review import ReviewProvider
from ctrlcctrlv.table_ocr import recognition_prompt
from test_polish_controller import controller, finish


@pytest.mark.parametrize("local", [True, False], ids=["local", "cloud"])
@pytest.mark.parametrize("kind", ["text", "table"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_no_preview_has_one_primary_request_and_no_refinement(
        controller, monkeypatch, local, kind, outcome):
    c = controller
    c.settings.show_preview = False
    c.settings.capture_format = "auto"
    c.settings.provider = "local" if local else "openrouter"
    original_clipboard = "existing literal clipboard\r\n"
    c.app.clipboard().setText(original_clipboard)
    expected = ('ID,Amount\n00123,"1,25"\n' if kind == "table" else "    Привет 😀\n\n")
    native = ("<table><tr><td>ID</td><td>Amount</td></tr>"
              "<tr><td>00123</td><td>1,25</td></tr></table>" if kind == "table" else expected)
    primary_calls, requests, forbidden = [], [], []
    operation = Operation()

    def receive(request):
        requests.append(json.loads(request.content))
        if outcome == "error":
            return httpx.Response(429, json={"error": {"message": "synthetic refusal"}})
        if outcome == "cancel":
            operation.cancel()
        content = native if local else json.dumps({"text": native})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                     "message": {"content": content}}]})

    class MockProvider(OCRProvider):
        def recognize_result(self, png, request_operation, *, prompt=None, context=None):
            primary_calls.append(1)
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            assert request_operation is operation
            assert prompt == recognition_prompt("auto")
            return super().recognize_result(png, request_operation, httpx.MockTransport(receive),
                                            prompt=prompt, context=context)

    provider = MockProvider("https://ocr.invalid/v1/chat/completions", "lighton-synthetic" if local
                            else "synthetic-cloud", "fake-test-key", local=local,
                            mode="transcription" if local else "json")
    monkeypatch.setattr(c, "provider", lambda: provider)
    monkeypatch.setattr(c.review_runtime, "start", lambda *a, **k: forbidden.append("heavy"))
    monkeypatch.setattr(ReviewProvider, "inspect", lambda *a, **k: forbidden.append("review"))
    monkeypatch.setattr(c.preview.review_panel, "show", lambda *a, **k: forbidden.append("compare"))
    monkeypatch.setattr(c.preview, "display", lambda *a, **k: forbidden.append("preview"))
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    c.recognize(image, operation)
    finish(c)

    assert primary_calls == [1]
    assert len(requests) == 1
    assert ("chat_template_kwargs" in requests[0]) is local
    assert ("modalities" in requests[0]) is not local
    assert forbidden == []
    assert not c.preview.isVisible()
    assert not c.preview.review_panel.isVisible()
    assert c.preview.raw_response is None
    entries = c.history.entries()
    if outcome == "success":
        assert c.app.clipboard().text() == expected
        assert len(entries) == 1
        assert entries[0].text == expected
        assert entries[0].model == provider.model
        if kind == "table":
            assert list(csv.reader(io.StringIO(entries[0].text))) == [["ID", "Amount"], ["00123", "1,25"]]
    else:
        assert c.app.clipboard().text() == original_clipboard
        assert entries == []
