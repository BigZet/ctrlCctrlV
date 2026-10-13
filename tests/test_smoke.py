"""Exercise the real GUI worker lifecycle and clean interpreter shutdown."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


def run_smoke(*arguments, **options):
    # A GUI worker that is stuck during shutdown otherwise produces only the
    # parent process's TimeoutExpired, hiding the code that is actually blocked.
    command = [sys.executable, "-c",
               "import faulthandler, runpy; "
               "faulthandler.dump_traceback_later(15); "
               "runpy.run_module('ctrlcctrlv', run_name='__main__')", *arguments]
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=20, **options)
    except subprocess.TimeoutExpired as error:
        def output(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        pytest.fail("Smoke process did not exit within 20 seconds.\n"
                    f"stdout:\n{output(error.stdout)}\nstderr:\n{output(error.stderr)}")


def test_gui_smoke_checks_parser_and_diagnostics_then_exits_cleanly(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = run_smoke("--smoke", "--data-dir", str(tmp_path), cwd=root, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GUI, background syntax and diagnostics checks passed" in result.stdout
    assert "OCR transport and cancellation checks passed" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows credential vault")
def test_opt_in_gui_smoke_checks_disposable_credentials(tmp_path):
    result = run_smoke("--smoke", "--smoke-credentials", "--data-dir", str(tmp_path),
                       env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Disposable credential check passed" in result.stdout
