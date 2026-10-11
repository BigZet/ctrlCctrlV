"""Build a staging app; install only a build signed by a stable certificate."""
import argparse
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="Сборка ctrlCctrlV для Mac ARM")
    parser.add_argument("--identity", default=os.environ.get("CTRLC_CODESIGN_IDENTITY"),
                        help="Имя или SHA1 сертификата code signing в Keychain")
    parser.add_argument("--development", action="store_true", help="Отдельная тестовая сборка с ad-hoc подписью")
    parser.add_argument("--install", action="store_true", help="Установить подписанную сборку в ~/Applications")
    parser.add_argument("--output", type=Path, default=Path("artifacts/builds"))
    args = parser.parse_args()
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
