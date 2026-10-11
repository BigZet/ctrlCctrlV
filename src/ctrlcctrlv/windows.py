"""Win32 integration. Only construction loads Windows system libraries."""
import ctypes as c
import sys
from pathlib import Path

from .core import OperationError
from .hotkeys import parse_hotkey

# Explicit widths keep structures correct on Windows and in portable tests.
DWORD, LONG, WORD, UINT, ULONG_PTR = c.c_uint32, c.c_int32, c.c_uint16, c.c_uint32, c.c_size_t


class NVML_MEMORY(c.Structure):
    _fields_ = [("total", c.c_uint64), ("free", c.c_uint64), ("used", c.c_uint64)]


def nvidia_memory_capacity():
    """Query driver-wide memory instead of WDDM's per-process CUDA budget."""
    try:
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.GetSystemDirectoryW.argtypes = [c.c_wchar_p, c.c_uint32]
        kernel.GetSystemDirectoryW.restype = c.c_uint32
        directory = c.create_unicode_buffer(32768)
        length = kernel.GetSystemDirectoryW(directory, len(directory))
        if not 0 < length < len(directory):
            return []
        nvml = c.WinDLL(str(Path(directory.value) / "nvml.dll"))
        nvml.nvmlInit_v2.argtypes = []
        nvml.nvmlInit_v2.restype = c.c_int
        nvml.nvmlShutdown.argtypes = []
        nvml.nvmlShutdown.restype = c.c_int
        nvml.nvmlDeviceGetCount_v2.argtypes = [c.POINTER(c.c_uint32)]
        nvml.nvmlDeviceGetCount_v2.restype = c.c_int
        nvml.nvmlDeviceGetHandleByIndex_v2.argtypes = [c.c_uint32, c.POINTER(c.c_void_p)]
        nvml.nvmlDeviceGetHandleByIndex_v2.restype = c.c_int
        nvml.nvmlDeviceGetName.argtypes = [c.c_void_p, c.c_char_p, c.c_uint32]
        nvml.nvmlDeviceGetName.restype = c.c_int
        nvml.nvmlDeviceGetMemoryInfo.argtypes = [c.c_void_p, c.POINTER(NVML_MEMORY)]
        nvml.nvmlDeviceGetMemoryInfo.restype = c.c_int
        if nvml.nvmlInit_v2():
            return []
        try:
            count = c.c_uint32()
            if nvml.nvmlDeviceGetCount_v2(c.byref(count)) or not 1 <= count.value <= 32:
                return []
            devices = []
            for index in range(count.value):
                handle = c.c_void_p()
                name = c.create_string_buffer(256)
                memory = NVML_MEMORY()
                if (nvml.nvmlDeviceGetHandleByIndex_v2(index, c.byref(handle)) or
                    nvml.nvmlDeviceGetName(handle, name, len(name)) or
                    nvml.nvmlDeviceGetMemoryInfo(handle, c.byref(memory))):
                    continue
                if 0 < memory.total and memory.free <= memory.total:
                    devices.append({"name": name.value.decode("utf-8", errors="replace"),
                                    "total_bytes": memory.total, "available_bytes": memory.free})
            return devices
        finally:
            nvml.nvmlShutdown()
    except (OSError, AttributeError):
        return []


class KEYBDINPUT(c.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(c.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(c.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class INPUTUNION(c.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(c.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", DWORD), ("data", INPUTUNION)]


class POINT(c.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MSG(c.Structure):
    _fields_ = [("hwnd", c.c_void_p), ("message", UINT), ("wParam", ULONG_PTR),
                ("lParam", c.c_ssize_t), ("time", DWORD), ("pt", POINT), ("lPrivate", DWORD)]


def keyboard(vk=0, scan=0, flags=0):
    return INPUT(type=1, ki=KEYBDINPUT(vk, scan, flags, 0, 0))


def memory_capacity():
    class MEMORYSTATUSEX(c.Structure):
        _fields_ = [("length", DWORD), ("load", DWORD)] + [(name, c.c_uint64) for name in (
            "total_physical", "available_physical", "total_page", "available_page", "total_virtual", "available_virtual", "extended")]
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [c.POINTER(MEMORYSTATUSEX)]
    kernel.GlobalMemoryStatusEx.restype = c.c_int
    memory = MEMORYSTATUSEX()
    memory.length = c.sizeof(memory)
    if kernel.GlobalMemoryStatusEx(c.byref(memory)):
        return memory.total_physical, memory.available_physical
    return None, None


def physical_memory():
    return memory_capacity()[0]


def resident_memory(pid):
    class COUNTERS(c.Structure):
        _fields_ = [("cb", DWORD), ("faults", DWORD)] + [(name, c.c_size_t) for name in (
            "peak", "working_set", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
    kernel = c.WinDLL("kernel32", use_last_error=True)
    psapi = c.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [DWORD, c.c_int, DWORD]
    kernel.OpenProcess.restype = c.c_void_p
    kernel.CloseHandle.argtypes = [c.c_void_p]
    kernel.CloseHandle.restype = c.c_int
    psapi.GetProcessMemoryInfo.argtypes = [c.c_void_p, c.POINTER(COUNTERS), DWORD]
    psapi.GetProcessMemoryInfo.restype = c.c_int
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return None
    try:
        counters = COUNTERS()
        counters.cb = c.sizeof(counters)
        return counters.working_set if psapi.GetProcessMemoryInfo(handle, c.byref(counters), c.sizeof(counters)) else None
    finally:
        kernel.CloseHandle(handle)


class WindowsPlatform:
    def __init__(self, user32=None):
        self.user32 = user32
        if user32 is None:
            if sys.platform != "win32":
                raise OperationError("Адаптер Windows доступен только на Windows")
            self.user32 = u = c.WinDLL("user32", use_last_error=True)
            signatures = {
                "SendInput": ([UINT, c.POINTER(INPUT), c.c_int], UINT),
                "GetForegroundWindow": ([], c.c_void_p),
                "GetWindowThreadProcessId": ([c.c_void_p, c.POINTER(DWORD)], DWORD),
                "GetAsyncKeyState": ([c.c_int], c.c_int16),
                "RegisterHotKey": ([c.c_void_p, c.c_int, UINT, UINT], c.c_int),
                "UnregisterHotKey": ([c.c_void_p, c.c_int], c.c_int),
            }
            for name, (args, result) in signatures.items():
                getattr(u, name).argtypes = args
                getattr(u, name).restype = result
        self.filter = None
        self.registered = {}
        self.last_hotkey_error = ""

    def permissions(self):
        # These operations have no macOS-style privacy grant on Windows.
        # SendInput reports an error for protected/elevated target windows.
        return {"accessibility": True, "screen": True, "input_monitoring": True}

    def request_permissions(self):
        return self.permissions()

    def request_permission(self, permission):
        if permission not in self.permissions():
            raise ValueError("Unknown permission")
        return False

    open_permission_settings = request_permission

    def active_target(self):
        window = self.user32.GetForegroundWindow()
        if not window:
            return None
        pid = DWORD()
        if not self.user32.GetWindowThreadProcessId(window, c.byref(pid)):
            return None
        return int(window), pid.value

    def active_pid(self):
        target = self.active_target()
        return target[1] if target else None

    def modifiers_pressed(self):
        return any(self.user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def _send(self, events):
        array = (INPUT * len(events))(*events)
        sent = self.user32.SendInput(len(array), array, c.sizeof(INPUT))
        if sent != len(array):
            # Release synthetic keys after a partial batch, including Shift.
            releases = [keyboard(e.ki.wVk, e.ki.wScan, e.ki.dwFlags | 2)
                        for e in events if not e.ki.dwFlags & 2]
            if releases:
                up = (INPUT * len(releases))(*releases)
                self.user32.SendInput(len(up), up, c.sizeof(INPUT))
            raise OperationError("Windows отклонила ввод. Проверьте активное окно и уровни прав приложения и редактора.")

    def character(self, char):
        encoded = char.encode("utf-16-le")
        events = []
        for i in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[i:i + 2], "little")
            events.extend((keyboard(scan=unit, flags=4), keyboard(scan=unit, flags=6)))
        self._send(events)

    def key(self, vk, extended=False):
        flag = 1 if extended else 0
        self._send([keyboard(vk, flags=flag), keyboard(vk, flags=flag | 2)])

    def enter(self):
        self.key(0x0D)

    def tab(self):
        self.key(0x09)

    def select_indentation(self):
        self._send([keyboard(0x10), keyboard(0x24, flags=1),
                    keyboard(0x24, flags=3), keyboard(0x10, flags=2)])

    def start_hotkeys(self, emit, bindings):
        from PySide6.QtCore import QAbstractNativeEventFilter
        from PySide6.QtWidgets import QApplication
        self.stop_hotkeys()
        self.last_hotkey_error = ""
        app = QApplication.instance()
        if app is None:
            raise OperationError("Для горячих клавиш требуется работающий интерфейс")
        shortcuts = []
        flags = {"alt": 1, "ctrl": 2, "shift": 4, "cmd": 8}
        for action, value in bindings.items():
            modifiers, key = parse_hotkey(value)
            shortcuts.append((action, sum(flags[m] for m in modifiers) | 0x4000,
                              0x1B if key == "esc" else ord(key.upper()), value))
        try:
            for identifier, (action, mods, vk, value) in enumerate(shortcuts, 0x4300):
                if not self.user32.RegisterHotKey(None, identifier, mods, vk):
                    self.last_hotkey_error = f"Сочетание {value} занято или недоступно. Измените его в разделе «Система»."
                    self.stop_hotkeys()
                    return False
                self.registered[identifier] = action
            owner = self
            class Filter(QAbstractNativeEventFilter):
                def nativeEventFilter(self, event_type, message):
                    if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                        msg = MSG.from_address(int(message))
                        if msg.message == 0x0312 and msg.wParam in owner.registered:
                            emit(owner.registered[msg.wParam])
                            return True, 0
                    return False, 0
            self.filter = Filter()
            app.installNativeEventFilter(self.filter)
            return True
        except BaseException:
            self.stop_hotkeys()
            raise

    def stop_hotkeys(self):
        if self.filter is not None:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.removeNativeEventFilter(self.filter)
            self.filter = None
        for identifier in self.registered:
            self.user32.UnregisterHotKey(None, identifier)
        self.registered.clear()
