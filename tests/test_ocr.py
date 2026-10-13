import json
import socket
import threading
import time

import httpx
import pytest

from ctrlcctrlv.core import Cancelled, DeadlineExceeded, Operation, OperationError
from ctrlcctrlv.ocr import OCRProvider, parse_completion


def completion(text, reason="stop", raw=False):
    return {"choices": [{"finish_reason": reason, "message": {
        "content": text if raw else json.dumps({"text": text}, ensure_ascii=False)}}]}


def test_literal_code_and_blank_lines_survive_json_transport():
    expected = '    if x == "Ж🙂":\n\n\tprint("a|b", \'’\')  \n'
    result = []

    def request(req):
        payload = json.loads(req.content)
        assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json=completion(expected))

    provider = OCRProvider("https://example.invalid/ocr", "model", "test-key")
    op = Operation()
    text = provider.recognize(b"png", op, httpx.MockTransport(request))
    op.publish(text, result.append)
    assert result == [expected]


@pytest.mark.parametrize("reason", ["length", "content_filter", "error", None])
def test_incomplete_response_never_accepted(reason):
    with pytest.raises(OperationError, match="неполный"):
        parse_completion(completion("partial", reason))


@pytest.mark.parametrize("payload", [{}, {"choices": []}, completion("not-json", raw=True),
    {"choices": [{"finish_reason": "stop", "message": {"content": '{"text": 42}'}}]}])
def test_malformed_result_rejected(payload):
    with pytest.raises(OperationError):
        parse_completion(payload)


def test_specialized_ocr_gets_image_only_and_literal_markdown():
    text = "```python\n    x = 1\n```\n"

    def request(req):
        payload = json.loads(req.content)
        assert "response_format" not in payload
        assert [item["type"] for item in payload["messages"][0]["content"]] == ["image_url"]
        return httpx.Response(200, json=completion(text, raw=True))

    provider = OCRProvider("http://127.0.0.1/ocr", "other-transcriber", "test", local=True, mode="transcription")
    assert provider.recognize(b"png", Operation(), httpx.MockTransport(request)) == text


@pytest.mark.parametrize('prefix', ['python\n', 'python\r\n', r'python\n'])
def test_lighton_language_label_is_removed_before_publication(prefix):
    expected = '    def greet(name):\n\n\tprint("python\\n", name)  \n'
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=completion(prefix + expected, raw=True)))
    provider = OCRProvider('http://localhost/ocr', 'lightonocr-3-0.8b', 'key', local=True, mode='transcription')
    operation = Operation()
    published = []
    operation.publish(provider.recognize(b'png', operation, transport), published.append)
    assert published == [expected]


@pytest.mark.parametrize('text', ['python\nThis is a language name.\n', 'python = 3\nprint(python)\n',
                                 'python\nfrom the documentation, choose the correct version.\n',
                                 'python\nif you need help, read the documentation.\n',
                                 'def f():\n    python = "python"\n', 'python\n'])
def test_lighton_cleanup_preserves_literal_text_and_identifiers(text):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=completion(text, raw=True)))
    provider = OCRProvider('http://localhost/ocr', 'lightonocr-3-0.8b', 'key', local=True, mode='transcription')
    assert provider.recognize(b'png', Operation(), transport) == text


def test_lighton_python_fence_is_unwrapped_without_stripping_code_whitespace():
    expected = '\n    x = 1  \n\n'
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=completion('```python\n' + expected + '```\n', raw=True)))
    provider = OCRProvider('http://localhost/ocr', 'lightonocr-3-0.8b', 'key', local=True, mode='transcription')
    assert provider.recognize(b'png', Operation(), transport) == expected


@pytest.mark.parametrize('expected', [
    '27  def main():\n28      parser = argparse.ArgumentParser()\n29      parser.parse_args()\n',
    '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n',
    '[build-system]\nrequires = ["setuptools>=68"]\n\n[project]\ninvalid OCR fragment\n',
    'import argparse\n\ndef main():\n    pass\n',
])
def test_lighton_label_cleanup_preserves_numbered_code_and_configuration(expected):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=completion('python\n' + expected, raw=True)))
    provider = OCRProvider('http://localhost/ocr', 'lightonocr-3-0.8b', 'key', local=True, mode='transcription')
    assert provider.recognize(b'png', Operation(), transport) == expected


def test_deadline_before_publication_preserves_old_clipboard():
    clipboard = ["old"]
    op = Operation(budget=4, started=time.monotonic() - 4.01)
    with pytest.raises(DeadlineExceeded):
        op.publish("new", lambda text: clipboard.__setitem__(0, text))
    assert clipboard == ["old"]


def test_cancellation_before_publication_preserves_old_clipboard():
    op = Operation()
    op.cancel()
    result = []
    with pytest.raises(Cancelled):
        op.publish("new", result.append)
    assert result == []


def test_network_time_is_included_in_total_deadline():
    def request(req):
        time.sleep(0.03)
        return httpx.Response(200, json=completion("new"))

    with pytest.raises(DeadlineExceeded):
        OCRProvider("https://example.invalid/ocr", "model", "key").recognize(
            b"png", Operation(budget=4, started=time.monotonic() - 3.99), httpx.MockTransport(request))


def test_sensitive_error_body_not_exposed():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="private screenshot and key"))
    with pytest.raises(OperationError) as error:
        OCRProvider("https://example.invalid", "model", "secret").recognize(b"png", Operation(), transport)
    assert "private" not in str(error.value)
    assert "secret" not in str(error.value)


def test_ocr_speed_target_does_not_expire_request_or_publication():
    def request(req):
        assert req.extensions["timeout"]["read"] == 120
        return httpx.Response(200, json=completion("full result after target"))

    operation = Operation(started=time.monotonic() - 5)
    provider = OCRProvider("https://example.invalid", "model", "key")
    text = provider.recognize(b"png", operation, httpx.MockTransport(request))
    published = []
    elapsed = operation.publish(text, published.append)
    assert elapsed > 4
    assert published == ["full result after target"]


def test_technical_network_timeout_is_distinct_from_speed_target():
    def request(req):
        raise httpx.ReadTimeout("private data")

    with pytest.raises(OperationError, match="долго не отвечает") as error:
        OCRProvider("https://example.invalid", "model", "key").recognize(
            b"png", Operation(), httpx.MockTransport(request))
    assert "private" not in str(error.value)


@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize("local", [True, False])
def test_cancel_closes_socket_while_server_is_not_sending_data(phase, local, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    listening.listen(1)
    listening.settimeout(2)
    received, disconnected = threading.Event(), threading.Event()
    server_errors, client_errors = [], []

    def server():
        try:
            with listening.accept()[0] as connection:
                connection.settimeout(2)
                data = b""
                while b"\r\n\r\n" not in data:
                    data += connection.recv(65536)
                headers, body = data.split(b"\r\n\r\n", 1)
                length = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                              if line.lower().startswith(b"content-length:"))
                while len(body) < length:
                    body += connection.recv(65536)
                if phase == "body":
                    connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{")
                received.set()
                if connection.recv(1) == b"":
                    disconnected.set()
        except Exception as error:
            server_errors.append(error)

    operation = Operation()
    endpoint = f"http://127.0.0.1:{listening.getsockname()[1]}/ocr"

    def client():
        try:
            OCRProvider(endpoint, "test", "key", local=local).recognize(b"png", operation)
        except Exception as error:
            client_errors.append(error)

    server_thread = threading.Thread(target=server)
    client_thread = threading.Thread(target=client)
    server_thread.start()
    client_thread.start()
    try:
        assert received.wait(2)
        started = time.monotonic()
        operation.cancel()
        client_thread.join(1)
        assert not client_thread.is_alive()
        assert time.monotonic() - started < 1
        assert len(client_errors) == 1 and isinstance(client_errors[0], Cancelled)
        assert disconnected.wait(1)
        assert not server_errors
    finally:
        operation.cancel()
        listening.close()
        client_thread.join(3)
        server_thread.join(3)
