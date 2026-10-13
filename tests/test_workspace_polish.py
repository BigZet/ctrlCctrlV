from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from ctrlcctrlv.model_picker import DETAIL_ROLE, ModelPicker
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.workspace_ui import AddModelDialog, HistoryPage, SettingsWindow


@pytest.fixture
def window():
    widget = SettingsWindow(Settings(character_delay=.008125, newline_delay=.060375))
    yield widget
    widget.deleteLater()


def test_presets_preserve_fractional_manual_values_and_editing_updates_preset(window):
    assert window.delay.value() == 8.125
    assert window.newline.value() == 60.375
    assert window.speed_preset.currentData() is None
    saved = Settings()
    window.apply_to(saved)
    assert saved.character_delay == .008125
    assert saved.newline_delay == .060375
    window.speed_preset.setCurrentIndex(0)
    assert (window.delay.value(), window.newline.value()) == (2, 20)
    window.delay.setValue(3.5)
    assert window.speed_preset.currentData() is None


def test_busy_disables_preset_and_hotkey_recording_controls(window):
    window.delay.setValue(5)
    window.set_busy(True)
    assert not window.speed_preset.isEnabled()
    assert all(not record.isEnabled() for record in window.hotkey_record_buttons)
    window.update_pending()
    assert not window.save_button.isEnabled()
    window.set_busy(False)
    assert window.speed_preset.isEnabled()
    assert window.save_button.isEnabled()


def test_manual_preset_reveals_advanced_fields(window):
    window.speed_preset.setCurrentIndex(0)
    window.speed_preset.setCurrentIndex(3)
    assert window.timing_toggle.isChecked()


def test_preview_sync_save_visibility_and_permissions(window):
    events = []
    window.preview_option_changed.connect(events.append)
    window.quick_preview.setChecked(False)
    assert not window.show_preview.isChecked()
    assert events == [False]
    window.show_preview.setChecked(True)
    assert window.quick_preview.isChecked()
    window.navigation.setCurrentRow(2)
    assert window.save_button.isHidden()
    window.delay.setValue(5)
    assert not window.save_button.isHidden()
    window.mark_saved()
    assert window.save_button.isHidden()
    window.show_permissions(dict(accessibility=True, screen=True, input_monitoring=True))
    assert not window.permissions_toggle.isChecked()
    window.show_permissions(dict(accessibility=True, screen=False, input_monitoring=True))
    assert window.permissions_toggle.isChecked()


def test_hotkey_capture_keeps_portable_validator_compatible_text(window, physical_control_modifier):
    from ctrlcctrlv.hotkeys import parse_hotkey
    field = window.capture_hotkey
    field.start_recording()
    QTest.keyClick(field, Qt.Key.Key_K, physical_control_modifier | Qt.KeyboardModifier.AltModifier)
    assert parse_hotkey(field.text()) == (frozenset({'ctrl', 'alt'}), 'k')
    field.start_recording()
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert field.text() == 'ctrl+alt+k'


def test_hotkey_display_uses_platform_names_and_recording_announces_cancel(window):
    from ctrlcctrlv.settings_controls import pretty_hotkey
    assert pretty_hotkey('ctrl+alt+win+esc', platform='win32') == 'Ctrl+Alt+Win+Esc'
    assert pretty_hotkey('ctrl+alt+cmd+4', platform='darwin') == '⌃⌥⌘4'
    field = window.capture_hotkey
    changes = []
    field.recording_changed.connect(changes.append)
    field.start_recording()
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert changes == [True, False]


@pytest.mark.parametrize('platform,expected', [('win32', 'Ctrl+Alt+Win+4'), ('darwin', '⌃⌥⌘4')])
def test_hotkey_field_displays_native_names_and_keeps_portable_binding(monkeypatch, platform, expected):
    from ctrlcctrlv import settings_controls
    monkeypatch.setattr(settings_controls, 'sys', SimpleNamespace(platform=platform))
    field = settings_controls.HotkeyEdit('ctrl+alt+cmd+4')
    assert field.displayText() == expected
    assert field.text() == 'ctrl+alt+cmd+4'
    field.setText('ctrl+alt+k')
    assert field.text() == 'ctrl+alt+k'
    assert field.displayText() == settings_controls.pretty_hotkey('ctrl+alt+k')
    field.deleteLater()


def test_model_queue_progress_selection_and_unavailable_delete(window):
    identifiers = list(window.models)
    first, second = identifiers[:2]
    window.update_model_states({first}, set(), True, False)
    assert window.model_rows[second][1][1].isHidden()
    window.toggle_all_models(0)
    missing = sum(f.size for key, m in window.models.items() if key != first for f in m.files)
    assert str(len(identifiers)) in window.download_selected.text()
    assert str(missing) in window.download_selected.toolTip()
    window.set_model_queue([first, second])
    window.set_model_progress(first, 'Downloading weights', 3, 10)
    assert '30%' in window.model_table.item(window.model_rows[first][0], 3).text()
    assert 'очеред' in window.model_table.item(window.model_rows[second][0], 3).text().lower()
    window.clear_model_queue()
    assert window.model_table.item(window.model_rows[first][0], 3).text() == 'На диске'


def test_model_queue_has_only_row_progress_on_models_page(window):
    window.set_model_queue(list(window.models))
    window.navigation.setCurrentRow(1)
    window.set_busy(True)
    assert window.progress.isHidden()
    window.navigation.setCurrentRow(0)
    assert not window.progress.isHidden()
    window.navigation.setCurrentRow(1)
    assert window.progress.isHidden()


def test_quick_preview_fits_next_to_format_at_minimum_window_size(window):
    from PySide6.QtWidgets import QApplication
    window.resize(820, 620)
    window.show()
    QApplication.processEvents()
    preview_center = window.quick_preview.mapTo(window, window.quick_preview.rect().center())
    format_center = window.capture_format.mapTo(window, window.capture_format.rect().center())
    assert abs(preview_center.y() - format_center.y()) < 4
    assert window.quick_preview.width() >= window.quick_preview.sizeHint().width()
    window.close()


def test_row_progress_reports_volume_and_completion_leaves_other_models_queued(window):
    first, second = list(window.models)[:2]
    window.set_model_queue([first, second])
    window.set_model_progress(first, 'Weights', 750_000_000, 1_500_000_000)
    status, bar, volume = window.model_status_widgets[first]
    assert bar.value() == 50 and not bar.isHidden()
    assert volume.text() == '0.75 / 1.50 ГБ'
    assert status.text() == '50%'
    window.set_model_complete(first)
    assert first not in window._model_queue and first not in window._model_progress
    assert status.text() == 'На диске' and bar.isHidden() and volume.isHidden()
    assert window.model_status_widgets[second][0].text() == 'В очереди'
    assert window.model_rows[first][1][1].isEnabled()


def test_status_cell_paints_widget_without_duplicate_text_underneath(window):
    from PySide6.QtWidgets import QStyleOptionViewItem
    first = next(iter(window.models))
    window.set_model_queue([first])
    row = window.model_rows[first][0]
    option = QStyleOptionViewItem()
    index = window.model_table.model().index(row, 3)
    window.model_table.itemDelegateForColumn(3).initStyleOption(option, index)
    assert option.text == ''
    assert window.model_table.item(row, 3).text() == 'В очереди'


def test_work_error_disclosure_preserves_exact_details_and_reuses_retry_action(window):
    message = 'OCR unavailable\n<server response>\nTry another model'
    assert window.operation_error.isHidden()
    window.show_operation_error(message)
    assert not window.operation_error.isHidden()
    assert not window.operation_error_toggle.isChecked()
    window.operation_error_toggle.setChecked(True)
    assert window.operation_error_details.toPlainText() == message
    requested = []
    window.retry_requested.connect(lambda: requested.append(True))
    window.retry_button.click()
    assert requested == [True]
    window.clear_operation_error()
    assert window.operation_error.isHidden()
    assert window.operation_error_details.toPlainText() == ''


def test_picker_filters_favorites_and_requests_install_without_selecting():
    class State:
        def __init__(self): self.values = {}
        def get(self, key, default=None): return self.values.get(key, default)
        def set(self, **values): self.values.update(values); return True
    state = State()
    picker = ModelPicker()
    for identifier in ('installed', 'missing'):
        picker.addItem(identifier, identifier)
        picker.setItemData(picker.count() - 1, {'source': 'local'}, DETAIL_ROLE)
    picker.set_ui_state(state)
    picker.set_installed({'installed'})
    dialog = picker.create_chooser()
    assert dialog.table.horizontalHeaderItem(1).text() == 'Размер / Состояние'
    dialog.downloaded_only.setChecked(True)
    assert dialog.table.rowCount() == 1
    dialog.favorite.click()
    assert state.values['favorites'] == ['local:installed']
    dialog.downloaded_only.setChecked(False)
    dialog.favorites_only.setChecked(True)
    assert dialog.table.rowCount() == 1
    dialog.favorites_only.setChecked(False)
    dialog.search.setText('missing')
    requested = []
    picker.install_requested.connect(requested.append)
    dialog.install.click()
    assert requested == ['missing']
    assert picker.currentData() == 'installed'
    dialog.deleteLater()
    picker.deleteLater()


def test_workspace_picker_install_request_routes_to_library_without_downloading(window):
    requests = []
    window.model_action_requested.connect(lambda *args: requests.append(args))
    window.local_model.install_requested.emit('missing')
    assert requests == [('missing', 'show_install')]


def test_history_groups_days_and_open_save_keep_original_entry():
    page = HistoryPage()
    now = datetime.now().astimezone()
    entries = [SimpleNamespace(id=i + 1, created=(now - timedelta(days=i)).isoformat(),
               text=f'Original {i}', kind='ocr', model='test', status='completed') for i in range(2)]
    page.display(entries)
    assert page.entries.item(0).text() == 'Сегодня'
    assert page.entries.item(2).text() == 'Вчера'
    opened, saved = [], []
    page.open_requested.connect(opened.append)
    page.save_requested.connect(saved.append)
    page.open.click()
    page.save.click()
    assert opened == saved == [1]
    assert entries[0].text == 'Original 0'
    page.search.setText('absent')
    page.display([])
    assert page.empty_heading.text() == 'Ничего не найдено'
    assert page.copy.isHidden() and page.open.isHidden()
    page.deleteLater()


def test_import_starts_with_search_and_reveals_fields_after_discovery(window):
    dialog = AddModelDialog(window)
    assert dialog.file_fields.isHidden()
    assert dialog.add.isHidden()
    assert dialog.height() < 350
    dialog.display(dict(weights=['m.gguf'], projectors=['p.gguf'], selected='m.gguf',
                        repo='author/repo', commit='a'*40, license='MIT'))
    assert not dialog.file_fields.isHidden()
    assert not dialog.add.isHidden()
    assert 'author/repo' in dialog.bundle_summary.text()
    assert 'm.gguf' in dialog.bundle_summary.text()
    assert dialog.weights.parentWidget() is dialog.mode.parentWidget()
    assert dialog.weights.parentWidget().isHidden()
    assert not dialog.advanced_toggle.isChecked()
    dialog.link.setText('another')
    assert not dialog.add.isEnabled()
    assert dialog.add.isHidden() and dialog.file_fields.isHidden()
    assert dialog.weights.count() == 0 and dialog.projector.count() == 0
    dialog.deleteLater()


def test_import_repeated_discovery_resets_protocol_and_updates_bundle_summary(window):
    dialog = AddModelDialog(window)
    info = dict(weights=['m.gguf', 'alternative.gguf'], projectors=['p.gguf'], selected='m.gguf',
                repo='author/LightOnOCR', commit='a'*40, license='MIT')
    dialog.display(info)
    assert dialog.mode.currentData() == 'transcription'
    dialog.display(dict(info, repo='author/vision'))
    assert dialog.mode.currentData() == 'json'
    dialog.weights.setCurrentText('alternative.gguf')
    assert 'alternative.gguf' in dialog.bundle_summary.text()
    dialog.deleteLater()
