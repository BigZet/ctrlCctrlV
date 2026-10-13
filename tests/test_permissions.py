from itertools import product
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.app import Controller
from ctrlcctrlv.macos import MacPlatform


@pytest.mark.parametrize("granted", list(product((False, True), repeat=3)))
def test_system_requests_only_missing_permissions(granted):
    values = dict(zip(("accessibility", "screen", "input_monitoring"), granted))
    requested = []
    platform = MacPlatform.__new__(MacPlatform)
    def request(key):
        requested.append(key)
        values[key] = True
    platform.accessibility = SimpleNamespace(
        AXIsProcessTrusted=lambda: values["accessibility"],
        kAXTrustedCheckOptionPrompt="prompt",
        AXIsProcessTrustedWithOptions=lambda options: request("accessibility"))
    platform.quartz = SimpleNamespace(
        CGPreflightScreenCaptureAccess=lambda: values["screen"],
        CGRequestScreenCaptureAccess=lambda: request("screen"),
        CGPreflightListenEventAccess=lambda: values["input_monitoring"],
        CGRequestListenEventAccess=lambda: request("input_monitoring"))
    assert all(platform.request_permissions().values())
    assert requested == [key for key, allowed in zip(values, granted) if not allowed]
    requested.clear()
    platform.request_permissions()
    assert requested == []


class Adapter:
    def __init__(self):
        self.values = {"accessibility": False, "screen": True, "input_monitoring": False}
        self.registrations = self.requests = self.stops = 0
        self.opened = []

    def permissions(self):
        return self.values.copy()

    def request_permissions(self):
        self.requests += 1

    def open_permission_settings(self, permission):
        if not self.values[permission]:
            self.opened.append(permission)

    def start_hotkeys(self, *args):
        self.registrations += 1
        return True

    def stop_hotkeys(self):
        self.stops += 1


def test_ui_detects_new_access_and_does_not_request_granted_rights(tmp_path):
    app = QApplication.instance() or QApplication([])
    adapter = Adapter()
    c = Controller(app, tmp_path, platform_adapter=adapter)
    try:
        assert adapter.registrations == 0
        assert c.window.permission_buttons["input_monitoring"].isEnabled()
        assert "доступен" in c.window.permission_labels["screen"].text()
        assert not c.window.permission_buttons["screen"].isEnabled()
        c.window.permission_buttons["accessibility"].click()
        assert adapter.opened == ["accessibility"]
        adapter.values = dict.fromkeys(adapter.values, True)
        c.refresh()
        assert c.hotkeys_registered
        assert adapter.registrations == 1
        assert not any(b.isEnabled() for b in c.window.permission_buttons.values())
        c.refresh()
        c.request_permissions()
        assert adapter.registrations == 1
        assert adapter.requests == 0
        adapter.values["input_monitoring"] = False
        c.refresh()
        assert not c.hotkeys_registered
        assert c.window.permission_buttons["input_monitoring"].isEnabled()
    finally:
        c.tray.hide()
        c.watchdog.stop()
        c.idle_timer.stop()
        c.permissions_timer.stop()
        c.pool.waitForDone()
        c.window.deleteLater()
        c.preview.deleteLater()
        c.deleteLater()
        app.sendPostedEvents()


@pytest.mark.parametrize("permission,pane", [
    ("accessibility", "Privacy_Accessibility"),
    ("screen", "Privacy_ScreenCapture"),
    ("input_monitoring", "Privacy_ListenEvent"),
])
def test_open_settings_targets_only_missing_permission(monkeypatch, permission, pane):
    from PySide6.QtGui import QDesktopServices
    platform = MacPlatform.__new__(MacPlatform)
    values = dict.fromkeys(("accessibility", "screen", "input_monitoring"), False)
    platform.permissions = lambda: values.copy()
    opened = []
    requested = []
    platform.request_permission = lambda key: requested.append(key)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    assert platform.open_permission_settings(permission)
    assert requested == [permission]
    assert opened == [f"x-apple.systempreferences:com.apple.preference.security?{pane}"]
    values[permission] = True
    assert not platform.open_permission_settings(permission)
    assert len(opened) == 1
    assert requested == [permission]


@pytest.mark.parametrize("permission", ("accessibility", "screen", "input_monitoring"))
def test_individual_request_never_requests_other_permissions(permission):
    platform = MacPlatform.__new__(MacPlatform)
    values = dict.fromkeys(("accessibility", "screen", "input_monitoring"), False)
    requested = []
    platform.permissions = lambda: values.copy()
    platform.accessibility = SimpleNamespace(
        kAXTrustedCheckOptionPrompt="prompt",
        AXIsProcessTrustedWithOptions=lambda options: requested.append("accessibility"))
    platform.quartz = SimpleNamespace(
        CGRequestScreenCaptureAccess=lambda: requested.append("screen"),
        CGRequestListenEventAccess=lambda: requested.append("input_monitoring"))
    assert platform.request_permission(permission)
    assert requested == [permission]
    values[permission] = True
    assert not platform.request_permission(permission)
    assert requested == [permission]
