"""macOS adapter. Importing the common core never imports system APIs."""
from dataclasses import dataclass, field
import threading
from .core import OperationError
from .typing import type_text


class _AXWindow:
    """Retain the AX element and compare its native identity, not wrapper identity."""
    def __init__(self, element, equal):
        self.element, self.equal = element, equal

    def __eq__(self, other):
        if not isinstance(other, _AXWindow):
            return NotImplemented
        try:
            return bool(self.equal(self.element, other.element))
        except Exception:
            return False


@dataclass
class _HotkeySession:
    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    tap: object = None
    loop: object = None
    source: object = None
    callback: object = None
    thread: object = None
    invalidated: bool = False


class MacPlatform:
    def __init__(self):
        import AppKit
        import ApplicationServices
        import CoreFoundation
        import Quartz
        self.quartz = Quartz
        self.appkit = AppKit
        self.accessibility = ApplicationServices
        self.corefoundation = CoreFoundation
        self._hotkeys = None
        self.thread = None

    def permissions(self):
        q = self.quartz
        return {"accessibility": bool(self.accessibility.AXIsProcessTrusted()),
            "screen": bool(q.CGPreflightScreenCaptureAccess()),
            "input_monitoring": bool(q.CGPreflightListenEventAccess())}

    def request_permission(self, permission):
        if self.permissions()[permission]:
            return False
        if permission == "accessibility":
            a = self.accessibility
            a.AXIsProcessTrustedWithOptions({a.kAXTrustedCheckOptionPrompt: True})
        elif permission == "screen":
            self.quartz.CGRequestScreenCaptureAccess()
        elif permission == "input_monitoring":
            self.quartz.CGRequestListenEventAccess()
        else:
            raise ValueError(f"Unknown permission: {permission}")
        return True

    def request_permissions(self):
        for permission in ("accessibility", "screen", "input_monitoring"):
            self.request_permission(permission)
        return self.permissions()

    def open_permission_settings(self, permission):
        panes = {"accessibility": "Privacy_Accessibility",
                 "screen": "Privacy_ScreenCapture",
                 "input_monitoring": "Privacy_ListenEvent"}
        pane = panes[permission]
        if self.permissions()[permission]:
            return False
        # A deep link alone does not register this app with macOS privacy controls.
        self.request_permission(permission)
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        url = QUrl(f"x-apple.systempreferences:com.apple.preference.security?{pane}")
        if not QDesktopServices.openUrl(url):
            raise OperationError("Не удалось открыть системные настройки macOS")
        return True

    def active_pid(self):
        app = self.appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app else None

    def active_target(self):
        """Identify the focused window; an unavailable AX identity must stop input."""
        a, cf = self.accessibility, self.corefoundation
        try:
            pid = self.active_pid()
            if pid is None:
                return None
            application = a.AXUIElementCreateApplication(pid)
            if application is None or a.AXUIElementSetMessagingTimeout(application, 0.2) != a.kAXErrorSuccess:
                return None
            error, window = a.AXUIElementCopyAttributeValue(application, a.kAXFocusedWindowAttribute, None)
            if (error != a.kAXErrorSuccess or window is None or
                    cf.CFGetTypeID(window) != a.AXUIElementGetTypeID()):
                return None
            error, owner = a.AXUIElementGetPid(window, None)
            if error != a.kAXErrorSuccess or owner != pid or self.active_pid() != pid:
                return None
            return pid, _AXWindow(window, cf.CFEqual)
        except Exception:
            return None

    def modifiers_pressed(self):
        q = self.quartz
        mask = q.kCGEventFlagMaskCommand | q.kCGEventFlagMaskControl | q.kCGEventFlagMaskAlternate | q.kCGEventFlagMaskShift
        return bool(q.CGEventSourceFlagsState(q.kCGEventSourceStateCombinedSessionState) & mask)

    def key(self, code, modifiers=0):
        q = self.quartz
        for down in (True, False):
            event = q.CGEventCreateKeyboardEvent(None, code, down)
            q.CGEventSetFlags(event, modifiers)
            q.CGEventPost(q.kCGHIDEventTap, event)

    def character(self, char):
        q = self.quartz
        # Quartz expects UTF-16 code units (non-BMP characters need a surrogate pair).
        encoded = char.encode("utf-16-le")
        units = tuple(chr(int.from_bytes(encoded[i:i+2], "little")) for i in range(0, len(encoded), 2))
        for down in (True, False):
            event = q.CGEventCreateKeyboardEvent(None, 0, down)
            q.CGEventSetFlags(event, 0)
            q.CGEventKeyboardSetUnicodeString(event, len(units), units)
            q.CGEventPost(q.kCGHIDEventTap, event)

    def enter(self):
        self.key(36)

    def tab(self):
        self.key(48)

    def select_indentation(self):
        self.key(115, self.quartz.kCGEventFlagMaskShift)

    def start_hotkeys(self, emit, bindings):
        self.stop_hotkeys()
        q = self.quartz
        from .hotkeys import KEY_CODES, parse_hotkey
        mask = q.kCGEventFlagMaskCommand | q.kCGEventFlagMaskControl | q.kCGEventFlagMaskAlternate | q.kCGEventFlagMaskShift
        flags_by_name = {"cmd": q.kCGEventFlagMaskCommand, "ctrl": q.kCGEventFlagMaskControl,
            "alt": q.kCGEventFlagMaskAlternate, "shift": q.kCGEventFlagMaskShift}
        shortcuts = {}
        for action, value in bindings.items():
            modifiers, key = parse_hotkey(value)
            required = 0
            for name in modifiers:
                required |= flags_by_name[name]
            shortcuts[(required, KEY_CODES[key])] = action

        session = _HotkeySession()

        def callback(proxy, event_type, event, refcon):
            with session.lock:
                if session.cancelled.is_set():
                    return event
                if event_type in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                    if session.tap:
                        q.CGEventTapEnable(session.tap, True)
                    return event
                flags = q.CGEventGetFlags(event) & mask
                if not q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventAutorepeat):
                    code = q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)
                    action = shortcuts.get((flags, code))
                    if action:
                        emit(action)
            return event

        session.callback = callback
        session.tap = q.CGEventTapCreate(q.kCGSessionEventTap, q.kCGHeadInsertEventTap,
            q.kCGEventTapOptionListenOnly, 1 << q.kCGEventKeyDown, callback, None)
        if not session.tap:
            return False

        def run():
            try:
                loop = q.CFRunLoopGetCurrent()
                with session.lock:
                    if session.cancelled.is_set():
                        return
                    session.loop = loop
                    session.source = q.CFMachPortCreateRunLoopSource(None, session.tap, 0)
                    q.CFRunLoopAddSource(loop, session.source, q.kCFRunLoopCommonModes)
                    q.CGEventTapEnable(session.tap, True)
                # A stop immediately before run-loop entry can miss CFRunLoopStop.
                # Bounded iterations still observe cancellation and release the tap.
                while not session.cancelled.is_set():
                    q.CFRunLoopRunInMode(q.kCFRunLoopDefaultMode, 0.1, False)
            finally:
                with session.lock:
                    session.cancelled.set()
                    if session.source is not None:
                        q.CFRunLoopRemoveSource(session.loop, session.source, q.kCFRunLoopCommonModes)
                        session.source = None
                    if not session.invalidated:
                        q.CGEventTapEnable(session.tap, False)
                        q.CFMachPortInvalidate(session.tap)
                        session.invalidated = True
                    session.tap = session.callback = None
                    session.loop = None

        session.thread = threading.Thread(target=run, daemon=True)
        self._hotkeys = session
        self.thread = session.thread
        try:
            session.thread.start()
        except BaseException:
            self.stop_hotkeys()
            raise
        return True

    def stop_hotkeys(self):
        session, self._hotkeys = self._hotkeys, None
        self.thread = None
        if session is None:
            return
        session.cancelled.set()
        with session.lock:
            if not session.invalidated:
                self.quartz.CGEventTapEnable(session.tap, False)
                self.quartz.CFMachPortInvalidate(session.tap)
                session.invalidated = True
            session.tap = session.callback = None
            if session.loop is not None:
                self.quartz.CFRunLoopStop(session.loop)
        if session.thread and session.thread.is_alive() and session.thread is not threading.current_thread():
            session.thread.join(timeout=1)
