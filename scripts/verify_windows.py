"""Native input acceptance against a disposable, owned Qt editor only."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.typing import type_text
from ctrlcctrlv.windows import WindowsPlatform, keyboard, physical_memory


def main():
    parser = argparse.ArgumentParser(description="Проверка ввода в отдельном тестовом окне Windows")
    parser.add_argument("--output", type=Path, default=Path("artifacts/windows-native.json"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Требуется Windows")
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        parser.error("Для проверки нужен обычный оконный сеанс Qt")
    app = QApplication([])
    editor = QPlainTextEdit()
    editor.setWindowTitle("ctrlCctrlV · Проверка ввода (синтетический текст)")
    editor.resize(680, 360)
    editor.setTabChangesFocus(False)
    editor.show()
    editor.activateWindow()
    second_editor = QPlainTextEdit()
    second_editor.setWindowTitle("ctrlCctrlV · Второе тестовое окно (проверка фокуса)")
    second_editor.resize(500, 240)
    adapter = WindowsPlatform()
    import ctypes as c
    adapter.user32.SetForegroundWindow.argtypes = [c.c_void_p]
    adapter.user32.SetForegroundWindow.restype = c.c_int
    foreground = adapter.user32.GetForegroundWindow()
    foreground_thread = adapter.user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    kernel = c.WinDLL("kernel32")
    kernel.GetCurrentThreadId.restype = c.c_uint32
    thread = kernel.GetCurrentThreadId()
    adapter.user32.AttachThreadInput.argtypes = [c.c_uint32, c.c_uint32, c.c_int]
    adapter.user32.AttachThreadInput.restype = c.c_int
    attached = foreground_thread and adapter.user32.AttachThreadInput(thread, foreground_thread, True)
    try:
        adapter.user32.SetForegroundWindow(int(editor.winId()))
        editor.setFocus()
    finally:
        if attached:
            adapter.user32.AttachThreadInput(thread, foreground_thread, False)
    cancel = threading.Event()
    sample = 'English / Русский / 🙂\n    spaces\tTab\n\n[] {} () _ | : ; , . "quotes"\n'
    result = {"schema": 1, "physical_memory_bytes": physical_memory(), "checks": {}}
    completed = []
    workers = []
    actions = []
    focus_requests = []
    stage = "wait"
    capture_index = 0
    capture_at = 0.0
    deadline = time.monotonic() + 15

    def owned():
        if adapter.active_pid() != os.getpid():
            raise Cancelled("Тестовое окно потеряло фокус; ввод остановлен")

    def start(text, stop_after=None, focus_after=None):
        target = adapter.active_target()
        def run():
            try:
                def progress(done, total):
                    if stop_after and done >= stop_after:
                        cancel.set()
                    if focus_after and done == focus_after:
                        focus_requests.append(True)
                type_text(adapter, text, Settings(character_delay=0.2 if focus_after else 0.004, newline_delay=0.03),
                          cancel, progress, expected_target=target)
                completed.append("complete")
            except Cancelled:
                completed.append("cancelled")
            except Exception as error:
                completed.append(type(error).__name__)
        worker = threading.Thread(target=run, daemon=True)
        workers.append(worker)
        worker.start()

    def finish(error=None):
        cancel.set()
        adapter.stop_hotkeys()
        result["passed"] = error is None and all(result["checks"].values())
        if error:
            result["error"] = str(error)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        app.exit(0 if result["passed"] else 1)
        print(json.dumps(result), flush=True)

    def tick():
        nonlocal stage, deadline, capture_index, capture_at
        try:
            if time.monotonic() > deadline:
                raise OperationError("Тест не завершился за отведённое время")
            if stage == "wait":
                if adapter.active_pid() != os.getpid():
                    return
                result["screens"] = [{"width": s.geometry().width(), "height": s.geometry().height(),
                                      "dpi_scale": s.devicePixelRatio()} for s in app.screens()]
                stage = "typing"
                start(sample)
            elif stage == "typing" and completed:
                owned()
                result["checks"]["unicode_multiline_tab"] = completed.pop() == "complete" and editor.toPlainText() == sample
                editor.clear()
                stage = "cancel"
                start("abcdef", stop_after=1)
            elif stage == "cancel" and completed:
                owned()
                result["checks"]["cancel_after_first_character"] = completed.pop() == "cancelled" and editor.toPlainText() == "a"
                cancel.clear()
                editor.clear()
                stage = "focus"
                start("abcdef", focus_after=1)
            elif stage == "focus":
                owned()
                if focus_requests:
                    focus_requests.clear()
                    second_editor.show()
                    second_editor.activateWindow()
                    adapter.user32.SetForegroundWindow(int(second_editor.winId()))
                    second_editor.setFocus()
                    if adapter.active_target()[0] != int(second_editor.winId()):
                        raise OperationError("Не удалось активировать второе тестовое окно")
                if not completed:
                    return
                result["checks"]["stop_on_same_process_window_change"] = (
                    completed.pop() == "cancelled" and editor.toPlainText() == "a"
                    and second_editor.toPlainText() == "")
                second_editor.hide()
                editor.activateWindow()
                adapter.user32.SetForegroundWindow(int(editor.winId()))
                editor.setFocus()
                registered = adapter.start_hotkeys(actions.append, {"probe": "ctrl+alt+shift+9"})
                result["checks"]["hotkey_registered"] = registered
                if not registered:
                    raise OperationError(adapter.last_hotkey_error)
                # Deliver an actual WM_HOTKEY through Qt's native event filter.
                owned()
                adapter._send([keyboard(0x11), keyboard(0x12), keyboard(0x10), keyboard(ord('9')),
                               keyboard(ord('9'), flags=2), keyboard(0x10, flags=2),
                               keyboard(0x12, flags=2), keyboard(0x11, flags=2)])
                stage = "hotkey"
                deadline = time.monotonic() + 3
            elif stage == "hotkey" and actions:
                result["checks"]["native_hotkey_dispatch"] = actions == ["probe"]
                editor.setPlainText(sample)
                stage = "move"
                deadline = time.monotonic() + 8
            elif stage == "move":
                screen = app.screens()[capture_index]
                bounds = screen.availableGeometry()
                editor.setGeometry(bounds.x() + 40, bounds.y() + 40, 640, 360)
                capture_at = time.monotonic() + 0.6
                stage = "capture"
            elif stage == "capture" and time.monotonic() >= capture_at:
                screen = app.screens()[capture_index]
                # Capture only the owned editor, never an entire desktop.
                pixmap = screen.grabWindow(int(editor.winId()))
                logical = pixmap.deviceIndependentSize()
                result["checks"][f"native_capture_monitor_{capture_index + 1}"] = (
                    not pixmap.isNull() and abs(logical.width() - editor.width()) <= 2
                    and abs(logical.height() - editor.height()) <= 2)
                result.setdefault("capture_dpi_scales", []).append(pixmap.devicePixelRatio())
                capture_index += 1
                if capture_index < len(app.screens()):
                    stage = "move"
                else:
                    finish()
                    timer.stop()
        except Exception as error:
            timer.stop()
            finish(error)

    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(tick)
    timer.start()
    code = app.exec()
    cancel.set()
    for worker in workers:
        worker.join(timeout=1)
    editor.close()
    second_editor.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
