"""macOS adapter. Importing the common core never imports system APIs."""
import threading
from .core import OperationError
from .typing import type_text


class MacPlatform:
    def __init__(self):
        import AppKit
        import ApplicationServices
        import Quartz
        self.quartz = Quartz
        self.appkit = AppKit
        self.accessibility = ApplicationServices
        self.tap = self.loop = self.source = None
        self.thread = None
        self.callback = None

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

        def callback(proxy, event_type, event, refcon):
            if event_type in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                if self.tap:
                    q.CGEventTapEnable(self.tap, True)
                return event
            flags = q.CGEventGetFlags(event) & mask
            if not q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventAutorepeat):
                code = q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)
                action = shortcuts.get((flags, code))
                if action:
                    emit(action)
            return event

        self.callback = callback
        self.tap = q.CGEventTapCreate(q.kCGSessionEventTap, q.kCGHeadInsertEventTap,
            q.kCGEventTapOptionListenOnly, 1 << q.kCGEventKeyDown, callback, None)
        if not self.tap:
            return False

        def run():
            self.loop = q.CFRunLoopGetCurrent()
            self.source = q.CFMachPortCreateRunLoopSource(None, self.tap, 0)
            q.CFRunLoopAddSource(self.loop, self.source, q.kCFRunLoopCommonModes)
            q.CGEventTapEnable(self.tap, True)
            q.CFRunLoopRun()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        return True

    def stop_hotkeys(self):
        if self.tap:
            self.quartz.CGEventTapEnable(self.tap, False)
        if self.loop:
            self.quartz.CFRunLoopStop(self.loop)
        if self.thread:
            self.thread.join(timeout=1)
