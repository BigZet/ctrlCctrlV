"""Build an app and optional DMG; auto-install only a certificate-signed build."""
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


INSTALL_INSTRUCTIONS = """ctrlCctrlV — установка на Mac Apple Silicon (macOS 14+)

1. Перетащите ctrlCctrlV.app на значок Applications.
2. Извлеките этот образ и откройте ctrlCctrlV из папки «Программы».
3. Если macOS блокирует запуск, проверьте источник загрузки. Для доверенной
   сборки используйте «Системные настройки → Конфиденциальность и безопасность
   → Всё равно открыть» после первой попытки запуска.
4. Разрешите приложению запись экрана для OCR, универсальный доступ для ввода
   текста и мониторинг ввода для глобальных сочетаний клавиш. После изменения
   разрешений macOS может попросить перезапустить приложение.
5. Выберите и скачайте OCR-модель в приложении. Модели не включены в установщик.

Этот образ не нотарифицирован Apple. {signing_note}
При обновлении закройте приложение перед заменой. Для новой ad-hoc сборки
может потребоваться заново добавить приложение в списки системных разрешений.

English

1. Drag ctrlCctrlV.app to Applications.
2. Eject this disk image and launch ctrlCctrlV from Applications.
3. This disk image is not notarized by Apple. If macOS blocks a trusted download,
   use System Settings > Privacy & Security > Open Anyway after trying to launch.
4. Grant Screen Recording for OCR, Accessibility for typing, and Input Monitoring
   for global hotkeys. Restart the app when macOS requests it.
5. Download an OCR model in the app; models are not bundled with this installer.

Quit the app before replacing it during an update. An updated ad-hoc build may
need to be removed and added again in the macOS permission lists.
"""


def project_version(repository: Path) -> str:
    """Read the version used for release names from the package metadata."""
    with (repository / "pyproject.toml").open("rb") as source:
        version = tomllib.load(source)["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]*", version):
        raise ValueError("Некорректная версия в pyproject.toml")
    return version


def create_dmg(app: Path, version: str, *, development: bool) -> tuple[Path, Path]:
    """Write a compressed installer and checksum only after image verification."""
    destination = app.parent / f"ctrlCctrlV-{version}-macos-arm64.dmg"
    checksum = destination.with_suffix(".dmg.sha256")
    with tempfile.TemporaryDirectory(prefix=".ctrlcctrlv-dmg-", dir=app.parent) as temporary:
        workspace = Path(temporary)
        stage = workspace / "contents"
        stage.mkdir()
        subprocess.run(["ditto", str(app), str(stage / app.name)], check=True)
        (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
        signing_note = (
            "Приложение имеет локальную ad-hoc подпись, без сертификата Developer ID."
            if development else "Приложение подписано указанным при сборке сертификатом."
        )
        (stage / "INSTALL.txt").write_text(
            INSTALL_INSTRUCTIONS.format(signing_note=signing_note), encoding="utf-8"
        )
        image = workspace / destination.name
        subprocess.run([
            "hdiutil", "create", "-volname", f"ctrlCctrlV {version}", "-srcfolder", str(stage),
            "-format", "UDZO", "-fs", "HFS+", str(image),
        ], check=True)
        subprocess.run(["hdiutil", "verify", str(image)], check=True)
        with image.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        checksum_file = workspace / checksum.name
        checksum_file.write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
        image.replace(destination)
        checksum_file.replace(checksum)
    return destination, checksum


def verify_dmg(image: Path) -> None:
    """Smoke-test an installed copy without touching Applications or user data."""
    workspace = Path(tempfile.mkdtemp(prefix="ctrlcctrlv-dmg-check-"))
    mount = workspace / "volume"
    mount.mkdir()
    mounted = False
    try:
        subprocess.run([
            "hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(image),
        ], check=True)
        mounted = True
        if (mount / "Applications").readlink() != Path("/Applications"):
            raise ValueError("В DMG отсутствует правильная ссылка Applications")
        if not (mount / "INSTALL.txt").is_file():
            raise ValueError("В DMG отсутствует инструкция INSTALL.txt")
        app = workspace / "installed" / "ctrlCctrlV.app"
        app.parent.mkdir()
        subprocess.run(["ditto", str(mount / app.name), str(app)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        subprocess.run([
            str(app / "Contents/MacOS/ctrlCctrlV"), "--smoke", "--data-dir", str(workspace / "profile"),
        ], env=environment, check=True, timeout=180)
    finally:
        if mounted:
            detached = subprocess.run(["hdiutil", "detach", str(mount)], check=False)
            if detached.returncode:
                # The volume is read-only and only this verification process uses it.
                # Do not remove the temporary directory while it is still mounted.
                subprocess.run(["hdiutil", "detach", "-force", str(mount)], check=True)
        shutil.rmtree(workspace)


def main():
    parser = argparse.ArgumentParser(description="Сборка ctrlCctrlV для Mac ARM")
    parser.add_argument("--identity", default=os.environ.get("CTRLC_CODESIGN_IDENTITY"),
                        help="Имя или SHA1 сертификата code signing в Keychain")
    parser.add_argument("--development", action="store_true", help="Отдельная тестовая сборка с ad-hoc подписью")
    parser.add_argument("--install", action="store_true", help="Установить подписанную сборку в ~/Applications")
    parser.add_argument("--dmg", action="store_true", help="Создать установочный DMG и файл SHA-256 рядом с .app")
    parser.add_argument("--verify-dmg", action="store_true", help="С --dmg: проверить установку во временную папку и запуск")
    parser.add_argument("--output", type=Path, default=Path("artifacts/builds"))
    args = parser.parse_args()
    if args.verify_dmg and not args.dmg:
        parser.error("--verify-dmg требует --dmg")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("Сборка предназначена для Mac Apple Silicon")
    if args.development and (args.identity or args.install):
        parser.error("Тестовую ad-hoc сборку нельзя устанавливать вместо приложения или подписывать сертификатом")
    if not args.development and not args.identity:
        parser.error("Нужен --identity для постоянной подписи; для отдельной тестовой сборки используйте --development")
    if args.identity:
        result = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"],
                                capture_output=True, text=True, check=True)
        identities = re.findall(r'\d+\) ([0-9a-fA-F]{40}) "([^"]+)"', result.stdout)
        if args.identity not in {value for pair in identities for value in pair}:
            parser.error("Указанный сертификат подписи недоступен; сборка не заменит установленное приложение")
    repository = Path(__file__).resolve().parent.parent
    output = args.output.resolve()
    environment = os.environ.copy()
    environment.pop("CTRLC_CODESIGN_IDENTITY", None)
    if args.identity:
        environment["CTRLC_CODESIGN_IDENTITY"] = args.identity
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(output / "dist"),
                    "--workpath", str(output / "work"), "ctrlCctrlV.spec"],
                   cwd=repository, env=environment, check=True)
    app = output / "dist" / "ctrlCctrlV.app"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    if args.dmg:
        image, checksum = create_dmg(app, project_version(repository), development=args.development)
        print(image)
        print(checksum)
        if args.verify_dmg:
            verify_dmg(image)
    if args.install:
        destination = Path.home() / "Applications" / app.name
        # Do not replace a running executable or invalidate its dynamic signature.
        processes = subprocess.run(["pgrep", "-f", str(destination / "Contents/MacOS/ctrlCctrlV")],
                                   capture_output=True, text=True)
        if processes.returncode == 0:
            parser.error("Закройте установленное приложение перед обновлением; новая сборка сохранена отдельно")
        if processes.returncode != 1:
            parser.error("Не удалось проверить запущенное приложение; новая сборка сохранена отдельно")
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ditto", str(app), str(destination)], check=True)
        print(destination)
    else:
        print(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
