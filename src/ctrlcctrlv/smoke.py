"""Explicit smoke checks use synthetic data and a loopback-only HTTP server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from .core import Cancelled, Operation
from .ocr import OCRProvider


def check_formatters():
    from .formatting import format_text
    cases = {"python": "x=1\n", "sql": "select a,b from t;", "java": "class X{int x=1;}", "json": '{"x":1,"x":2}'}
    return all(format_text(text, language) != text for language, text in cases.items())


def check_table_exports():
    import csv
    from pathlib import Path
    import tempfile
    from zipfile import ZipFile
    from .table_export import export_table
    from .text_export import save_text, suggest_extension
    with tempfile.TemporaryDirectory(prefix="ctrlc-export-smoke-") as directory:
        code = '    print("Привет")  \n'
        if suggest_extension(code) != "py":
            return False
        text_file = save_text(Path(directory) / "code.py", code)
        if text_file.read_bytes() != code.encode("utf-8"):
            return False
        source = 'Товар,Код\n"Папка, А4",00123\n'
        csv_file = export_table(Path(directory) / "table.csv", source, "csv")
        with csv_file.open(encoding="utf-8-sig", newline="") as stream:
            if list(csv.reader(stream)) != [["Товар", "Код"], ["Папка, А4", "00123"]]:
                return False
        xlsx_file = export_table(Path(directory) / "table.xlsx", source, "xlsx")
        with ZipFile(xlsx_file) as package:
            return package.testzip() is None and b"00123" in package.read("xl/sharedStrings.xml")


def check_credentials():
    """Opt-in check against a disposable service; never reads the app's key."""
    import uuid
    from .settings import Credentials
    credentials = Credentials()
    credentials.service = "ctrlCctrlV.Smoke." + uuid.uuid4().hex
    try:
        credentials.write("synthetic-smoke-value")
        return credentials.read() == "synthetic-smoke-value"
    finally:
        credentials.write("")


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
