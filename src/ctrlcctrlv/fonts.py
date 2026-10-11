"""Use native fonts; also populate Qt's offscreen font database on Windows."""
import os
from pathlib import Path
import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


def prepare_fonts():
    app = QApplication.instance()
    if app and sys.platform == "win32" and app.platformName() == "offscreen":
        directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        families = set(QFontDatabase.families())
        for family, filename in (("Segoe UI", "segoeui.ttf"), ("Consolas", "consola.ttf"),
                                 ("Segoe UI Symbol", "seguisym.ttf")):
            if family not in families and (directory / filename).exists():
                QFontDatabase.addApplicationFont(str(directory / filename))
        if "Segoe UI" in QFontDatabase.families():
            app.setFont(QFont("Segoe UI"))


def monospace_font():
    prepare_fonts()
    families = set(QFontDatabase.families())
    for family in ("Consolas", "Menlo", "DejaVu Sans Mono"):
        if family in families:
            return QFont(family)
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
