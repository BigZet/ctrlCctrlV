"""Native boundaries are simulated; these tests never send input or register keys."""
from types import SimpleNamespace
import sys
import threading

import pytest

from ctrlcctrlv.core import Cancelled, OperationError
from ctrlcctrlv.macos import MacPlatform
from ctrlcctrlv.platforms import active_target
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.typing import type_text


class Accessibility:
    kAXErrorSuccess = 0
    kAXFocusedWindowAttribute = "AXFocusedWindow"

    def __init__(self):
        self.pid, self.window, self.error = 501, 1, 0
        self.owner = 501
        self.after_read = lambda: None

    def AXUIElementCreateApplication(self, pid):
        return SimpleNamespace(pid=pid)

    def AXUIElementSetMessagingTimeout(self, element, seconds):
        assert 0 < seconds <= 0.5
        return 0

    def AXUIElementCopyAttributeValue(self, app, attribute, output):
        assert attribute == "AXFocusedWindow" and output is None
        value = SimpleNamespace(window=self.window, pid=self.owner, type_id=42) if self.window else None
        self.after_read()
        return self.error, value

    def AXUIElementGetPid(self, element, output):
        assert output is None
        return 0, element.pid

    def AXUIElementGetTypeID(self):
        return 42


@pytest.fixture
def mac(monkeypatch):
    access = Accessibility()
    app = SimpleNamespace(processIdentifier=lambda: access.pid)
    workspace = SimpleNamespace(frontmostApplication=lambda: app if access.pid else None)
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace)))
    monkeypatch.setitem(sys.modules, "ApplicationServices", access)
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "CoreFoundation", SimpleNamespace(
        CFEqual=lambda first, second: first.window == second.window and first.pid == second.pid,
        CFGetTypeID=lambda element: element.type_id))
    adapter = MacPlatform()
    adapter.permissions = lambda: {"accessibility": True}
    adapter.modifiers_pressed = lambda: False
    adapter.events = []
    adapter.character = lambda char: adapter.events.append((access.window, char))
    return adapter, access


def test_same_process_window_switch_stops_before_next_character(mac):
    adapter, access = mac
    with pytest.raises(Cancelled, match="окно"):
        type_text(adapter, "abc", Settings(character_delay=0.001), threading.Event(),
                  lambda *_: setattr(access, "window", 2), expected_target=active_target(adapter))
    assert adapter.events == [(1, "a")]


def test_window_switch_before_worker_starts_sends_nothing(mac):
    adapter, access = mac
    expected = active_target(adapter)
    access.window = 2
    with pytest.raises(Cancelled):
        type_text(adapter, "abc", Settings(), threading.Event(), lambda *_: None, expected_target=expected)
    assert adapter.events == []


def test_fresh_accessibility_wrappers_for_same_window_do_not_interrupt_typing(mac):
    adapter, _ = mac
    type_text(adapter, "abc", Settings(character_delay=0.001), threading.Event(), lambda *_: None,
              expected_target=active_target(adapter))
    assert adapter.events == [(1, "a"), (1, "b"), (1, "c")]


@pytest.mark.parametrize("failure", ["no_app", "no_window", "ax_error", "wrong_owner", "focus_changed"])
def test_unknown_focused_window_fails_closed(mac, failure):
    adapter, access = mac
    if failure == "no_app":
        access.pid = None
    elif failure == "no_window":
        access.window = None
    elif failure == "ax_error":
        access.error = -25204
    elif failure == "wrong_owner":
        access.owner = 999
    else:
        access.after_read = lambda: setattr(access, "pid", access.pid + 1)
    assert active_target(adapter) is None
    with pytest.raises(OperationError):
        type_text(adapter, "abc", Settings(), threading.Event(), lambda *_: None)
    assert adapter.events == []


def test_losing_window_identity_mid_typing_stops_input(mac):
    adapter, access = mac
    with pytest.raises(Cancelled):
        type_text(adapter, "abc", Settings(character_delay=0.001), threading.Event(),
                  lambda *_: setattr(access, "error", -25204))
    assert adapter.events == [(1, "a")]


class Quartz:
    kCGEventFlagMaskCommand, kCGEventFlagMaskControl = 1, 2
    kCGEventFlagMaskAlternate, kCGEventFlagMaskShift = 4, 8
    kCGSessionEventTap = kCGHeadInsertEventTap = kCGEventTapOptionListenOnly = 0
    kCGEventKeyDown, kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput = 10, 11, 12
    kCGKeyboardEventAutorepeat, kCGKeyboardEventKeycode = "repeat", "key"
    kCFRunLoopCommonModes, kCFRunLoopDefaultMode = "common", "default"

    def __init__(self, delay_first=False):
        self.delay_first = delay_first
        self.before_loop, self.allow_loop = threading.Event(), threading.Event()
        self.loops, self.taps, self.calls = [], [], []
        self.current = threading.local()

    def CGEventTapCreate(self, place, head, options, mask, callback, refcon):
        tap = SimpleNamespace(callback=callback, valid=True, enabled=False, started=threading.Event())
        self.taps.append(tap)
        return tap

    def CGEventTapEnable(self, tap, enabled):
        self.calls.append(("enable", self.taps.index(tap), enabled))
        tap.enabled = enabled
        if enabled:
            tap.started.set()

    def CFMachPortInvalidate(self, tap):
        self.calls.append(("invalidate", self.taps.index(tap)))
        tap.valid, tap.enabled = False, False

    def CFRunLoopGetCurrent(self):
        loop = SimpleNamespace(stopped=threading.Event(), running=threading.Event())
        self.loops.append(loop)
        self.current.loop = loop
        if self.delay_first and len(self.loops) == 1:
            self.before_loop.set()
            assert self.allow_loop.wait(5)
        return loop

    def CFMachPortCreateRunLoopSource(self, allocator, tap, order):
        return SimpleNamespace(tap=tap)

    def CFRunLoopAddSource(self, loop, source, mode):
        self.calls.append(("add", self.taps.index(source.tap)))

    def CFRunLoopRemoveSource(self, loop, source, mode):
        self.calls.append(("remove", self.taps.index(source.tap)))

    def CFRunLoopRunInMode(self, mode, seconds, once):
        self.current.loop.running.set()
        self.current.loop.stopped.wait(min(seconds, 0.02))
        return 3

    def CFRunLoopRun(self):
        self.current.loop.running.set()
        self.current.loop.stopped.wait(0.1)

    def CFRunLoopStop(self, loop):
        loop.stopped.set()

    def CGEventGetFlags(self, event):
        return event["flags"]

    def CGEventGetIntegerValueField(self, event, field):
        return event[field]


def test_stop_before_loop_initialization_never_reenables_tap(mac):
    adapter, _ = mac
    q = adapter.quartz = Quartz(delay_first=True)
    assert adapter.start_hotkeys(lambda action: None, {"capture": "ctrl+alt+4"})
    thread = adapter.thread
    assert q.before_loop.wait(1)
    try:
        adapter.stop_hotkeys()
        stopped_at = len(q.calls)
        q.allow_loop.set()
        thread.join(1)
        assert not thread.is_alive()
        assert not any(call[0] == "enable" and call[2] for call in q.calls[stopped_at:])
        assert not q.taps[0].enabled and not q.taps[0].valid
    finally:
        q.allow_loop.set()
        adapter.stop_hotkeys()
        thread.join(1)


def test_immediate_restart_keeps_old_worker_and_callbacks_off_new_registration(mac):
    adapter, _ = mac
    q = adapter.quartz = Quartz(delay_first=True)
    actions = []
    assert adapter.start_hotkeys(actions.append, {"old": "ctrl+alt+4"})
    old_thread = adapter.thread
    assert q.before_loop.wait(1)
    try:
        assert adapter.start_hotkeys(actions.append, {"new": "ctrl+alt+4"})
        new_thread = adapter.thread
        assert q.taps[1].started.wait(1)
        q.allow_loop.set()
        old_thread.join(1)
        assert not old_thread.is_alive()
        event = {"flags": 6, "repeat": 0, "key": 21}
        q.taps[0].callback(None, q.kCGEventTapDisabledByTimeout, event, None)
        q.taps[0].callback(None, q.kCGEventKeyDown, event, None)
        q.taps[1].callback(None, q.kCGEventKeyDown, event, None)
        assert actions == ["new"]
        assert not q.taps[0].valid and not q.taps[0].enabled
        assert q.taps[1].valid and q.taps[1].enabled
        adapter.stop_hotkeys()
        new_thread.join(1)
        assert not new_thread.is_alive()
        assert not q.taps[1].valid and not q.taps[1].enabled
    finally:
        q.allow_loop.set()
        adapter.stop_hotkeys()
        old_thread.join(1)


def test_stopped_callback_never_dispatches_or_reenables_listener(mac):
    adapter, _ = mac
    q = adapter.quartz = Quartz()
    actions = []
    assert adapter.start_hotkeys(actions.append, {"capture": "ctrl+alt+4"})
    callback = q.taps[0].callback
    adapter.stop_hotkeys()
    event = {"flags": 6, "repeat": 0, "key": 21}
    callback(None, q.kCGEventTapDisabledByTimeout, event, None)
    callback(None, q.kCGEventKeyDown, event, None)
    assert actions == []
    assert not q.taps[0].valid and not q.taps[0].enabled
