import ctypes as c
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")


@pytest.mark.parametrize("crash", [False, True])
def test_owned_child_exits_when_job_owner_closes_or_crashes(crash):
    script = """
import subprocess,sys
from ctrlcctrlv.process_job import ProcessJob
job=ProcessJob()
child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    job.attach(child)
except BaseException:
    child.kill()
    child.wait()
    raise
print(child.pid,flush=True)
sys.stdin.readline()
job.close()
child.wait(timeout=5)
"""
    owner = subprocess.Popen(
        [sys.executable, "-u", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [c.c_uint32, c.c_int, c.c_uint32]
    kernel.OpenProcess.restype = c.c_void_p
    kernel.WaitForSingleObject.argtypes = [c.c_void_p, c.c_uint32]
    kernel.WaitForSingleObject.restype = c.c_uint32
    kernel.CloseHandle.argtypes = [c.c_void_p]
    handle = None
    try:
        line = owner.stdout.readline()
        assert line.strip().isdigit(), owner.stderr.read()
        pid = int(line)
        handle = kernel.OpenProcess(0x100000, False, pid)
        assert handle
        if crash:
            owner.kill()
            owner.wait(timeout=5)
        else:
            _, error = owner.communicate("\n", timeout=10)
            assert owner.returncode == 0, error
        assert kernel.WaitForSingleObject(handle, 10000) == 0
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if handle:
            kernel.CloseHandle(handle)
