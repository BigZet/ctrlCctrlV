"""A whitelist-only diagnostic snapshot: no credentials, clipboard or user text."""
import json
import os
import platform
import re
import shutil
import subprocess
import sys

from PySide6.QtCore import qVersion

from . import __version__
from .catalog import ENGINE_VERSION, MODELS
from .runtime import check_cancel


def command_number(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=True)
        value = int(result.stdout.strip())
        return value if value >= 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def system_memory():
    if platform.system() == "Darwin":
        return command_number(["sysctl", "-n", "hw.memsize"])
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None


def signature_info():
    packaged = bool(getattr(sys, "frozen", False))
    report = {"packaged": packaged, "signature": "source" if not packaged else "unknown", "code_hash": None}
    if not packaged or platform.system() != "Darwin":
        return report
    try:
        result = subprocess.run(["codesign", "-d", "--verbose=4", sys.executable],
                                capture_output=True, text=True, timeout=2)
        output = result.stdout + "\n" + result.stderr
        match = re.search(r"(?m)^CDHash=([0-9a-fA-F]{40})$", output)
        report["code_hash"] = match[1].lower() if match else None
        if result.returncode == 0:
            report["signature"] = "adhoc" if "Signature=adhoc" in output else "certificate" if "Authority=" in output else "unknown"
        else:
            report["signature"] = "unavailable"
    except (OSError, subprocess.SubprocessError):
        pass
    return report


def runtime_report(runtime):
    process = runtime.process
    code = process.poll() if process else None
    running = process is not None and code is None
    memory = None
    if running and platform.system() == "Darwin":
        value = command_number(["ps", "-o", "rss=", "-p", str(process.pid)])
        memory = value * 1024 if value is not None else None
        code = process.poll()
        running = code is None
        if not running:
            memory = None
    return {"state": "running" if running else "exited" if process else "stopped",
            "ready": bool(runtime.ready and running),
            "model": runtime.model_id if runtime.model_id in MODELS else None,
            "exit_code": code, "resident_memory_bytes": memory}


def collect_diagnostics(directory, runtime, review_runtime, permissions, provider,
                        selected_model, hotkeys_registered, operation, cancel):
    check_cancel(cancel)
    report = {"schema": 1, "application": __version__, "qt": qVersion(),
              "system": platform.system(), "system_version": platform.mac_ver()[0] if platform.system() == "Darwin" else platform.release(),
              "architecture": platform.machine(), "python": platform.python_version(),
              "physical_memory_bytes": system_memory(), "engine_catalog_version": ENGINE_VERSION,
              "build": signature_info(),
              "provider": provider if provider in {"local", "openrouter"} else None,
              "selected_local_model": selected_model if selected_model in MODELS else None,
              "permissions": {key: permissions.get(key) if isinstance(permissions.get(key), bool) else None
                              for key in ("accessibility", "screen", "input_monitoring")},
              "hotkeys_registered": bool(hotkeys_registered),
              "operation": operation if operation in {"idle", "ocr", "review", "typing", "model", "capture", "typing_countdown"} else "unknown"}
    check_cancel(cancel)
    try:
        report["free_disk_bytes"] = shutil.disk_usage(directory).free
    except OSError:
        report["free_disk_bytes"] = None
    report["installed_models"] = {identifier: bool(runtime.installed(identifier)) for identifier in MODELS}
    check_cancel(cancel)
    report["ocr_runtime"] = runtime_report(runtime)
    check_cancel(cancel)
    report["review_runtime"] = runtime_report(review_runtime)
    check_cancel(cancel)
    return json.dumps(report, ensure_ascii=False, indent=2)
