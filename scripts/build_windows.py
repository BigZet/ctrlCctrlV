"""Build a verified portable x64 ZIP and optional per-user Windows installer."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid


UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ctrlCctrlV.Desktop_is1"


def publish_archive(source, final):
    try:
        source.replace(final)
    except PermissionError:
        # Explorer or a preview can keep a ZIP open. Publish a fresh artifact
        # without deleting or overwriting the archive currently being used.
        final = final.with_name(final.stem + "-" + uuid.uuid4().hex[:8] + final.suffix)
        source.replace(final)
    return final


def write_checksum(artifact):
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = artifact.with_suffix(".sha256")
    temporary = checksum.with_suffix(".sha256.tmp")
    temporary.write_text(digest + "  " + artifact.name + "\n", encoding="utf-8")
    temporary.replace(checksum)


def find_inno_compiler():
    executable = shutil.which("ISCC.exe")
    if executable:
        return Path(executable)
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        if location := os.environ.get(variable):
            candidate = Path(location) / "Inno Setup 6" / "ISCC.exe"
            if candidate.is_file():
                return candidate
    raise FileNotFoundError("Install Inno Setup 6 (6.3 or newer), or add ISCC.exe to PATH")


def build_installer(folder, output, version, compiler):
    if (not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", version)
            or any(int(part) > 65535 for part in version.split("."))):
        raise ValueError("Installer version must contain three or four numbers from 0 to 65535")
    if not (folder / "ctrlCctrlV.exe").is_file():
        raise FileNotFoundError("The verified ctrlCctrlV.exe build is missing")
    name = f"ctrlCctrlV-{version}-windows-x64-setup"
    script = Path(__file__).with_name("windows_installer.iss")
    with tempfile.TemporaryDirectory(dir=output, prefix="installer-") as staging:
        subprocess.run([
            str(compiler), "/Qp", f"/DAppVersion={version}",
            f"/DSourceDir={folder.resolve()}", f"/O{staging}", f"/F{name}", str(script),
        ], check=True, timeout=600)
        artifact = publish_archive(Path(staging) / f"{name}.exe", output / f"{name}.exe")
    write_checksum(artifact)
    return artifact


def existing_installation():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except FileNotFoundError:
        return False


def verify_installer(installer):
    """Exercise install, repair/upgrade, frozen smoke and uninstall on a clean runner."""
    if existing_installation():
        raise RuntimeError("Installer verification refuses to replace an existing installation")
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    with tempfile.TemporaryDirectory(prefix="ctrlcctrlv-installer-check-") as staging:
        root = Path(staging)
        application = root / "installed"
        data = root / "profile"
        data.mkdir()
        sentinel = data / "preserve-user-data.txt"
        sentinel.write_text("Installer verification data", encoding="utf-8")
        uninstall = application / "unins000.exe"
        try:
            for attempt in range(2):
                subprocess.run([
                    str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                    f"/DIR={application}", f"/LOG={root / f'install-{attempt}.log'}",
                ], check=True, timeout=180)
                subprocess.run([
                    str(application / "ctrlCctrlV.exe"), "--smoke", "--smoke-credentials",
                    "--data-dir", str(data),
                ], env=environment, check=True, timeout=45)
        finally:
            if uninstall.exists():
                subprocess.run([
                    str(uninstall), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/LOG={root / 'uninstall.log'}",
                ], check=True, timeout=180)
        if (application / "ctrlCctrlV.exe").exists() or existing_installation():
            raise RuntimeError("The installer verification left application files or registration behind")
        if sentinel.read_text(encoding="utf-8") != "Installer verification data":
            raise RuntimeError("Uninstall did not preserve the application's separate data directory")
    print("Installer install, repair, smoke, uninstall and data preservation verified")


def main():
    parser = argparse.ArgumentParser(description="Сборка ctrlCctrlV для Windows x64")
    parser.add_argument("--output", type=Path, default=Path("artifacts/builds/windows"))
    parser.add_argument("--console", action="store_true", help="Диагностическая сборка с консолью")
    parser.add_argument("--installer", action="store_true", help="Создать Setup.exe через Inno Setup 6")
    parser.add_argument("--verify-installer", action="store_true",
                        help="Проверить установку и удаление на машине без установленной ctrlCctrlV")
    args = parser.parse_args()
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        parser.error("Сборка должна выполняться на Windows x64")
    if sys.maxsize <= 2 ** 32:
        parser.error("Используйте Python x64")
    if args.verify_installer and not args.installer:
        parser.error("--verify-installer требует --installer")
    compiler = find_inno_compiler() if args.installer else None
    repository = Path(__file__).resolve().parent.parent
    output = args.output.resolve()
    build_environment = os.environ.copy()
    build_environment.pop("CTRLC_BUILD_CONSOLE", None)
    if args.console:
        build_environment["CTRLC_BUILD_CONSOLE"] = "1"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(output / "dist"),
                    "--workpath", str(output / "work"), str(repository / "ctrlCctrlV.spec")],
                   cwd=repository, env=build_environment, check=True)
    folder = output / "dist/ctrlCctrlV"
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    with tempfile.TemporaryDirectory(prefix="ctrlcctrlv-build-smoke-") as data:
        subprocess.run([str(folder / "ctrlCctrlV.exe"), "--smoke", "--smoke-credentials", "--data-dir", data],
                       env=environment, check=True, timeout=45)
    output.mkdir(parents=True, exist_ok=True)
    final = output / "ctrlCctrlV-windows-x64.zip"
    with tempfile.TemporaryDirectory(dir=output, prefix="package-") as staging:
        archive = Path(shutil.make_archive(str(Path(staging) / final.stem), "zip",
                                          root_dir=folder.parent, base_dir=folder.name))
        final = publish_archive(archive, final)
    write_checksum(final)
    print(final)
    if compiler:
        with (repository / "pyproject.toml").open("rb") as project:
            version = tomllib.load(project)["project"]["version"]
        installer = build_installer(folder, output, version, compiler)
        if args.verify_installer:
            verify_installer(installer)
        print(installer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
