"""Explicit smoke checks use synthetic data and a loopback-only HTTP server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from .core import Cancelled, Operation
from .ocr import OCRProvider


def check_ocr_transport():
    entered, disconnected = threading.Event(), threading.Event()
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.connection.settimeout(3)
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/wait":
                entered.set()
                if self.rfile.read(1) == b"":
                    disconnected.set()
                return
            body = json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"text": "synthetic complete result"})}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    operation = Operation()
    pending = None
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        slow = Operation(started=time.monotonic() - 5)
        result = OCRProvider(endpoint + "/complete", "smoke", "synthetic", local=True).recognize(b"png", slow)
        copied = []
        elapsed = slow.publish(result, copied.append)
        if copied != ["synthetic complete result"] or elapsed <= 4:
            return False

        def wait_for_response():
            try:
                OCRProvider(endpoint + "/wait", "smoke", "synthetic", local=True).recognize(b"png", operation)
            except Exception as error:
                failures.append(error)

        pending = threading.Thread(target=wait_for_response, daemon=True)
        pending.start()
        if not entered.wait(3):
            return False
        operation.cancel()
        pending.join(1)
        return (not pending.is_alive() and len(failures) == 1 and isinstance(failures[0], Cancelled)
                and disconnected.wait(1))
    finally:
        operation.cancel()
        if pending:
            pending.join(3)
        server.shutdown()
        server.server_close()
        serving.join(1)
