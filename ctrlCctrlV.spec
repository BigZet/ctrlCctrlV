# Build on the target Mac: .venv/bin/python -m PyInstaller --noconfirm ctrlCctrlV.spec
import os
from PyInstaller.utils.hooks import collect_submodules

signing_identity = os.environ.get("CTRLC_CODESIGN_IDENTITY") or None

hidden = []
for package in ("Quartz", "ApplicationServices", "keyring.backends"):
    hidden += collect_submodules(package)

a = Analysis(
    ["scripts/launch.py"], pathex=["src"],
    binaries=[], datas=[("THIRD_PARTY.md", "."), ("src/ctrlcctrlv/assets", "ctrlcctrlv/assets")],
    hiddenimports=hidden,
    excludes=["pytest", "tkinter", "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="ctrlCctrlV", debug=False, strip=False, upx=False, console=False,
    target_arch="arm64", codesign_identity=signing_identity, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ctrlCctrlV")
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
