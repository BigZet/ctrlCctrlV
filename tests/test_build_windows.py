from pathlib import Path
import hashlib
import subprocess

import pytest

from scripts import build_windows
from scripts.build_windows import publish_archive


def test_open_archive_is_preserved_and_new_artifact_path_is_returned(tmp_path, monkeypatch):
    old = tmp_path / "release.zip"
    new = tmp_path / "staged.zip"
    old.write_bytes(b"currently open archive")
    new.write_bytes(b"new verified archive")
    replace = Path.replace
    def locked_replace(source, target):
        if Path(target) == old:
            raise PermissionError("archive is open")
        return replace(source, target)
    monkeypatch.setattr(Path, "replace", locked_replace)
    published = publish_archive(new, old)
    assert published != old and published.parent == old.parent
    assert published.read_bytes() == b"new verified archive"
    assert old.read_bytes() == b"currently open archive"
    assert not new.exists()


def test_installer_is_versioned_and_checked_after_compilation(tmp_path, monkeypatch):
    folder = tmp_path / "dist with spaces" / "ctrlCctrlV"
    folder.mkdir(parents=True)
    (folder / "ctrlCctrlV.exe").write_bytes(b"verified application")
    output = tmp_path / "release"
    output.mkdir()
    compiler = tmp_path / "Inno Setup 6" / "ISCC.exe"

    def compile_installer(command, **kwargs):
        assert command[0] == str(compiler)
        assert "/DAppVersion=1.2.3" in command
        assert f"/DSourceDir={folder}" in command
        assert command[-1].endswith("windows_installer.iss")
        assert kwargs["check"] is True
        staging = Path(next(value[2:] for value in command if value.startswith("/O")))
        name = next(value[2:] for value in command if value.startswith("/F"))
        (staging / (name + ".exe")).write_bytes(b"compiled installer")

    monkeypatch.setattr(subprocess, "run", compile_installer)
    artifact = build_windows.build_installer(folder, output, "1.2.3", compiler)
    assert artifact == output / "ctrlCctrlV-1.2.3-windows-x64-setup.exe"
    assert artifact.read_bytes() == b"compiled installer"
    assert artifact.with_suffix(".sha256").read_text() == (
        hashlib.sha256(b"compiled installer").hexdigest() + "  " + artifact.name + "\n"
    )


def test_failed_compilation_preserves_previous_installer(tmp_path, monkeypatch):
    folder = tmp_path / "dist"
    folder.mkdir()
    (folder / "ctrlCctrlV.exe").write_bytes(b"application")
    previous = tmp_path / "ctrlCctrlV-1.2.3-windows-x64-setup.exe"
    previous.write_bytes(b"previous installer")

    def failed_compile(command, **kwargs):
        raise subprocess.CalledProcessError(2, command)

    monkeypatch.setattr(subprocess, "run", failed_compile)
    with pytest.raises(subprocess.CalledProcessError):
        build_windows.build_installer(folder, tmp_path, "1.2.3", Path("ISCC.exe"))
    assert previous.read_bytes() == b"previous installer"


@pytest.mark.parametrize("version", ["1.2", "1.2.3-rc.1", "../1.2.3", '1.2.3\"'])
def test_unsupported_installer_versions_fail_before_compilation(tmp_path, version):
    with pytest.raises(ValueError, match="version"):
        build_windows.build_installer(tmp_path, tmp_path, version, Path("ISCC.exe"))


def test_installer_verification_refuses_to_replace_existing_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(build_windows, "existing_installation", lambda: True)
    with pytest.raises(RuntimeError, match="existing installation"):
        build_windows.verify_installer(tmp_path / "setup.exe")


def test_compiler_lookup_supports_standard_windows_install(tmp_path, monkeypatch):
    program_files = tmp_path / "Program Files (x86)"
    compiler = program_files / "Inno Setup 6" / "ISCC.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"compiler")
    monkeypatch.setattr(build_windows.shutil, "which", lambda _: None)
    monkeypatch.setenv("ProgramFiles(x86)", str(program_files))
    monkeypatch.delenv("ProgramFiles", raising=False)
    assert build_windows.find_inno_compiler() == compiler
