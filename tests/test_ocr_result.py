"""Only the parsed transcription, before cleanup, is exposed for explicit inspection."""
import json

import httpx
import pytest

from ctrlcctrlv.core import Operation, OperationError, Cancelled
from ctrlcctrlv.ocr import OCRProvider


@pytest.mark.parametrize('mode,model,raw,clean', [
    ('transcription', 'lightonocr-3-0.8b', 'python\ndef f():\n    return 1\n', 'def f():\n    return 1\n'),
    ('transcription', 'lightonocr-3-0.8b', '```python\nx = 1\n```\n', 'x = 1\n'),
    ('json', 'vision-model', 'Name,Qty\nPaper,001\n', 'Name,Qty\nPaper,001\n'),
])
def test_result_retains_raw_transcription_and_makes_only_one_request(mode, model, raw, clean):
    requests = []
    def receive(request):
        requests.append(request)
        content = raw if mode == 'transcription' else json.dumps({'text': raw})
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})
    provider = OCRProvider('https://example.invalid', model, 'not-a-real-key', mode=mode)
    assert hasattr(provider, 'recognize_result')
    result = provider.recognize_result(b'png', Operation(), httpx.MockTransport(receive))
    assert result.text == clean
    assert result.raw_text == raw
    assert len(requests) == 1
    assert 'not-a-real-key' not in repr(result)


@pytest.mark.parametrize('reason', ['length', 'error'])
def test_raw_result_is_not_published_for_incomplete_response(reason):
    provider = OCRProvider('https://example.invalid', 'vision-model', '')
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={
        'choices': [{'finish_reason': reason, 'message': {'content': '{"text":"partial"}'}}]}))
    assert hasattr(provider, 'recognize_result')
    with pytest.raises(OperationError):
        provider.recognize_result(b'png', Operation(), transport)


def test_cancelled_raw_result_makes_no_request():
    operation = Operation()
    operation.cancel()
    provider = OCRProvider('https://example.invalid', 'vision-model', '')
    requests = []
    transport = httpx.MockTransport(lambda req: requests.append(req))
    assert hasattr(provider, 'recognize_result')
    with pytest.raises(Cancelled):
        provider.recognize_result(b'png', operation, transport)
    assert requests == []
