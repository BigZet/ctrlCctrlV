from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QScrollArea
from PySide6.QtTest import QTest

from ctrlcctrlv.settings import Settings
from ctrlcctrlv.model_picker import DETAIL_ROLE, ModelPicker
from ctrlcctrlv.workspace_ui import SettingsWindow, HistoryPage, AddModelDialog


def test_delay_display_preserves_precision_and_locale():
    from ctrlcctrlv.settings_controls import DelaySpinBox
    box = DelaySpinBox()
    box.setDecimals(6)
    box.setRange(0, 200)
    box.setLocale(QLocale(QLocale.Language.Russian))
    box.setValue(2.123456)
    assert box.value() == 2.123456
    assert box.text() == '2,123456'
    box.setValue(2)
    assert box.text() == '2'
    box.lineEdit().setText('3,125')
    box.interpretText()
    assert box.value() == 3.125


def test_hotkey_recording_waits_in_field_and_preserves_width():
    window = SettingsWindow(Settings())
    window.show_configuration('system')
    window.show()
    QApplication.processEvents()
    control = window.hotkey_record_buttons[0]
    before = control.size()
    control.click()
    QApplication.processEvents()
    assert control.text() == 'Отмена'
    assert control.size() == before
    assert 'Нажмите' in window.capture_hotkey.displayText()
    QTest.keyClick(window.capture_hotkey, Qt.Key.Key_Escape)
    assert control.text() == 'Записать'
    QTest.mouseClick(control, Qt.MouseButton.LeftButton)
    QTest.mouseClick(control, Qt.MouseButton.LeftButton)
    assert not window.capture_hotkey.recording
    window.close()


def test_history_pin_identity_and_undo_keep_list_geometry():
    page = HistoryPage()
    first = SimpleNamespace(id=3, created=datetime.now().astimezone().isoformat(), text='old', kind='ocr', model='', status='completed')
    other = SimpleNamespace(id=3, created='2025-01-01T12:00:00+00:00', text='other', kind='ocr', model='', status='completed')
    page.display([first], pinned=[first])
    page.resize(580, 400)
    page.show()
    QApplication.processEvents()
    assert page.entries.item(0).text() == 'Закреплённые'
    assert page.selected_ref().created == first.created
    pins = []
    page.pin_requested.connect(lambda *args: pins.append(args))
    page.pin.click()
    assert pins == [(3, False)]
    before = page.split.size()
    page.set_undo_available(True)
    QApplication.processEvents()
    assert page.split.size() == before
    assert page.undo.isVisible()
    assert page.pin.isVisible()
    page.display([other])
    assert page.selected_ref().created == other.created
    assert page.type.text() == 'Напечатать'
    page.close()


def test_chooser_local_metadata_and_empty_actions():
    picker = ModelPicker()
    for i in range(5):
        picker.addItem('Example vision model ' + str(i), str(i))
        picker.setItemData(i, {'source': 'local', 'size': '1.2 ГБ'}, DETAIL_ROLE)
    picker.set_installed({'0'})
    chooser = picker.create_chooser()
    chooser.resize(640, 420)
    chooser.show()
    QApplication.processEvents()
    assert chooser.table.horizontalHeaderItem(1).text() == 'Размер / Состояние'
    assert 'На диске' in chooser.table.item(0, 1).text()
    assert 'Не скачана' in chooser.table.item(1, 1).text()
    assert chooser.table.viewport().height() >= sum(chooser.table.rowHeight(i) for i in range(3))
    chooser.search.setText('nothing matches')
    assert chooser.choose.isHidden() and chooser.install.isHidden() and chooser.favorite.isHidden()
    assert chooser.reset_filters.isVisible()
    chooser.reset_filters.click()
    assert chooser.table.rowCount() == 5
    chooser.deleteLater()
    picker.deleteLater()


def test_import_uses_two_elided_file_rows_and_rejects_empty_bundle():
    parent = SettingsWindow(Settings())
    dialog = AddModelDialog(parent)
    long = 'very-' * 35 + '.gguf'
    info = dict(weights=[long], projectors=['projector.gguf'], selected=long, repo='author/repo', commit='a'*40, license='MIT')
    dialog.display(info)
    dialog.show()
    QApplication.processEvents()
    assert dialog.weights_summary.toolTip() == long
    assert dialog.projector_summary.toolTip() == 'projector.gguf'
    assert dialog.weights_summary.text() != long
    assert dialog.add.isEnabled()
    dialog.display(dict(info, projectors=[]))
    assert not dialog.add.isEnabled()
    dialog.close()


def test_library_header_stays_fixed_and_unknown_total_shows_known_bytes():
    window = SettingsWindow(Settings())
    window.resize(820, 620)
    window.navigation.setCurrentRow(1)
    window.runtime_toggle.setChecked(True)
    window.show()
    QApplication.processEvents()
    assert not isinstance(window.pages.widget(1), QScrollArea)
    assert window.model_table.viewport().height() >= 2 * window.model_table.rowHeight(0)
    first = next(iter(window.models))
    height = window.model_table.rowHeight(0)
    window.set_model_queue([first])
    window.set_model_progress(first, 'weights', 120_000_000, 0)
    status, bar, volume = window.model_status_widgets[first]
    QApplication.processEvents()
    assert status.height() >= status.fontMetrics().height()
    assert volume.height() >= volume.fontMetrics().height()
    assert bar.width() >= 140
    assert bar.maximum() == 0
    assert volume.text() == '120 МБ'
    assert window.model_table.rowHeight(0) == height
    assert window.queue_cancel.isVisible()
    cancelled = []
    window.cancel_requested.connect(lambda: cancelled.append(True))
    window.set_busy(True)
    assert window.queue_cancel.isEnabled()
    window.queue_cancel.click()
    assert cancelled == [True]
    window.close()


def test_chooser_cloud_and_mixed_sources_label_price_units():
    picker = ModelPicker()
    picker.addItem('Cloud vision', 'cloud')
    picker.setItemData(0, {'source': 'openrouter', 'price': '$1 / $2'}, DETAIL_ROLE)
    cloud = picker.create_chooser()
    assert 'USD / 1 млн токенов' in cloud.table.horizontalHeaderItem(1).text()
    assert cloud.table.item(0, 1).text() == '$1 / $2'
    cloud.deleteLater()
    picker.addItem('Local vision', 'local')
    picker.setItemData(1, {'source': 'local', 'size': '1 ГБ'}, DETAIL_ROLE)
    mixed = picker.create_chooser()
    assert mixed.table.horizontalHeaderItem(1).text() == 'Источник'
    assert mixed.table.horizontalHeaderItem(2).text() == 'Размер / Цена'
    assert 'USD / 1 млн токенов' in mixed.table.item(0, 2).toolTip()
    assert 'Локально' not in mixed.table.item(0, 2).text()
    mixed.deleteLater()
    picker.deleteLater()


def test_import_advanced_long_names_keep_rows_and_actions_accessible():
    parent = SettingsWindow(Settings())
    dialog = AddModelDialog(parent)
    dialog.display(dict(weights=['weights-'*100+'.gguf'], projectors=['projector-'*100+'.gguf'], selected='', repo='author/model', commit='a'*40, license='MIT'))
    dialog.show()
    dialog.advanced_toggle.setChecked(True)
    QApplication.processEvents()
    assert dialog.weights.geometry().center().y() < dialog.projector.geometry().center().y()
    assert dialog.weights.geometry().bottom() < dialog.projector.geometry().top()
    assert dialog.mode.geometry().bottom() < dialog.add.mapTo(dialog, dialog.add.rect().topLeft()).y()
    assert dialog.weights.width() > 200
    dialog.close()


@pytest.mark.parametrize('count', [4, 30])
def test_library_geometry_expanded_error_and_scroll_keeps_actions(count, monkeypatch):
    from dataclasses import replace
    from PySide6.QtCore import QRect
    from ctrlcctrlv import workspace_ui
    from ctrlcctrlv.catalog import MODELS
    from ctrlcctrlv.theme import fit_window_to_screen
    monkeypatch.setattr(workspace_ui, 'fit_window_to_screen', lambda w: fit_window_to_screen(w, QRect(0, 0, 1200, 900)))
    base = next(iter(MODELS.values()))
    models = {str(i): replace(base, id=str(i), label=f'Vision model {i}') for i in range(count)}
    window = SettingsWindow(Settings(), models=models)
    window.resize(820, 620)
    window.navigation.setCurrentRow(1)
    window.runtime_toggle.setChecked(True)
    window.show_model_error('Download failed\nExact details')
    window.set_model_retry_available(True)
    window.idle.setValue(window.idle.value() + 1)
    window.show()
    QApplication.processEvents()
    assert window.model_table.viewport().height() >= 2 * window.model_table.rowHeight(0)
    before = window.download_selected.mapTo(window, window.download_selected.rect().topLeft())
    window.runtime_scroll.verticalScrollBar().setValue(window.runtime_scroll.verticalScrollBar().maximum())
    QApplication.processEvents()
    assert window.download_selected.mapTo(window, window.download_selected.rect().topLeft()) == before
    window.resize(960, 500)
    fit_window_to_screen(window, QRect(0, 0, 960, 500))
    QApplication.processEvents()
    assert window.frameGeometry().height() <= 500
    assert window.download_selected.isVisible()
    assert window.runtime_toggle.isVisible()
    assert window.model_retry_button.mapTo(window, window.model_retry_button.rect().topLeft()).y() >= window.model_table.mapTo(window, window.model_table.rect().bottomLeft()).y()
    window.close()


def test_model_retry_is_explicit_and_preserves_error_details():
    window = SettingsWindow(Settings())
    calls = []
    window.model_retry_requested.connect(lambda: calls.append(True))
    window.show_model_error('HTTP error\nThe exact server detail')
    assert calls == []
    window.set_model_retry_available(True)
    window.model_retry_button.click()
    assert calls == [True]
    assert window.model_error.toolTip() == 'HTTP error\nThe exact server detail'
    window.clear_model_error()
    assert window.model_retry_button.isHidden()
    window.deleteLater()


def test_repeat_region_remains_secondary_and_obeys_busy_state():
    window = SettingsWindow(Settings())
    assert window.repeat_capture_button.isHidden()
    assert not window.repeat_capture_button.isEnabled()
    window.set_repeat_available(True)
    assert not window.repeat_capture_button.isHidden()
    assert window.repeat_capture_button.isEnabled()
    requested = []
    window.repeat_capture_requested.connect(lambda: requested.append(True))
    window.repeat_capture_button.click()
    assert requested == [True]
    window.set_busy(True)
    assert not window.repeat_capture_button.isEnabled()
    window.set_busy(False)
    assert window.repeat_capture_button.isEnabled()
    window.set_repeat_available(False)
    assert window.repeat_capture_button.isHidden()
    window.deleteLater()


def test_four_model_library_fits_full_content_without_tiny_scrollbar(monkeypatch):
    from PySide6.QtCore import QRect
    from ctrlcctrlv import workspace_ui
    from ctrlcctrlv.theme import fit_window_to_screen
    monkeypatch.setattr(workspace_ui, 'fit_window_to_screen', lambda w: fit_window_to_screen(w, QRect(0, 0, 1200, 900)))
    window = SettingsWindow(Settings())
    window.resize(820, 620)
    window.navigation.setCurrentRow(1)
    window.runtime_toggle.setChecked(True)
    window.show()
    QApplication.processEvents()
    assert window.model_table.viewport().height() >= sum(window.model_table.rowHeight(i) for i in range(window.model_table.rowCount()))
    assert window.model_table.verticalScrollBar().maximum() == 0
    window.deleteLater()


def test_pending_state_survives_navigation_and_save_stays_available_on_own_surfaces():
    window = SettingsWindow(Settings())
    window.delay.setValue(window.delay.value() + 1)
    assert window.save_hint.text() == 'Изменено'
    assert window.has_pending()
    assert window.save_hint.isHidden() and window.save_button.isHidden()
    for page in (1, 2):
        window.navigation.setCurrentRow(page)
        assert not window.save_button.isHidden() and window.save_button.isEnabled()
        assert not window.save_hint.isHidden()
        window.set_busy(True)
        assert not window.save_button.isEnabled()
        window.set_busy(False)
        assert window.save_button.isEnabled()
    window.navigation.setCurrentRow(3)
    assert window.save_button.isHidden() and window.save_hint.isHidden()
    assert window.has_pending() and window.save_hint.text() == 'Изменено'
    window.mark_saved()
    assert not window.has_pending()
    assert window.save_hint.text() == ''
    window.deleteLater()


@pytest.mark.parametrize('available', [True, False])
def test_history_error_survives_undo_visibility_and_expiry_until_explicit_clear(available):
    page = HistoryPage()
    page.show_history_error('Не удалось прочитать историю')
    page.set_undo_available(available)
    assert page.history_status.text() == 'Не удалось прочитать историю'
    assert page.history_status.toolTip() == 'Не удалось прочитать историю'
    page.clear_history_error()
    assert page.history_status.text() == ('Удалено' if available else '')
    assert page.history_status.toolTip() == ''
    page.show_history_error('Не удалось прочитать историю')
    page.set_undo_available(True)
    page.set_undo_available(False)  # Expiry timer follows this same UI transition.
    assert page.history_status.text() == 'Не удалось прочитать историю'
    assert page.undo.isHidden()
    page.clear_history_error()
    assert page.history_status.text() == ''
    page.deleteLater()
