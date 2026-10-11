import ctypes as c
import sys
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.hotkeys import parse_hotkey
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.typing import type_text
from ctrlcctrlv.windows import INPUT, MSG, WindowsPlatform


class User32:
    def __init__(self):
        self.window, self.pid = 123, 456
        self.events = []
        self.hotkeys = {}
        self.released = []
        self.pressed = set()
        self.conflict = None
        self.partial = False

    def GetForegroundWindow(self):
        return self.window

    def GetWindowThreadProcessId(self, window, pid):
        pid._obj.value = self.pid
        return 1

    def GetAsyncKeyState(self, vk):
        return 0x8000 if vk in self.pressed else 0

    def SendInput(self, count, events, size):
        assert size == c.sizeof(INPUT)
        self.events.extend((e.ki.wVk, e.ki.wScan, e.ki.dwFlags) for e in events)
        if self.partial:
            self.partial = False
            return 1
        return count

    def RegisterHotKey(self, hwnd, identifier, mods, vk):
        if vk == self.conflict:
            return 0
        self.hotkeys[identifier] = mods, vk
        return 1

    def UnregisterHotKey(self, hwnd, identifier):
        self.released.append(identifier)
        self.hotkeys.pop(identifier)
        return 1


def test_input_native_sizes_and_utf16_surrogates():
    assert c.sizeof(INPUT) == (40 if c.sizeof(c.c_void_p) == 8 else 28)
    u = User32()
    adapter = WindowsPlatform(u)
    adapter.character("Ж🙂")
    assert u.events == [(0, 0x0416, 4), (0, 0x0416, 6),
                        (0, 0xD83D, 4), (0, 0xD83D, 6), (0, 0xDE42, 4), (0, 0xDE42, 6)]


def test_windows_typing_uses_native_enter_tab_and_shift_home():
    u = User32()
    adapter = WindowsPlatform(u)
    type_text(adapter, "а\r\n\tx", Settings(character_delay=0.001, newline_delay=0, autoindent=True),
              threading.Event(), lambda *args: None)
    assert u.events == [(0, 0x430, 4), (0, 0x430, 6), (13, 0, 0), (13, 0, 2),
                        (16, 0, 0), (36, 0, 1), (36, 0, 3), (16, 0, 2),
                        (9, 0, 0), (9, 0, 2), (0, ord('x'), 4), (0, ord('x'), 6)]


def test_focus_switch_within_same_process_stops_input():
    u = User32()
    adapter = WindowsPlatform(u)
    with pytest.raises(Cancelled, match="окно"):
        type_text(adapter, "abc", Settings(character_delay=0.001), threading.Event(),
                  lambda *args: setattr(u, "window", 789), expected_target=(123, 456))
    assert len(u.events) == 2


def test_focus_switch_before_worker_or_no_foreground_never_sends():
    u = User32()
    adapter = WindowsPlatform(u)
    with pytest.raises(Cancelled):
        type_text(adapter, "abc", Settings(), threading.Event(), lambda *args: None,
                  expected_target=(999, 456))
    u.window = 0
    with pytest.raises(OperationError, match="активного окна"):
        type_text(adapter, "abc", Settings(), threading.Event(), lambda *args: None)
    assert not u.events


def test_new_physical_modifier_stops_further_typing():
    u = User32()
    with pytest.raises(Cancelled, match="модификатор"):
        type_text(WindowsPlatform(u), "abc", Settings(character_delay=0.001), threading.Event(),
                  lambda *args: u.pressed.add(0x11))
    assert len(u.events) == 2


def test_short_sendinput_releases_shift_and_reports_failure():
    u = User32()
    u.partial = True
    with pytest.raises(OperationError, match="отклонила"):
        WindowsPlatform(u).select_indentation()
    assert u.events[-2:] == [(16, 0, 2), (36, 0, 3)]


def test_hotkey_dispatch_norepeat_conflicts_and_cleanup():
    app = QApplication.instance() or QApplication([])
    u = User32()
    adapter = WindowsPlatform(u)
    bindings = {"capture": "ctrl+alt+4", "type": "ctrl+alt+v", "cancel": "ctrl+alt+esc"}
    actions = []
    assert adapter.start_hotkeys(actions.append, bindings)
    assert u.hotkeys[0x4300] == (0x4003, ord('4'))
    message = MSG(message=0x312, wParam=0x4301)
    assert adapter.filter.nativeEventFilter(b"windows_dispatcher_MSG", c.addressof(message)) == (True, 0)
    assert actions == ["type"]
    assert adapter.filter.nativeEventFilter(b"other", c.addressof(message)) == (False, 0)
    adapter.stop_hotkeys()
    assert not u.hotkeys and adapter.filter is None
    u.conflict = ord('V')
    assert not adapter.start_hotkeys(actions.append, bindings)
    assert not u.hotkeys and not adapter.registered
    assert "ctrl+alt+v" in adapter.last_hotkey_error
    assert adapter.start_hotkeys(actions.append, {"capture": "ctrl+alt+5"})
    adapter.stop_hotkeys()


def test_meta_aliases_cannot_hide_duplicate_hotkeys():
    assert parse_hotkey("win+ctrl+4") == parse_hotkey("cmd+ctrl+4")
    with pytest.raises(ValueError, match="различаться"):
        Settings(capture_hotkey="win+ctrl+4", typing_hotkey="cmd+ctrl+4").validate()


@pytest.mark.skipif(sys.platform != "win32", reason="native Win32 read-only probe")
def test_real_windows_queries_and_hotkey_lifecycle():
    app = QApplication.instance() or QApplication([])
    adapter = WindowsPlatform()
    assert all(adapter.permissions().values())
    target = adapter.active_target()
    assert target is None or (target[0] > 0 and target[1] > 0)
    try:
        assert adapter.start_hotkeys(lambda action: None, {"probe": "ctrl+alt+shift+9"})
    finally:
        adapter.stop_hotkeys()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows credential vault")
def test_windows_credential_vault_roundtrip_uses_disposable_service():
    import uuid
    from ctrlcctrlv.settings import Credentials
    credentials = Credentials()
    credentials.service = "ctrlCctrlV.Test." + uuid.uuid4().hex
    try:
        assert credentials.read() == ""
        credentials.write("synthetic-test-value")
        assert credentials.read() == "synthetic-test-value"
    finally:
        credentials.write("")
    assert credentials.read() == ""


def test_controller_selects_platform_through_factory(tmp_path, monkeypatch):
    from ctrlcctrlv.app import Controller
    adapter = WindowsPlatform(User32())
    monkeypatch.setattr("ctrlcctrlv.app.create_platform", lambda: adapter)
    app = QApplication.instance() or QApplication([])
    controller = Controller(app, tmp_path)
    try:
        assert controller.platform is adapter
        assert controller.hotkeys_registered
    finally:
        adapter.stop_hotkeys()
        for timer in (controller.watchdog, controller.idle_timer, controller.permissions_timer):
            timer.stop()
        controller.tray.hide()
        controller.window.close()
        controller.preview.close()
        controller.deleteLater()
