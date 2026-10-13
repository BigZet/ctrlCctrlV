"""Release installers, diagnostics and Python metadata must name one version."""
from pathlib import Path
import tomllib

from ctrlcctrlv import __version__


def test_distribution_version_matches_runtime():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__
