"""Baseline measurements of the Python/PySide6 ctrlCctrlV before the Rust rewrite.

Run from the repository root with the project's virtualenv:

    python scripts/benchmark_baseline.py all --output baseline.json

Subcommands: startup, capture, ocr, typing, size, all. Nothing here types into
other windows, touches the real clipboard, settings, history or keyring unless
an explicit --native-* flag is given. Each measurement uses its own temporary
data directory.
"""
import argparse
import base64
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# A typical clipboard: 40 lines of indented Python, ~1.3k characters.
SAMPLE_CLIPBOARD = '''import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Invoice:
    number: str
    customer: str
    total: float
    paid: bool = False


def load_invoices(path: Path) -> list[Invoice]:
    """Read invoices from a JSON file, skipping broken rows."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for row in rows:
        try:
            result.append(Invoice(row["number"], row["customer"],
                                  float(row["total"]), bool(row.get("paid"))))
        except (KeyError, ValueError):
            continue
    return result


def overdue(invoices, limit=1000.0):
    for invoice in invoices:
        if not invoice.paid and invoice.total > limit:
            yield invoice


if __name__ == "__main__":
    items = load_invoices(Path("invoices.json"))
    late = list(overdue(items))
    print(f"Всего: {len(items)}, просрочено: {len(late)}")
    for invoice in late:
        print(f"  {invoice.number:>8}  {invoice.customer:<20} {invoice.total:10.2f}")
    total = sum(invoice.total for invoice in late)
    print(f"Сумма просроченных: {total:.2f}")
'''

# (name, logical width, logical height) of a selection on a 1x or 2x screen.
SELECTIONS = (("small 400x200", 400, 200), ("medium 900x600", 900, 600), ("large 1600x1000", 1600, 1000))
SCREENS = (("1920x1080 @1x", 1920, 1080, 1), ("1512x982 @2x (MacBook 14)", 1512, 982, 2))


def summary(values):
    values = sorted(values)
    p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
    return {"n": len(values), "median_ms": round(statistics.median(values) * 1000, 2),
            "min_ms": round(values[0] * 1000, 2), "p95_ms": round(p95 * 1000, 2)}


def rss_bytes(pid=None):
    """Resident set size of a process, without requiring psutil."""
    try:
        import psutil
        info = psutil.Process(pid or os.getpid()).memory_full_info()
        return {"rss": info.rss, "uss": getattr(info, "uss", None)}
    except ImportError:
        pass
    if sys.platform.startswith("linux"):
        status = Path(f"/proc/{pid or 'self'}/status").read_text()
        values = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in status.splitlines()
                  if line.startswith(("VmRSS", "RssAnon"))}
        return {"rss": values.get("VmRSS"), "uss": values.get("RssAnon")}
    if pid is None:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"rss_peak": peak if sys.platform == "darwin" else peak * 1024}
    return {}


def environment():
    from PySide6 import __version__ as pyside
    import ctrlcctrlv
    return {"python": sys.version.split()[0], "pyside6": pyside, "platform": platform.platform(),
            "machine": platform.machine(), "processor": platform.processor() or None,
            "cpus": os.cpu_count(), "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM"),
            "ctrlcctrlv": getattr(ctrlcctrlv, "__version__", None)}


# ---------------------------------------------------------------- startup

def child_startup(data_dir, native):
    """Runs in a fresh interpreter; prints one JSON line when the window is up."""
    started = time.perf_counter()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from ctrlcctrlv.app import Controller
    imported = time.perf_counter()
    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    controller = Controller(app, Path(data_dir), native=native)
    controller.show_startup()
    constructed = time.perf_counter()

    def ready():
        report = {"import_s": imported - started, "construct_s": constructed - imported,
                  "first_event_loop_s": time.perf_counter() - started, "memory_ready": rss_bytes()}
        # Settle: background workers (catalog, diagnostics) keep allocating briefly.
        QTimer.singleShot(3000, lambda: (report.update(memory_after_3s=rss_bytes()),
                                         print("BASELINE " + json.dumps(report), flush=True),
                                         app.exit(0)))
    QTimer.singleShot(0, ready)
    app.exec()
    os._exit(0)  # skip slow interpreter teardown; it is not part of startup


def drop_caches():
    try:
        subprocess.run(["sync"], check=False)
        Path("/proc/sys/vm/drop_caches").write_text("3\n")
        return True
    except OSError:
        return False


def bench_startup(args):
    from ctrlcctrlv.settings import Settings
    runs = []
    cold_dropped = False
    for index in range(args.runs):
        with tempfile.TemporaryDirectory(prefix="ctrlc-baseline-") as directory:
            Settings().save(Path(directory))  # skip the first-run wizard: measure a normal launch
            if index == 0 and args.drop_caches:
                cold_dropped = drop_caches()
            launched = time.perf_counter()
            process = subprocess.run([sys.executable, __file__, "_child-startup", directory]
                                     + (["--native"] if args.native else []),
                                     capture_output=True, text=True, timeout=120, env=os.environ)
            line = next((l for l in process.stdout.splitlines() if l.startswith("BASELINE ")), None)
            if line is None:
                raise RuntimeError("startup child failed:\n" + process.stdout + process.stderr)
            report = json.loads(line[len("BASELINE "):])
            report["process_wall_s"] = time.perf_counter() - launched  # includes 3 s settle + exit
            runs.append(report)
    warm = runs[1:] or runs
    return {
        "note": "venv interpreter, not the PyInstaller bundle; ready = first event-loop turn after the "
                "main window is shown with existing settings",
        "first_run_cold_cache": cold_dropped,
        "first_run": {k: round(runs[0][k], 3) for k in ("import_s", "construct_s", "first_event_loop_s")},
        "warm_median_s": {k: round(statistics.median(r[k] for r in warm), 3)
                          for k in ("import_s", "construct_s", "first_event_loop_s")},
        "memory_ready_mib": round(statistics.median(r["memory_ready"].get("rss") or r["memory_ready"].get("rss_peak")
                                                    for r in runs) / 2**20, 1),
        "memory_after_3s_mib": round(statistics.median(r["memory_after_3s"].get("rss") or r["memory_after_3s"].get("rss_peak")
                                                       for r in runs) / 2**20, 1),
        "runs": runs,
    }


def bench_frozen(args):
    """Installed build: wall time of `--smoke` (opens UI, runs self-checks, exits) and peak RSS."""
    try:
        import psutil
    except ImportError:
        psutil = None
    times, peaks = [], []
    for _ in range(args.runs):
        with tempfile.TemporaryDirectory(prefix="ctrlc-baseline-") as directory:
            launched = time.perf_counter()
            process = subprocess.Popen([args.frozen, "--smoke", "--data-dir", directory],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            peak = 0
            while process.poll() is None:
                if psutil:
                    try:
                        tree = [psutil.Process(process.pid)] + psutil.Process(process.pid).children(recursive=True)
                        peak = max(peak, sum(p.memory_info().rss for p in tree))
                    except psutil.Error:
                        pass
                time.sleep(0.05)
            times.append(time.perf_counter() - launched)
            peaks.append(peak)
    return {"note": "--smoke includes formatter/export/transport self-checks, so this is an upper bound",
            "smoke_wall_s": summary(times), "peak_rss_mib": round(max(peaks) / 2**20, 1) if psutil else None}


# ---------------------------------------------------------------- capture

def synthetic_screen(width, height, dpr):
    """An IDE-like screen: light background, rows of monospace text, a side panel."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    pixmap = QPixmap(width * dpr, height * dpr)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(QColor("#fafafa"))
    painter = QPainter(pixmap)
    painter.fillRect(0, 0, 260, height, QColor("#eceff4"))
    font = QFont("monospace")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(14)
    painter.setFont(font)
    lines = SAMPLE_CLIPBOARD.splitlines()
    colors = [QColor("#24292e"), QColor("#005cc5"), QColor("#d73a49"), QColor("#22863a")]
    y, n = 30, 0
    while y < height:
        painter.setPen(QColor("#8a8a8a"))
        painter.drawText(270, y, f"{n + 1:4}")
        painter.setPen(colors[n % len(colors)])
        painter.drawText(320, y, lines[n % len(lines)])
        painter.setPen(QColor("#4c566a"))
        painter.drawText(12, y, f"module_{n % 17}.py")
        y += 20
        n += 1
    painter.end()
    return pixmap


def png_bytes(image):
    """Exactly the app's encoder path (app.py, recognize task)."""
    from PySide6.QtCore import QBuffer, QIODevice
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("PNG encoding failed")
    return bytes(buffer.data())


def bench_capture(args, app):
    from PySide6.QtCore import QRect
    from ctrlcctrlv.capture_region import physical_rect
    results = []
    for screen_name, width, height, dpr in SCREENS:
        screen = synthetic_screen(width, height, dpr)
        for name, w, h in SELECTIONS:
            w, h = min(w, width), min(h, height)
            crop, encode, wrap, sizes = [], [], [], []
            for _ in range(args.repeats):
                t0 = time.perf_counter()
                area = QRect(min(100, width - w), min(50, height - h), w, h)
                image = screen.copy(physical_rect(area, dpr)).toImage()
                image.setDevicePixelRatio(1)
                t1 = time.perf_counter()
                png = png_bytes(image)
                t2 = time.perf_counter()
                # ocr.py: base64 data URL inside a JSON body (httpx serialises with json.dumps)
                body = json.dumps({"messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,"
                                                        + base64.b64encode(png).decode("ascii")}}]}]}).encode()
                t3 = time.perf_counter()
                crop.append(t1 - t0); encode.append(t2 - t1); wrap.append(t3 - t2); sizes.append(len(png))
            results.append({"screen": screen_name, "selection": name,
                            "pixels": f"{w * dpr}x{h * dpr}", "png_kib": round(sizes[0] / 1024, 1),
                            "request_body_kib": round(len(body) / 1024, 1),
                            "crop_to_qimage": summary(crop), "png_encode": summary(encode),
                            "base64_json": summary(wrap),
                            "total_median_ms": round(statistics.median(a + b + c for a, b, c in zip(crop, encode, wrap)) * 1000, 2)})
    grab = None
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        # Real desktop: app.py grabs every screen in full before showing overlays.
        times = []
        for _ in range(args.repeats):
            t0 = time.perf_counter()
            shots = [s.grabWindow(0) for s in app.screens()]
            times.append(time.perf_counter() - t0)
        grab = {"screens": [f"{s.size().width()}x{s.size().height()} @{s.devicePixelRatio():g}x" for s in app.screens()],
                "grab_all_screens": summary(times), "null_pixmaps": sum(p.isNull() for p in shots)}
    return {"note": "synthetic IDE-like screen; grabWindow is only timed on a real desktop",
            "selections": results, "grab": grab}


# ---------------------------------------------------------------- OCR round trip

def fake_llama_server(answer, delay):
    body = json.dumps({"choices": [{"finish_reason": "stop",
                                    "message": {"role": "assistant", "content": answer}}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            self.rfile.read(length)
            self.server.received.append(time.perf_counter())
            if delay:
                time.sleep(delay)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.received = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def bench_ocr(args, app):
    from ctrlcctrlv.core import Operation
    from ctrlcctrlv.ocr import OCRProvider
    from PySide6.QtCore import QRect
    screen = synthetic_screen(1512, 982, 2)
    png = png_bytes(screen.copy(QRect(200, 100, 1800, 1200)).toImage())  # medium @2x
    server = fake_llama_server(SAMPLE_CLIPBOARD, 0)
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
    provider = OCRProvider(endpoint, "lightonocr-3-0.8b", "session-key", 4096, True, "transcription")
    for _ in range(3):
        provider.recognize_result(png, Operation())  # warm imports and the socket stack
    totals, to_server = [], []
    for _ in range(args.repeats):
        server.received.clear()
        t0 = time.perf_counter()
        result = provider.recognize_result(png, Operation())
        totals.append(time.perf_counter() - t0)
        to_server.append(server.received[0] - t0)
    assert result.text == SAMPLE_CLIPBOARD
    # Transport floor: one persistent keep-alive connection, prebuilt body, no asyncio.
    import http.client
    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}]}).encode()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    connection.connect()
    connection.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # avoid delayed-ACK stalls
    floor = []
    for index in range(args.repeats + 3):
        t0 = time.perf_counter()
        connection.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
        connection.getresponse().read()
        if index >= 3:
            floor.append(time.perf_counter() - t0)
    # The per-request httpx.AsyncClient builds a TLS context (CA bundle load) even for http://.
    import asyncio
    import httpx

    async def create_client():
        t0 = time.perf_counter()
        async with httpx.AsyncClient(trust_env=False):
            pass
        return time.perf_counter() - t0
    client_create = [asyncio.run(create_client()) for _ in range(args.repeats)]
    server.shutdown()
    report = {"note": "fake llama-server answers instantly; the time is pure client overhead "
                      "(base64+JSON, new asyncio loop and httpx client per request, 50 ms cancel polling)",
              "png_kib": round(len(png) / 1024, 1),
              "app_round_trip": summary(totals), "app_until_server_has_body": summary(to_server),
              "persistent_http_client_floor": summary(floor),
              "httpx_async_client_create": summary(client_create)}
    if args.endpoint:
        # Optional: a real llama-server (model time included). Key from --key.
        real = OCRProvider(args.endpoint, args.model, args.key or "", 4096, True, "transcription")
        times = []
        for _ in range(args.real_repeats):
            t0 = time.perf_counter()
            real.recognize_result(png, Operation())
            times.append(time.perf_counter() - t0)
        report["real_endpoint"] = {"endpoint": args.endpoint, "model": args.model, "round_trip": summary(times)}
    return report


# ---------------------------------------------------------------- typing

class FakePlatform:
    """Same interface as WindowsPlatform/MacPlatform, without any OS calls."""

    def __init__(self):
        self.calls = 0

    def permissions(self):
        return {"accessibility": True}

    def modifiers_pressed(self):
        return False

    def active_pid(self):
        return 4242

    def active_target(self):
        return (4242, 1)

    def character(self, char=None):
        self.calls += 1

    def enter(self):
        self.calls += 1

    def tab(self):
        self.calls += 1

    def select_indentation(self):
        self.calls += 1


class NativeProbe:
    """Real OS queries for checks done per character, but no keystrokes are sent."""

    def __init__(self, native):
        self.native = native
        self.calls = 0

    def permissions(self):
        return {"accessibility": True}

    def modifiers_pressed(self):
        return self.native.modifiers_pressed()

    def active_pid(self):
        return self.native.active_pid()

    def active_target(self):
        from ctrlcctrlv.platforms import active_target
        return active_target(self.native)

    def character(self, char=None):
        self.calls += 1

    enter = tab = select_indentation = character


def run_typing(adapter, settings, text):
    from ctrlcctrlv.typing import type_text
    cancelled = threading.Event()
    t0 = time.perf_counter()
    type_text(adapter, text, settings, cancelled, lambda *_: None)
    return time.perf_counter() - t0


def bench_typing(args):
    from dataclasses import replace
    from ctrlcctrlv.settings import Settings
    defaults = Settings()
    zero = replace(defaults, character_delay=0, newline_delay=0)
    text = SAMPLE_CLIPBOARD
    chars, newlines = len(text), text.count("\n")
    nominal = chars * defaults.character_delay + newlines * defaults.newline_delay
    overhead = [run_typing(FakePlatform(), zero, text) for _ in range(args.repeats)]
    real_waits = [run_typing(FakePlatform(), defaults, text) for _ in range(args.typing_repeats)]
    report = {"note": "type_text() policy with a no-op platform: the cost of the checks and the "
                      "accuracy of threading.Event.wait() delays, without SendInput/CGEventPost",
              "sample_chars": chars, "sample_lines": newlines,
              "settings": {"character_delay_s": defaults.character_delay, "newline_delay_s": defaults.newline_delay},
              "nominal_delay_sum_s": round(nominal, 3),
              "policy_overhead_zero_delay": summary(overhead),
              "policy_overhead_per_char_us": round(statistics.median(overhead) / chars * 1e6, 2),
              "default_delays_wall_s": round(statistics.median(real_waits), 3),
              "oversleep_vs_nominal_pct": round((statistics.median(real_waits) / nominal - 1) * 100, 1)}
    if args.native_probe:
        from ctrlcctrlv.platforms import create_platform
        native = create_platform()
        if native is None:
            report["native_probe"] = "unsupported platform"
        else:
            probe = [run_typing(NativeProbe(native), zero, text) for _ in range(3)]
            report["native_probe"] = {"note": "real active_target()/modifiers_pressed() per character, no keystrokes",
                                      "wall": summary(probe),
                                      "per_char_us": round(statistics.median(probe) / chars * 1e6, 1)}
    if args.native_type:
        from ctrlcctrlv.platforms import create_platform
        native = create_platform()
        print("Typing the sample into the focused window in 5 s. Focus an EMPTY editor (Notepad/TextEdit).", flush=True)
        time.sleep(5)
        report["native_type"] = {"note": "real keystrokes with default delays into the focused window",
                                 "wall_s": round(run_typing(native, defaults, text), 3)}
    return report


# ---------------------------------------------------------------- sizes

def bench_size(args):
    import urllib.request
    url = "https://api.github.com/repos/BigZet/ctrlCctrlV/releases/tags/" + args.release
    with urllib.request.urlopen(url, timeout=30) as response:
        assets = json.load(response)["assets"]
    report = {"release": args.release,
              "assets_mib": {a["name"]: round(a["size"] / 2**20, 1) for a in assets if not a["name"].endswith((".sha256", ".txt"))}}
    if args.unpacked:
        # Breakdown of an unpacked PyInstaller folder (e.g. the extracted windows zip).
        root = Path(args.unpacked)
        total, groups = 0, {}
        for path in root.rglob("*"):
            if path.is_file():
                size = path.stat().st_size
                total += size
                parts = path.relative_to(root).parts
                key = "/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1]) or "."
                groups[key] = groups.get(key, 0) + size
        report["unpacked_mib"] = round(total / 2**20, 1)
        report["largest_dirs_mib"] = {k: round(v / 2**20, 1) for k, v in sorted(groups.items(), key=lambda kv: -kv[1])[:15]}
    return report


# ---------------------------------------------------------------- main

def main():
    if len(sys.argv) > 2 and sys.argv[1] == "_child-startup":
        return child_startup(sys.argv[2], "--native" in sys.argv)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", choices=("startup", "capture", "ocr", "typing", "size", "all"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runs", type=int, default=7, help="startup launches")
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--typing-repeats", type=int, default=3)
    parser.add_argument("--native", action="store_true", help="startup with native hotkeys/platform (Windows/macOS)")
    parser.add_argument("--drop-caches", action="store_true", help="Linux root only: drop page cache before run 1")
    parser.add_argument("--frozen", help="path to an installed ctrlCctrlV executable for a --smoke timing")
    parser.add_argument("--endpoint", help="real llama-server /v1/chat/completions URL (optional)")
    parser.add_argument("--key", help="session key for --endpoint")
    parser.add_argument("--model", default="lightonocr-3-0.8b")
    parser.add_argument("--real-repeats", type=int, default=5)
    parser.add_argument("--native-probe", action="store_true", help="time real per-char OS checks, no typing")
    parser.add_argument("--native-type", action="store_true", help="REALLY type the sample into the focused window")
    parser.add_argument("--release", default="v0.1.1")
    parser.add_argument("--unpacked", help="unpacked PyInstaller folder for a size breakdown")
    args = parser.parse_args()

    report = {"environment": environment()}
    what = ("startup", "capture", "ocr", "typing", "size") if args.what == "all" else (args.what,)
    if "startup" in what:
        report["startup"] = bench_startup(args)
        if args.frozen:
            report["frozen_startup"] = bench_frozen(args)
    if "capture" in what or "ocr" in what:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([sys.argv[0]])
        if "capture" in what:
            report["capture"] = bench_capture(args, app)
        if "ocr" in what:
            report["ocr"] = bench_ocr(args, app)
    if "typing" in what:
        report["typing"] = bench_typing(args)
    if "size" in what:
        report["size"] = bench_size(args)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
