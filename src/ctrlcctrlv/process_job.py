"""Windows kill-on-close job, owned by the runtime rather than a global process."""

import ctypes as c


class ProcessJob:
    def __init__(self):
        class Basic(c.Structure):
            _fields_ = [
                ("process_time", c.c_int64),
                ("job_time", c.c_int64),
                ("flags", c.c_uint32),
                ("minimum", c.c_size_t),
                ("maximum", c.c_size_t),
                ("active_limit", c.c_uint32),
                ("affinity", c.c_size_t),
                ("priority", c.c_uint32),
                ("scheduling", c.c_uint32),
            ]

        class Extended(c.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", c.c_uint64 * 6),
                ("process_memory", c.c_size_t),
                ("job_memory", c.c_size_t),
                ("peak_process", c.c_size_t),
                ("peak_job", c.c_size_t),
            ]

        self.handle = None
        self.kernel = k = c.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.argtypes = [c.c_void_p, c.c_wchar_p]
        k.CreateJobObjectW.restype = c.c_void_p
        k.SetInformationJobObject.argtypes = [
            c.c_void_p,
            c.c_int,
            c.c_void_p,
            c.c_uint32,
        ]
        k.SetInformationJobObject.restype = c.c_int
        k.AssignProcessToJobObject.argtypes = [c.c_void_p, c.c_void_p]
        k.AssignProcessToJobObject.restype = c.c_int
        k.OpenProcess.argtypes = [c.c_uint32, c.c_int, c.c_uint32]
        k.OpenProcess.restype = c.c_void_p
        k.CloseHandle.argtypes = [c.c_void_p]
        k.CloseHandle.restype = c.c_int
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(
            self.handle, 9, c.byref(limits), c.sizeof(limits)
        ):
            error = c.get_last_error()
            self.close()
            raise c.WinError(error)

    def attach(self, process):
        handle = self.kernel.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            raise c.WinError(c.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, handle):
                raise c.WinError(c.get_last_error())
        finally:
            self.kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __del__(self):
        self.close()
