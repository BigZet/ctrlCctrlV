"""OS-backed profile lock shared with Rust (flock / overlapping LockFileEx range).

Never unlink this file: removing a locked inode would allow a second owner.
"""

import os
import sys


class ProfileLock:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.stream = (directory / ".profile.lock").open("a+b")
        try:
            if sys.platform == "win32":
                import ctypes as c
                import msvcrt

                class Overlapped(c.Structure):
                    _fields_ = [
                        ("internal", c.c_size_t),
                        ("internal_high", c.c_size_t),
                        ("offset", c.c_uint32),
                        ("offset_high", c.c_uint32),
                        ("event", c.c_void_p),
                    ]

                self.overlapped = Overlapped()
                kernel = c.WinDLL("kernel32", use_last_error=True)
                kernel.LockFileEx.argtypes = [
                    c.c_void_p,
                    c.c_uint32,
                    c.c_uint32,
                    c.c_uint32,
                    c.c_uint32,
                    c.POINTER(Overlapped),
                ]
                kernel.LockFileEx.restype = c.c_int
                if not kernel.LockFileEx(
                    msvcrt.get_osfhandle(self.stream.fileno()),
                    3,
                    0,
                    1,
                    0,
                    c.byref(self.overlapped),
                ):
                    error = c.get_last_error()
                    if error == 33:
                        raise BlockingIOError("Профиль используется другим экземпляром")
                    raise c.WinError(error)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.stream.close()
            raise

    def close(self):
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
