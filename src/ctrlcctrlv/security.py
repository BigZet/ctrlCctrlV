"""Restrict temporary inference credentials before writing their contents."""
import os
import sys


def protect_keyfile(path, stream):
    if sys.platform != "win32":
        os.fchmod(stream.fileno(), 0o600)
        return
    import ctypes as c
    advapi = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [c.c_wchar_p, c.c_uint32, c.POINTER(c.c_void_p), c.c_void_p]
    convert.restype = c.c_int
    set_security = advapi.SetFileSecurityW
    set_security.argtypes = [c.c_wchar_p, c.c_uint32, c.c_void_p]
    set_security.restype = c.c_int
    kernel.LocalFree.argtypes = [c.c_void_p]
    kernel.LocalFree.restype = c.c_void_p
    descriptor = c.c_void_p()
    # Protected DACL: owner, administrators and SYSTEM; no inherited grants.
    if not convert("D:P(A;;FA;;;OW)(A;;FA;;;BA)(A;;FA;;;SY)", 1, c.byref(descriptor), None):
        raise c.WinError(c.get_last_error())
    try:
        if not set_security(str(path), 0x80000004, descriptor):
            raise c.WinError(c.get_last_error())
    finally:
        kernel.LocalFree(descriptor)
