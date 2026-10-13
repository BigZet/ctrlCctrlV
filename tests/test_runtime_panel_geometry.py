"""Expanded computation controls use free space before requiring scrolling."""
import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QPushButton

from ctrlcctrlv.settings import Settings
from ctrlcctrlv.workspace_ui import SettingsWindow


@pytest.mark.parametrize('details', ['none', 'before_show', 'after_show'])
def test_computations_show_hardware_action_when_window_has_space(monkeypatch, details):
    from ctrlcctrlv import workspace_ui
    from ctrlcctrlv.theme import fit_window_to_screen
    monkeypatch.setattr(workspace_ui, 'fit_window_to_screen',
                        lambda window: fit_window_to_screen(window, QRect(0, 0, 1200, 900)))
    window = SettingsWindow(Settings())
    window.resize(1040, 740)
    window.navigation.setCurrentRow(1)
    detail_text = 'Windows AMD64 · NVIDIA GPU\nСвободно 8 ГБ VRAM\nМодель помещается в память'
    if details == 'before_show':
        window.hardware_info.setText(detail_text)
        window.hardware_info.show()
    window.runtime_toggle.setChecked(True)
    window.show()
    for _ in range(3):
        QApplication.processEvents()
    if details == 'after_show':
        window.hardware_info.setText(detail_text)
        window.hardware_info.show()
        for _ in range(3):
            QApplication.processEvents()
    try:
        panel = window.runtime_scroll
        action = next(button for button in panel.findChildren(QPushButton)
                      if button.text() == 'Проверить оборудование')
        action_rect = QRect(action.mapTo(panel.viewport(), action.rect().topLeft()), action.size())
        assert panel.viewport().rect().contains(action_rect), (panel.height(), action_rect)
        assert panel.verticalScrollBar().maximum() == 0
        assert window.model_table.verticalScrollBar().maximum() == 0
    finally:
        window.close()


def test_computations_remain_scrollable_on_short_screen(monkeypatch):
    from ctrlcctrlv import workspace_ui
    from ctrlcctrlv.theme import fit_window_to_screen
    available = QRect(0, 0, 960, 500)
    monkeypatch.setattr(workspace_ui, 'fit_window_to_screen',
                        lambda window: fit_window_to_screen(window, available))
    window = SettingsWindow(Settings())
    window.navigation.setCurrentRow(1)
    window.runtime_toggle.setChecked(True)
    window.hardware_info.setText('Проверка оборудования\n' * 12)
    window.hardware_info.show()
    window.show()
    for _ in range(3):
        QApplication.processEvents()
    try:
        panel = window.runtime_scroll
        action = next(button for button in panel.findChildren(QPushButton)
                      if button.text() == 'Проверить оборудование')
        panel.ensureWidgetVisible(action)
        QApplication.processEvents()
        action_rect = QRect(action.mapTo(panel.viewport(), action.rect().topLeft()), action.size())
        assert panel.viewport().rect().contains(action_rect)
        assert window.rect().contains(QRect(window.download_all.mapTo(window, window.download_all.rect().topLeft()), window.download_all.size()))
        assert available.contains(window.frameGeometry())
    finally:
        window.close()
