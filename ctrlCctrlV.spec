# Build on the target OS; no cross compilation.
import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_all

signing_identity = os.environ.get("CTRLC_CODESIGN_IDENTITY") or None

hidden = []
format_datas, format_binaries, format_hidden = collect_all("clang_format")
hidden += format_hidden
hidden += collect_submodules("black")
hidden += collect_submodules("pygments.lexers")
for package in (("Quartz", "ApplicationServices", "keyring.backends") if sys.platform == "darwin" else ("keyring.backends",)):
    hidden += collect_submodules(package)

a = Analysis(
    ["scripts/launch.py"], pathex=["src"],
    binaries=format_binaries, datas=format_datas + [("THIRD_PARTY.md", "."), ("src/ctrlcctrlv/assets", "ctrlcctrlv/assets")],
    hiddenimports=hidden,
    excludes=["pytest", "tkinter", "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest"],
)
if sys.platform == "win32":
    # Windows 11 provides UCRT, ICU and API-set contracts. A developer's PATH may
    # contain older copies from unrelated applications; bundling those can
    # break QtCore before the GUI starts (WinError 127).
    a.binaries = [entry for entry in a.binaries
                  if os.path.basename(entry[0]).lower() not in {"ucrtbase.dll", "icuuc.dll", "icuin.dll"}
                  and not os.path.basename(entry[0]).lower().startswith(("api-ms-win-", "ext-ms-win-", "icudt"))]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="ctrlCctrlV", debug=False, strip=False, upx=False,
    console=os.environ.get("CTRLC_BUILD_CONSOLE") == "1",
    target_arch="arm64" if sys.platform == "darwin" else None,
    codesign_identity=signing_identity if sys.platform == "darwin" else None, entitlements_file=None,
    icon="src/ctrlcctrlv/assets/app.icns" if sys.platform == "darwin" else "src/ctrlcctrlv/assets/app.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ctrlCctrlV")
if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="ctrlCctrlV.app", bundle_identifier="local.ctrlcctrlv.desktop",
        icon="src/ctrlcctrlv/assets/app.icns",
        info_plist={
            "CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "14.0", "LSUIElement": False,
            "NSHighResolutionCapable": True,
            "NSScreenCaptureUsageDescription": "Выделение области экрана для распознавания текста.",
            "NSInputMonitoringUsageDescription": "Глобальные сочетания клавиш для OCR и остановки ввода.",
        },
    )
