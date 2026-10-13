import json
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWizard

from ctrlcctrlv.ui import DiagnosticsDialog, SelectionOverlay, SetupWizard
from ctrlcctrlv.ui_state import UiState


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("adjust", [False, True])
def test_real_overlay_retains_quick_capture_and_optional_enter(app, adjust):
    screen = app.primaryScreen()
    snapshot = QPixmap(screen.size())
    snapshot.fill(Qt.GlobalColor.white)
    overlay = SelectionOverlay(screen, snapshot, adjust=adjust, show_help=False)
    images = []
    overlay.selected.connect(images.append)
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(40, 40))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(79, 89))
    assert len(images) == (0 if adjust else 1)
    if adjust:
        QTest.keyClick(overlay, Qt.Key.Key_Return)
    assert images[0].width() == 40
    assert images[0].height() == 50
    overlay.close()


def test_onboarding_final_action_selects_download_and_has_model_size(app):
    wizard = SetupWizard()
    wizard.show()
    wizard.next()
    assert wizard.setup_action is None
    assert wizard.local_model.currentData()
    assert "ГБ" in wizard.local_model.currentText()
    assert wizard.button(QWizard.WizardButton.FinishButton).text() == "Скачать модель"
    wizard.button(QWizard.WizardButton.FinishButton).click()
    assert wizard.setup_action == "download"
    assert wizard.result() == QWizard.DialogCode.Accepted


def test_cloud_onboarding_requires_key_and_only_connects_on_finish(app):
    wizard = SetupWizard()
    wizard.provider.setCurrentIndex(1)
    wizard.show()
    wizard.next()
    finish = wizard.button(QWizard.WizardButton.FinishButton)
    assert not finish.isEnabled()
    assert wizard.setup_action is None
    wizard.key.setText("example-test-key")
    assert finish.isEnabled()
    assert not wizard.key.isHidden()
    assert wizard.local_model.isHidden()
    finish.click()
    assert wizard.setup_action == "connect"
    assert wizard.key.text() == "example-test-key"


def test_diagnostics_summary_and_json_save_remember_directory(app, tmp_path, monkeypatch):
    state = UiState()
    report = {"application": "1.0", "system": "Windows", "architecture": "AMD64",
              "provider": "local", "ocr_runtime": {"state": "stopped", "ready": False},
              "permissions": {"screen": True, "accessibility": False}, "hotkeys_registered": True}
    dialog = DiagnosticsDialog(ui_state=state)
    dialog.display(json.dumps(report))
    assert "Windows" in dialog.summary.text()
    assert "не готов" in dialog.summary.text().lower()
    path = tmp_path / "diagnostics.json"
    monkeypatch.setattr("ctrlcctrlv.ui.QFileDialog.getSaveFileName", lambda *a, **k: (str(path), "JSON (*.json)"))
    dialog.save_button.click()
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert state.get("last_save_directory") == str(tmp_path)
    dialog.close()


def test_cancel_diagnostics_save_preserves_report_and_directory(app, monkeypatch):
    state = UiState()
    dialog = DiagnosticsDialog(ui_state=state)
    report = '{"system":"Windows"}'
    dialog.display(report)
    monkeypatch.setattr("ctrlcctrlv.ui.QFileDialog.getSaveFileName", lambda *a, **k: ("", ""))
    dialog.save_report()
    assert dialog.report.toPlainText() == report
    assert state.get("last_save_directory") == ""
    dialog.close()


@pytest.mark.parametrize('report', ['', 'Синтетический повреждённый JSON', '["unexpected structure"]'])
def test_malformed_diagnostics_remain_exportable_as_valid_json(app, tmp_path, monkeypatch, report):
    dialog = DiagnosticsDialog()
    dialog.display(report)
    path = tmp_path / 'error-report.json'
    monkeypatch.setattr('ctrlcctrlv.ui.QFileDialog.getSaveFileName',
                        lambda *a, **k: (str(path), 'JSON (*.json)'))
    try:
        assert dialog.save_button.isEnabled()
        dialog.save_button.click()
        saved = json.loads(path.read_text(encoding='utf-8'))
        assert saved == {'diagnostics_error': 'invalid_report', 'report': report}
        assert dialog.report.toPlainText() == report
        assert 'Не удалось' in dialog.summary.text()
    finally:
        dialog.close()


def test_failed_diagnostics_save_keeps_previous_directory_and_report(app, tmp_path, monkeypatch):
    state = UiState()
    state.set(last_save_directory=str(tmp_path))
    dialog = DiagnosticsDialog(ui_state=state)
    report = '{"system":"Windows"}'
    dialog.display(report)
    monkeypatch.setattr("ctrlcctrlv.ui.QFileDialog.getSaveFileName", lambda *a, **k:
                        (str(tmp_path / "absent" / "report.json"), "JSON (*.json)"))
    warnings = []
    monkeypatch.setattr("ctrlcctrlv.ui.QMessageBox.warning", lambda *args: warnings.append(args))
    dialog.save_report()
    assert warnings
    assert dialog.report.toPlainText() == report
    assert state.get("last_save_directory") == str(tmp_path)
    dialog.close()


def test_quick_mode_hides_seen_help_but_adjustment_still_has_instructions(app):
    screen = app.primaryScreen()
    snapshot = QPixmap(screen.size())
    snapshot.fill(Qt.GlobalColor.white)
    quick = SelectionOverlay(screen, snapshot, show_help=False)
    assisted = SelectionOverlay(screen, snapshot, adjust=True, show_help=False)
    quick_image = quick.grab().toImage()
    assisted_image = assisted.grab().toImage()
    center_x = quick.width() // 2
    assert quick_image.pixelColor(center_x, 25) == quick_image.pixelColor(center_x, 100)
    assert assisted_image.pixelColor(center_x, 25) != assisted_image.pixelColor(center_x, 100)
    quick.close()
    assisted.close()


def test_deferring_onboarding_does_not_request_setup_action(app):
    wizard = SetupWizard()
    wizard.show()
    wizard.next()
    wizard.reject()
    assert wizard.setup_action is None
    assert wizard.result() == QWizard.DialogCode.Rejected
