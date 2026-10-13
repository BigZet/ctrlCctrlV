"""User-visible summaries, compact onboarding, and screen bounds."""
import json
import sys

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QWizard

from ctrlcctrlv.ui import DiagnosticsDialog, SetupWizard


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_diagnostics_details_start_collapsed_and_unknown_is_not_failure(app):
    dialog = DiagnosticsDialog()
    try:
        dialog.display(json.dumps({'system': 'Windows', 'architecture': 'AMD64',
                                   'provider': 'local', 'ocr_runtime': {'ready': None},
                                   'permissions': {'screen': None}, 'physical_memory_bytes': None}))
        app.processEvents()
        assert dialog.report.isHidden()
        assert 'неизвест' in dialog.summary.text().lower()
        assert 'не готов' not in dialog.summary.text().lower()
        assert 'Доступы' in dialog.summary.text()
        assert dialog.save_button.isVisible() and dialog.copy_button.isVisible()
        dialog.details_toggle.click()
        assert dialog.report.isVisible()
        assert dialog.report.toPlainText().startswith('{')
        dialog.display('{}')
        assert dialog.report.isHidden()
    finally:
        dialog.close()


def test_diagnostics_summary_names_known_model_and_denied_permission(app):
    dialog = DiagnosticsDialog()
    try:
        dialog.display(json.dumps({'system': 'Windows', 'architecture': 'AMD64', 'provider': 'local',
            'selected_local_model': 'lightonocr-3-0.8b', 'ocr_runtime': {'ready': False},
            'permissions': {'screen': False, 'accessibility': True, 'input_monitoring': True},
            'hotkeys_registered': True, 'physical_memory_bytes': 16 * 1024**3}))
        summary = dialog.summary.text()
        assert 'LightOnOCR' in summary
        assert 'не готов' in summary and 'захват экрана' in summary
        assert '16' in summary and 'ГБ' in summary
    finally:
        dialog.close()


@pytest.mark.parametrize('platform,shown,absent', [('win32', 'CUDA', 'Metal'), ('darwin', 'Metal', 'CUDA')])
def test_setup_acceleration_is_platform_specific_and_disclosed(app, monkeypatch, platform, shown, absent):
    monkeypatch.setattr(sys, 'platform', platform)
    wizard = SetupWizard()
    try:
        wizard.show()
        app.processEvents()
        assert wizard.acceleration.isHidden() or not wizard.acceleration.isVisible()
        wizard.hardware_toggle.click()
        assert wizard.acceleration.isVisible()
        values = ' '.join(wizard.acceleration.itemText(i) for i in range(wizard.acceleration.count()))
        assert shown in values and absent not in values
        wizard.next()
        assert wizard.currentPage().title() == 'Локальная модель'
        finish = wizard.button(QWizard.WizardButton.FinishButton)
        assert finish.property('role') == 'primary' and finish.isEnabled()
        assert wizard.setup_action is None
        wizard.back()
        wizard.provider.setCurrentIndex(1)
        wizard.next()
        assert wizard.currentPage().title() == 'Подключение OpenRouter'
        assert 'OpenRouter' in wizard.mode_description.text()
        assert not finish.isEnabled()
    finally:
        wizard.close()


@pytest.mark.parametrize('kind', [DiagnosticsDialog, SetupWizard])
def test_secondary_window_fits_available_small_screen_with_actions(app, monkeypatch, kind):
    import ctrlcctrlv.ui as ui
    from ctrlcctrlv.theme import fit_window_to_screen
    available = QRect(0, 0, 960, 500)
    monkeypatch.setattr(ui, 'fit_window_to_screen', lambda window: fit_window_to_screen(window, available))
    dialog = kind()
    try:
        if kind is DiagnosticsDialog:
            dialog.display('{}')
            action = dialog.copy_button
        else:
            dialog.show()
            dialog.next()
            action = dialog.button(QWizard.WizardButton.FinishButton)
        app.processEvents()
        assert available.contains(dialog.frameGeometry())
        assert dialog.rect().contains(QRect(action.mapTo(dialog, action.rect().topLeft()), action.size()))
    finally:
        dialog.close()
