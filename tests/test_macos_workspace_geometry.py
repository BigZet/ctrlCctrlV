"""Native macOS layout margins must not clip the Work controls."""
import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QStyleFactory

from ctrlcctrlv.settings import Settings
from ctrlcctrlv.workspace_ui import SettingsWindow


def test_native_macos_work_controls_fit_their_groups_after_resize():
    style = QStyleFactory.create("macOS")
    if style is None:
        pytest.skip("The native macOS style is unavailable")
    window = SettingsWindow(Settings())
    style.setParent(window)
    window.setStyle(style)
    window.show()
    try:
        for width in (960, 640, 820):
            window.resize(width, 660)
            for _ in range(10):
                QApplication.processEvents()
            combo = window.capture_format
            assert combo.parentWidget().rect().contains(combo.geometry())
            for control in (combo, window.quick_preview):
                row = window.work_format_row
                bounds = QRect(control.mapTo(row, control.rect().topLeft()), control.size())
                assert row.rect().contains(bounds)
                assert control.height() >= control.sizeHint().height()
    finally:
        window.close()
        window.deleteLater()
