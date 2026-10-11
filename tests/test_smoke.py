"""Exercise the real GUI worker lifecycle and clean interpreter shutdown."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


def test_gui_smoke_checks_parser_and_diagnostics_then_exits_cleanly(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-m", "ctrlcctrlv", "--smoke", "--data-dir", str(tmp_path)],
                            cwd=root, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "GUI, background syntax and diagnostics checks passed" in result.stdout
    assert "OCR transport and cancellation checks passed" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows credential vault")
def test_opt_in_gui_smoke_checks_disposable_credentials(tmp_path):
    result = subprocess.run([sys.executable, "-m", "ctrlcctrlv", "--smoke", "--smoke-credentials",
                             "--data-dir", str(tmp_path)], env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Disposable credential check passed" in result.stdout
