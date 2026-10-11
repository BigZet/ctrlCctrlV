"""One synthetic capture/OCR in the shipped Windows EXE, using owned windows.

The script launches an isolated instance, checks the selection before releasing
the mouse, and reads no titles from other applications. One paid request at most.
"""
import argparse
import ctypes as c
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import httpx
from PySide6.QtCore import QPoint, QMimeData, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from ctrlcctrlv.app import diagnostic_print
from ctrlcctrlv.settings import Credentials, Settings
from ctrlcctrlv.windows import INPUT, MOUSEINPUT, POINT, WindowsPlatform, keyboard
from scripts.verify_openrouter import MODEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("artifacts/builds/windows/dist/ctrlCctrlV/ctrlCctrlV.exe"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/frozen-openrouter.json"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows is required")
    os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    clipboard = app.clipboard()
    previous = QMimeData()
    mime = clipboard.mimeData()
    if mime:
        for name in mime.formats():
            previous.setData(name, mime.data(name))
    platform = WindowsPlatform()
    user = platform.user32
    user.GetClipboardSequenceNumber.restype = c.c_uint32
    user.SetForegroundWindow.argtypes = [c.c_void_p]
    user.SetForegroundWindow.restype = c.c_int
    user.IsWindowVisible.argtypes = [c.c_void_p]
    user.IsWindowVisible.restype = c.c_int
    user.SetCursorPos.argtypes = [c.c_int32, c.c_int32]
    user.SetCursorPos.restype = c.c_int
    user.WindowFromPoint.argtypes = [POINT]
    user.WindowFromPoint.restype = c.c_void_p
    user.AttachThreadInput.argtypes = [c.c_uint32, c.c_uint32, c.c_int]
    user.AttachThreadInput.restype = c.c_int
    user.GetWindowTextW.argtypes = [c.c_void_p, c.c_wchar_p, c.c_int]
    callback_type = c.WINFUNCTYPE(c.c_int, c.c_void_p, c.c_void_p)
    user.EnumWindows.argtypes = [callback_type, c.c_void_p]
    kernel = c.WinDLL("kernel32")
    kernel.GetCurrentThreadId.restype = c.c_uint32
    process = None
    window = QLabel(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    report = {"schema": 1, "application_build": "frozen_exe", "model": MODEL,
              "fixture": "python-light-20", "generation_limit": 1,
              "max_output_tokens": 512, "checks": {}, "generation_cost_usd": None}
    checks = report["checks"]
    owned_sequence = None
    left_button_owned = False
    before_usage = None
    client = httpx.Client(timeout=10, follow_redirects=False)

    def wait(condition, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                return
            if process and process.poll() is not None:
                raise RuntimeError("Owned frozen application exited")
            time.sleep(0.02)
        raise RuntimeError("Owned frozen diagnostic timed out")

    def owned_windows(title):
        windows = []
        def inspect(handle, _):
            pid = c.c_uint32()
            user.GetWindowThreadProcessId(handle, c.byref(pid))
            if process and pid.value == process.pid and user.IsWindowVisible(handle):
                text = c.create_unicode_buffer(128)
                user.GetWindowTextW(handle, text, len(text))
                if text.value == title:
                    windows.append(int(handle))
            return 1
        user.EnumWindows(callback_type(inspect), None)
        return windows

    def activate(handle):
        current = kernel.GetCurrentThreadId()
        foreground = user.GetForegroundWindow()
        threads = {user.GetWindowThreadProcessId(handle, None),
                   user.GetWindowThreadProcessId(foreground, None) if foreground else 0} - {0, current}
        attached = [thread for thread in threads if user.AttachThreadInput(current, thread, True)]
        try:
            user.SetForegroundWindow(handle)
        finally:
            for thread in reversed(attached):
                user.AttachThreadInput(current, thread, False)
        wait(lambda: platform.active_target()[0] == handle if platform.active_target() else False, 2)

    def move_owned_pointer(handle, point):
        if platform.active_target() != (handle, process.pid):
            raise RuntimeError("Owned selection lost focus; input stopped")
        if not user.SetCursorPos(point.x(), point.y()):
            raise RuntimeError("Cannot position the pointer in our selection")
        target = user.WindowFromPoint(POINT(point.x(), point.y()))
        pid = c.c_uint32()
        user.GetWindowThreadProcessId(target, c.byref(pid))
        if pid.value != process.pid:
            raise RuntimeError("Pointer does not target our selection; input stopped")

    def mouse_button(flags):
        event = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))
        if user.SendInput(1, c.byref(event), c.sizeof(INPUT)) != 1:
            raise RuntimeError("Windows rejected the diagnostic mouse event")

    try:
        key = Credentials().read()
        if not key:
            raise RuntimeError("OpenRouter key is not configured")
        authorization = {"Authorization": "Bearer " + key}
        response = client.get("https://openrouter.ai/api/v1/models")
        response.raise_for_status()
        model = next(m for m in response.json()["data"] if m["id"] == MODEL)
        if (not 0 <= Decimal(model["pricing"]["prompt"]) <= Decimal("0.0000005") or
                not 0 <= Decimal(model["pricing"]["completion"]) <= Decimal("0.000001")):
            raise RuntimeError("Model no longer satisfies the inexpensive policy")
        response = client.get("https://openrouter.ai/api/v1/key", headers=authorization)
        response.raise_for_status()
        before_usage = Decimal(str(response.json()["data"]["usage"]))
        reference = QImage("artifacts/fixtures/python-light-20.png")
        expected = Path("artifacts/fixtures/python-light-20.txt").read_text(encoding="utf-8")
        if reference.size().width() != 1000 or reference.size().height() != 252:
            raise RuntimeError("Unexpected synthetic fixture")
        screen = next((s for s in app.screens() if s.devicePixelRatio() == 1 and s.geometry().width() > 1100), None)
        if not screen:
            raise RuntimeError("A native 100% display is required")
        directory = (Path("artifacts/frozen-openrouter") / uuid.uuid4().hex).resolve()
        Settings(provider="openrouter", remote_model=MODEL, review_remote_model=MODEL,
                 max_tokens=512, show_preview=True, capture_hotkey="ctrl+alt+shift+4",
                 typing_hotkey="ctrl+alt+shift+v", cancel_hotkey="ctrl+alt+shift+esc").save(directory)
        report["exe_sha256"] = hashlib.sha256(args.exe.read_bytes()).hexdigest()
        report["stage"] = "startup"
        process = subprocess.Popen([str(args.exe.resolve()), "--data-dir", str(directory)], env=os.environ.copy())
        wait(lambda: bool(owned_windows("ctrlCctrlV")), 10)
        window.setPixmap(QPixmap.fromImage(reference))
        window.setFixedSize(reference.size())
        window.move(screen.geometry().topLeft() + QPoint(60, 120))
        window.show()
        window.raise_()
        activate(int(window.winId()))
        time.sleep(0.3)
        app.processEvents()
        native = screen.grabWindow(int(window.winId())).toImage()
        checks["owned_fixture_native_pixels_match"] = (native.convertToFormat(QImage.Format.Format_RGB32) ==
                                                       reference.convertToFormat(QImage.Format.Format_RGB32))
        if not checks["owned_fixture_native_pixels_match"]:
            raise RuntimeError("Fixture is obscured; capture not started")
        if platform.active_target() != (int(window.winId()), os.getpid()):
            raise RuntimeError("Owned fixture lost focus; hotkey not sent")
        events = [keyboard(key) for key in (0x11, 0x12, 0x10)]
        events += [keyboard(ord('4')), keyboard(ord('4'), flags=2)]
        events += [keyboard(key, flags=2) for key in (0x10, 0x12, 0x11)]
        report["stage"] = "capture_hotkey"
        platform._send(events)
        wait(lambda: bool(owned_windows("ctrlCctrlV · Выделение области")), 5)
        overlays = owned_windows("ctrlCctrlV · Выделение области")
        class RECT(c.Structure):
            _fields_ = [(name, c.c_int32) for name in ("left", "top", "right", "bottom")]
        user.GetWindowRect.argtypes = [c.c_void_p, c.POINTER(RECT)]
        handles = []
        for handle in overlays:
            rectangle = RECT()
            user.GetWindowRect(handle, c.byref(rectangle))
            if (rectangle.left, rectangle.top) == (screen.geometry().x(), screen.geometry().y()):
                handles.append(handle)
        if len(handles) != 1:
            raise RuntimeError("Owned selection display is ambiguous")
        handle = handles[0]
        activate(handle)
        start = window.mapToGlobal(QPoint(0, 0)) - screen.geometry().topLeft()
        end = start + QPoint(reference.width() - 1, reference.height() - 1)
        report["stage"] = "selection"
        move_owned_pointer(handle, start + screen.geometry().topLeft())
        mouse_button(2)
        left_button_owned = True
        time.sleep(0.1)
        move_owned_pointer(handle, end + screen.geometry().topLeft())
        time.sleep(0.2)
        interior = screen.grabWindow(handle, start.x() + 4, start.y() + 4,
                                    reference.width() - 8, reference.height() - 8).toImage()
        checks["selection_contains_only_owned_fixture"] = (
            interior.convertToFormat(QImage.Format.Format_RGB32) ==
            reference.copy(4, 4, reference.width() - 8, reference.height() - 8).convertToFormat(QImage.Format.Format_RGB32))
        if not checks["selection_contains_only_owned_fixture"]:
            raise RuntimeError("Selection pixels differ; release/upload cancelled")
        initial_sequence = user.GetClipboardSequenceNumber()
        started = time.monotonic()
        report["stage"] = "ocr"
        move_owned_pointer(handle, end + screen.geometry().topLeft())
        mouse_button(4)
        left_button_owned = False
        wait(lambda: user.GetClipboardSequenceNumber() != initial_sequence and
                    bool(owned_windows("ctrlCctrlV · Результат")), 30)
        elapsed = time.monotonic() - started
        text = clipboard.text()
        matches = text.rstrip("\n") == expected.rstrip("\n")
        checks["system_clipboard_matches_fixture_ignoring_final_newline"] = matches
        checks["frozen_preview_visible"] = bool(owned_windows("ctrlCctrlV · Результат"))
        if matches:
            owned_sequence = user.GetClipboardSequenceNumber()
        report["ocr"] = {"elapsed_upper_bound": elapsed, "passed_4s": elapsed <= 4,
                         "output_characters": len(text), "expected_characters": len(expected),
                         "exact": text == expected, "exact_ignoring_final_newline": matches}
        response = client.get("https://openrouter.ai/api/v1/key", headers=authorization)
        response.raise_for_status()
        report["observed_key_usage_delta_usd"] = float(Decimal(str(response.json()["data"]["usage"])) - before_usage)
    except Exception as error:
        report["error_type"] = type(error).__name__
        if isinstance(error, RuntimeError):
            report["error"] = str(error)
        checks["operation_succeeded"] = False
        if process and process.poll() is None:
            report["visible_owned_windows"] = {
                "settings": bool(owned_windows("ctrlCctrlV")),
                "selection": bool(owned_windows("ctrlCctrlV · Выделение области")),
                "preview": bool(owned_windows("ctrlCctrlV · Результат")),
            }
    finally:
        if left_button_owned:
            try:
                mouse_button(4)
            except RuntimeError:
                checks["mouse_button_released"] = False
        if owned_sequence is not None and user.GetClipboardSequenceNumber() == owned_sequence:
            clipboard.setMimeData(previous)
            report["clipboard_restored"] = True
        else:
            report["clipboard_restored"] = False
        window.close()
        if process:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            checks["owned_frozen_process_stopped"] = process.poll() is not None
        client.close()
        report["passed"] = bool(checks) and all(checks.values())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        diagnostic_print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
