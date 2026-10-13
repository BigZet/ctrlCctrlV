"""Keep macOS distribution packaging reproducible without installing the app."""
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import build_macos


def test_dmg_contains_app_applications_link_and_install_help(tmp_path, monkeypatch):
    app = tmp_path / "ctrlCctrlV.app"
    app.mkdir()
    calls = []
    image_bytes = b"verified compressed disk image"

    def run(command, **kwargs):
        assert kwargs["check"] is True
        calls.append(command)
        if command[0] == "ditto":
            Path(command[2]).mkdir()
        elif command[:2] == ["hdiutil", "create"]:
            stage = Path(command[command.index("-srcfolder") + 1])
            assert (stage / app.name).is_dir()
            assert (stage / "Applications").readlink() == Path("/Applications")
            instructions = (stage / "INSTALL.txt").read_text(encoding="utf-8")
            assert "не нотарифицирован Apple" in instructions
            assert "ad-hoc" in instructions
            assert "не включены в установщик" in instructions
            assert "xattr" not in instructions
            Path(command[-1]).write_bytes(image_bytes)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_macos.subprocess, "run", run)
    image, checksum = build_macos.create_dmg(app, "0.1.1", development=True)

    assert image.name == "ctrlCctrlV-0.1.1-macos-arm64.dmg"
    assert image.read_bytes() == image_bytes
    assert checksum.read_text() == f"{hashlib.sha256(image_bytes).hexdigest()}  {image.name}\n"
    assert [call[:2] for call in calls][1:] == [["hdiutil", "create"], ["hdiutil", "verify"]]
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([app.name, image.name, checksum.name])


@pytest.mark.parametrize("failed_step", ["create", "verify"])
def test_failed_dmg_never_replaces_existing_release_and_cleans_staging(tmp_path, monkeypatch, failed_step):
    app = tmp_path / "ctrlCctrlV.app"
    app.mkdir()
    previous_image = tmp_path / "ctrlCctrlV-0.1.1-macos-arm64.dmg"
    previous_checksum = previous_image.with_suffix(".dmg.sha256")
    previous_image.write_bytes(b"previous release")
    previous_checksum.write_text("previous checksum")

    def run(command, **kwargs):
        if command[:2] == ["hdiutil", "create"]:
            Path(command[-1]).write_bytes(b"incomplete image")
        if command[:2] == ["hdiutil", failed_step]:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_macos.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        build_macos.create_dmg(app, "0.1.1", development=True)

    assert previous_image.read_bytes() == b"previous release"
    assert previous_checksum.read_text() == "previous checksum"
    assert not list(tmp_path.glob(".ctrlcctrlv-dmg-*"))


def test_release_filename_version_comes_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3rc1"\n')
    assert build_macos.project_version(tmp_path) == "1.2.3rc1"


def test_unsafe_release_filename_version_is_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "../outside"\n')
    with pytest.raises(ValueError, match="Некорректная версия"):
        build_macos.project_version(tmp_path)


def test_development_dmg_verifies_signature_before_packaging_and_never_installs(tmp_path, monkeypatch):
    monkeypatch.delenv("CTRLC_CODESIGN_IDENTITY", raising=False)
    monkeypatch.setattr(build_macos.sys, "argv", [
        "build_macos.py", "--development", "--dmg", "--output", str(tmp_path),
    ])
    monkeypatch.setattr(build_macos.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(build_macos.platform, "machine", lambda: "arm64")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    def create_dmg(app, version, *, development):
        assert calls[-1] == ["codesign", "--verify", "--deep", "--strict", str(app)]
        assert app == tmp_path / "dist" / "ctrlCctrlV.app"
        assert version == "1.2.3"
        assert development is True
        return tmp_path / "release.dmg", tmp_path / "release.dmg.sha256"

    monkeypatch.setattr(build_macos.subprocess, "run", run)
    monkeypatch.setattr(build_macos, "project_version", lambda repository: "1.2.3")
    monkeypatch.setattr(build_macos, "create_dmg", create_dmg)
    assert build_macos.main() == 0
    assert len(calls) == 2
    assert calls[0][:3] == [build_macos.sys.executable, "-m", "PyInstaller"]


def test_bad_signature_prevents_dmg_distribution(tmp_path, monkeypatch):
    monkeypatch.delenv("CTRLC_CODESIGN_IDENTITY", raising=False)
    monkeypatch.setattr(build_macos.sys, "argv", [
        "build_macos.py", "--development", "--dmg", "--output", str(tmp_path),
    ])
    monkeypatch.setattr(build_macos.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(build_macos.platform, "machine", lambda: "arm64")

    def run(command, **kwargs):
        if command[0] == "codesign":
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_macos.subprocess, "run", run)
    monkeypatch.setattr(build_macos, "create_dmg", lambda *args, **kwargs: pytest.fail("Invalid app must not ship"))
    with pytest.raises(subprocess.CalledProcessError):
        build_macos.main()


@pytest.mark.parametrize("fail_smoke", [False, True])
def test_mounted_dmg_checks_copied_app_and_always_detaches(tmp_path, monkeypatch, fail_smoke):
    workspace = tmp_path / "check"
    workspace.mkdir()
    monkeypatch.setattr(build_macos.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["hdiutil", "attach"]:
            assert "-readonly" in command
            assert "-nobrowse" in command
            mount = Path(command[command.index("-mountpoint") + 1])
            (mount / "Applications").symlink_to("/Applications", target_is_directory=True)
            (mount / "INSTALL.txt").write_text("instructions")
        elif "--smoke" in command:
            assert Path(command[0]) == workspace / "installed/ctrlCctrlV.app/Contents/MacOS/ctrlCctrlV"
            assert command[-2:] == ["--data-dir", str(workspace / "profile")]
            assert kwargs["env"]["QT_QPA_PLATFORM"] == "offscreen"
            assert kwargs["timeout"] == 180
            if fail_smoke:
                raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_macos.subprocess, "run", run)
    if fail_smoke:
        with pytest.raises(subprocess.CalledProcessError):
            build_macos.verify_dmg(tmp_path / "release.dmg")
    else:
        build_macos.verify_dmg(tmp_path / "release.dmg")
    assert calls[1] == ["ditto", str(workspace / "volume/ctrlCctrlV.app"), str(workspace / "installed/ctrlCctrlV.app")]
    assert calls[2] == ["codesign", "--verify", "--deep", "--strict", str(workspace / "installed/ctrlCctrlV.app")]
    assert calls[-1] == ["hdiutil", "detach", str(workspace / "volume")]
    assert not workspace.exists()


def test_dmg_check_does_not_remove_volume_if_detach_fails(tmp_path, monkeypatch):
    workspace = tmp_path / "check"
    workspace.mkdir()
    monkeypatch.setattr(build_macos.tempfile, "mkdtemp", lambda **kwargs: str(workspace))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["hdiutil", "detach"]:
            if "-force" in command:
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(returncode=1)
        # A missing Applications link causes verification to fail after attaching.
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(build_macos.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        build_macos.verify_dmg(tmp_path / "release.dmg")
    assert calls[-1] == ["hdiutil", "detach", "-force", str(workspace / "volume")]
    assert workspace.exists()


def test_verify_dmg_requires_dmg_creation(monkeypatch):
    monkeypatch.setattr(build_macos.sys, "argv", ["build_macos.py", "--development", "--verify-dmg"])
    monkeypatch.setattr(build_macos.subprocess, "run", lambda *args, **kwargs: pytest.fail("Invalid flags must fail before build"))
    with pytest.raises(SystemExit) as error:
        build_macos.main()
    assert error.value.code == 2
