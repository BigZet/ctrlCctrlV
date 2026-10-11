"""Build a portable x64 folder and ZIP, with a verified frozen smoke run."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid


def publish_archive(source, final):
    try:
        source.replace(final)
    except PermissionError:
        # Explorer or a preview can keep a ZIP open. Publish a fresh artifact
        # without deleting or overwriting the archive currently being used.
        final = final.with_name(final.stem + "-" + uuid.uuid4().hex[:8] + final.suffix)
        source.replace(final)
    return final


def main():
    parser = argparse.ArgumentParser(description="Сборка ctrlCctrlV для Windows x64")
    parser.add_argument("--output", type=Path, default=Path("artifacts/builds/windows"))
    parser.add_argument("--console", action="store_true", help="Диагностическая сборка с консолью")
    args = parser.parse_args()
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        parser.error("Сборка должна выполняться на Windows x64")
    if sys.maxsize <= 2 ** 32:
        parser.error("Используйте Python x64")
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
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    checksum = final.with_suffix(".sha256")
    temporary = checksum.with_suffix(".sha256.tmp")
    temporary.write_text(digest + "  " + final.name + "\n", encoding="utf-8")
    temporary.replace(checksum)
    print(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
