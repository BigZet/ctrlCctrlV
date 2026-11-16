import os
import subprocess
import sys

import pytest

from ctrlcctrlv.profile_lock import ProfileLock


def test_profile_lock_excludes_processes_and_releases_on_close(tmp_path):
    script = (
        "import sys; from pathlib import Path; "
        "from ctrlcctrlv.profile_lock import ProfileLock; "
        "guard = ProfileLock(Path(sys.argv[1]))"
    )
    with ProfileLock(tmp_path):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)], capture_output=True
        )
        assert result.returncode != 0
        assert b"BlockingIOError" in result.stderr
        with ProfileLock(tmp_path / "other"):
            pass
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".profile.lock").exists()


def test_crashed_owner_does_not_leave_a_stale_lock(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sys; from pathlib import Path; from ctrlcctrlv.profile_lock import ProfileLock; "
            "guard=ProfileLock(Path(sys.argv[1])); os._exit(7)",
            str(tmp_path),
        ],
        capture_output=True,
    )
    assert result.returncode == 7
    with ProfileLock(tmp_path):
        pass
