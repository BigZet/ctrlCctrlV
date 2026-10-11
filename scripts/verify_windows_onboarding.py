"""Verify first-launch keyboard navigation in an owned packaged window only."""
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from ctrlcctrlv.settings import Settings
from ctrlcctrlv.windows import WindowsPlatform, keyboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=Path("artifacts/builds/windows/dist/ctrlCctrlV/ctrlCctrlV.exe"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/windows-onboarding.json"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows is required")
    directory = Path("artifacts/package-onboarding") / uuid.uuid4().hex
    directory.mkdir(parents=True)
    environment = os.environ.copy()
    environment.pop("QT_QPA_PLATFORM", None)
    process = subprocess.Popen([str(args.exe.resolve()), "--data-dir", str(directory.resolve())], env=environment)
    platform = WindowsPlatform()
    user = platform.user32
    callback_type = c.WINFUNCTYPE(c.c_int, c.c_void_p, c.c_void_p)
    user.EnumWindows.argtypes = [callback_type, c.c_void_p]
    user.GetWindowTextW.argtypes = [c.c_void_p, c.c_wchar_p, c.c_int]
    user.SetForegroundWindow.argtypes = [c.c_void_p]
    user.SetForegroundWindow.restype = c.c_int
    user.IsWindowVisible.argtypes = [c.c_void_p]
    user.IsWindowVisible.restype = c.c_int
    result = {"schema": 1, "checks": {}}
    try:
        deadline = time.monotonic() + 10
        windows = []
        def inspect(handle, _):
            pid = c.c_uint32()
            user.GetWindowThreadProcessId(handle, c.byref(pid))
            if pid.value == process.pid:
                title = c.create_unicode_buffer(512)
                user.GetWindowTextW(handle, title, 512)
                if title.value == "ctrlCctrlV · Первый запуск" and user.IsWindowVisible(handle):
                    windows.append(int(handle))
            return 1
        callback = callback_type(inspect)
        while not windows and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Owned application exited before setup")
            user.EnumWindows(callback, None)
            time.sleep(0.1)
        if not windows:
            raise RuntimeError("Setup window was not found")
        handle = windows[0]
        # Transfer foreground permission to our window only. Windows can deny
        # a plain SetForegroundWindow even immediately after process launch.
        kernel = c.WinDLL("kernel32")
        kernel.GetCurrentThreadId.restype = c.c_uint32
        thread = kernel.GetCurrentThreadId()
        foreground = user.GetForegroundWindow()
        foreground_thread = user.GetWindowThreadProcessId(foreground, None) if foreground else 0
        user.AttachThreadInput.argtypes = [c.c_uint32, c.c_uint32, c.c_int]
        user.AttachThreadInput.restype = c.c_int
        target_thread = user.GetWindowThreadProcessId(handle, None)
        attached = []
        for other in {foreground_thread, target_thread} - {0, thread}:
            if user.AttachThreadInput(thread, other, True):
                attached.append(other)
        try:
            user.SetForegroundWindow(handle)
        finally:
            for other in reversed(attached):
                user.AttachThreadInput(thread, other, False)
        time.sleep(0.2)
        sent_keys = []
        def key(code):
            if platform.active_target() != (handle, process.pid):
                raise RuntimeError(f"Owned setup window lost focus after {len(sent_keys)} keys; no further input sent")
            platform._send([keyboard(code), keyboard(code, flags=2)])
            sent_keys.append(code)
            time.sleep(0.2)
        # Qt initially focuses the provider combo. Tab selects acceleration,
        # Down explicitly chooses CPU. Enter advances and completes the wizard.
        key(0x09)
        key(0x28)
        key(0x0D)
        key(0x0D)
        while not (directory / "settings.json").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not (directory / "settings.json").exists():
            raise RuntimeError("Setup choice was not saved")
        settings = Settings.load(directory)
        result["checks"]["local_cpu_choice_saved"] = settings.provider == "local" and settings.acceleration == "cpu"
        result["checks"]["no_download_before_install_command"] = not (directory / "models").exists()
        result["checks"]["no_ocr_before_prepare_command"] = not (directory / "runtime").exists()
    except Exception as error:
        result["error_type"] = type(error).__name__
        if isinstance(error, RuntimeError):
            result["error"] = str(error)
        result["checks"]["completed"] = False
    finally:
        # This first-launch scenario never starts an inference child. Shut down
        # only the disposable application PID; leave the diagnostic data intact.
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        result["checks"]["owned_application_stopped"] = process.poll() is not None
        result["passed"] = all(result["checks"].values())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
