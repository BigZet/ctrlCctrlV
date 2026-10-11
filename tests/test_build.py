"""Avoid accidentally replacing the user's granted app with an ad-hoc build."""
from types import SimpleNamespace

import pytest

from scripts import build_macos


@pytest.mark.parametrize("arguments", [[], ["--install"], ["--development", "--install"]])
def test_unsigned_install_refused_before_build_or_copy(monkeypatch, arguments):
    monkeypatch.setenv("CTRLC_CODESIGN_IDENTITY", "")
    monkeypatch.setattr(build_macos.sys, "argv", ["build_macos.py", *arguments])
    monkeypatch.setattr(build_macos.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(build_macos.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(build_macos.subprocess, "run", lambda *args, **kwargs: pytest.fail("Must refuse before build/install"))
    with pytest.raises(SystemExit) as error:
        build_macos.main()
    assert error.value.code == 2


def test_unavailable_signing_identity_never_falls_back_to_adhoc(monkeypatch):
    monkeypatch.setattr(build_macos.sys, "argv", ["build_macos.py", "--identity", "missing", "--install"])
    monkeypatch.setattr(build_macos.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(build_macos.platform, "machine", lambda: "arm64")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="0 valid identities found", returncode=0)
    monkeypatch.setattr(build_macos.subprocess, "run", run)
    with pytest.raises(SystemExit):
        build_macos.main()
    assert calls == [["security", "find-identity", "-v", "-p", "codesigning"]]
