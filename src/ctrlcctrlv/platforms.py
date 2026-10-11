"""Select native integration without importing the other operating system's APIs."""
import sys


def create_platform():
    if sys.platform == "darwin":
        from .macos import MacPlatform
        return MacPlatform()
    if sys.platform == "win32":
        from .windows import WindowsPlatform
        return WindowsPlatform()
    return None


def active_target(adapter):
    method = getattr(adapter, "active_target", adapter.active_pid)
    return method()
