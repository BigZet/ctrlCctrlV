"""Native SendInput acceptance in a separate VS Code profile and owned window."""
import argparse
import ctypes as c
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from ctrlcctrlv.app import diagnostic_print
from ctrlcctrlv.core import OperationError
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.typing import type_text
from ctrlcctrlv.windows import WindowsPlatform, keyboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("C:/Program Files/Microsoft VS Code/Code.exe"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/windows-vscode.json"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows is required")
    directory = (Path("artifacts/vscode-input") / uuid.uuid4().hex).resolve()
    profile = directory / "profile"
    user = profile / "User"
    user.mkdir(parents=True)
    configuration = {
        "workbench.startupEditor": "none", "window.restoreWindows": "none",
        "window.confirmBeforeClose": "never", "security.workspace.trust.enabled": False,
        "telemetry.telemetryLevel": "off", "update.mode": "none",
        "extensions.autoUpdate": False, "extensions.autoCheckUpdates": False,
        "editor.autoClosingBrackets": "never", "editor.autoClosingQuotes": "never",
        "editor.autoSurround": "never", "editor.autoIndent": "none",
        "editor.quickSuggestions": False, "editor.suggestOnTriggerCharacters": False,
        "editor.formatOnSave": False, "editor.formatOnType": False,
        "editor.detectIndentation": False, "editor.insertSpaces": False,
        "files.eol": "\n", "files.autoSave": "off",
    }
    settings_file = user / "settings.json"
    settings_file.write_text(json.dumps(configuration), encoding="utf-8")
    literal = directory / "literal.txt"
    literal.write_text("", encoding="utf-8")
    process = subprocess.Popen([str(args.exe), "--user-data-dir", str(profile), "--extensions-dir",
                                str(directory / "extensions"), "--disable-extensions", "--disable-gpu",
                                "--new-window", str(literal)])
    platform = WindowsPlatform()
    win = platform.user32
    callback_type = c.WINFUNCTYPE(c.c_int, c.c_void_p, c.c_void_p)
    win.EnumWindows.argtypes = [callback_type, c.c_void_p]
    win.GetWindowTextW.argtypes = [c.c_void_p, c.c_wchar_p, c.c_int]
    win.IsWindowVisible.argtypes = [c.c_void_p]
    win.IsWindowVisible.restype = c.c_int
    win.SetForegroundWindow.argtypes = [c.c_void_p]
    win.SetForegroundWindow.restype = c.c_int
    win.AttachThreadInput.argtypes = [c.c_uint32, c.c_uint32, c.c_int]
    win.AttachThreadInput.restype = c.c_int
    kernel = c.WinDLL("kernel32")
    kernel.GetCurrentThreadId.restype = c.c_uint32
    report = {"schema": 1, "cases": {}}
    handle = None
    try:
        windows = []
        def inspect(window, _):
            pid = c.c_uint32()
            win.GetWindowThreadProcessId(window, c.byref(pid))
            if pid.value == process.pid and win.IsWindowVisible(window):
                title = c.create_unicode_buffer(512)
                win.GetWindowTextW(window, title, len(title))
                if "literal.txt" in title.value:
                    windows.append(int(window))
            return 1
        callback = callback_type(inspect)
        deadline = time.monotonic() + 20
        while not windows and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Owned VS Code process exited during startup")
            win.EnumWindows(callback, None)
            time.sleep(0.1)
        if not windows:
            raise RuntimeError("Owned editor window not found")
        handle = windows[0]
        thread = kernel.GetCurrentThreadId()
        foreground = win.GetForegroundWindow()
        threads = {win.GetWindowThreadProcessId(handle, None),
                   win.GetWindowThreadProcessId(foreground, None) if foreground else 0} - {thread, 0}
        attached = [other for other in threads if win.AttachThreadInput(thread, other, True)]
        try:
            win.SetForegroundWindow(handle)
        finally:
            for other in reversed(attached):
                win.AttachThreadInput(thread, other, False)
        time.sleep(3)
        target = (handle, process.pid)
        def send(key, modifier=None):
            if platform.active_target() != target:
                raise RuntimeError("Owned editor lost focus; input stopped")
            events = [keyboard(modifier)] if modifier else []
            events += [keyboard(key), keyboard(key, flags=2)]
            if modifier:
                events.append(keyboard(modifier, flags=2))
            platform._send(events)
            time.sleep(0.15)
        def write_case(name, file, text, autoindent):
            report["active_case"] = name
            report["typed_characters"] = 0
            send(ord('1'), 0x11)  # Focus editor group 1.
            send(ord('A'), 0x11)
            send(0x2E)
            type_text(platform, text, Settings(character_delay=0.008, newline_delay=0.15,
                                               autoindent=autoindent), threading.Event(),
                      lambda done, total: report.update(typed_characters=done),
                      expected_target=target)
            send(ord('S'), 0x11)
            time.sleep(0.5)
            actual = file.read_text(encoding="utf-8")
            report["cases"][name] = {"exact": actual == text, "expected_characters": len(text),
                                     "actual_characters": len(actual), "autoindent_compensation": autoindent}
            if actual != text:
                report["cases"][name]["only_extra_final_indent"] = actual.rstrip(' \t') == text
        write_case("literal_unicode_tab_blank_lines", literal,
                   'English / Русский / 🙂\n    spaces\tTab\n\n[] {} () _ | : ; , . "quotes"\n', False)
        configuration["editor.autoIndent"] = "full"
        settings_file.write_text(json.dumps(configuration), encoding="utf-8")
        python = directory / "autoindent.py"
        python.write_text("", encoding="utf-8")
        time.sleep(1)
        send(ord('P'), 0x11)
        type_text(platform, str(python), Settings(character_delay=0.003), threading.Event(), lambda *a: None,
                  expected_target=target)
        send(0x0D)
        time.sleep(1)
        sample = 'def greeting(name):\n    if name:\n        print("Привет", name)\n    return name'
        write_case("python_autoindent", python, sample, True)
        write_case("python_autoindent_final_newline", python, sample + '\n\n', True)
        send(0x73, 0x12)  # Alt+F4, only in our disposable window.
        report["completed"] = True
    except Exception as error:
        active = platform.active_target()
        report["active_window_owned"] = bool(active and active[1] == process.pid)
        if active and active[1] == process.pid:
            title = c.create_unicode_buffer(512)
            win.GetWindowTextW(active[0], title, len(title))
            report["owned_active_title"] = title.value
        report["error_type"] = type(error).__name__
        if isinstance(error, (RuntimeError, OperationError)):
            report["error"] = str(error)
        report["completed"] = False
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        report["owned_process_stopped"] = process.poll() is not None
        report["passed"] = report["completed"] and all(case["exact"] for case in report["cases"].values())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        diagnostic_print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
