import json
import pytest

from ctrlcctrlv.ui_state import UiState


def test_state_persists_ui_preferences_without_touching_settings(tmp_path):
    settings = tmp_path / 'settings.json'
    settings.write_bytes(b'{"show_preview":false}')
    state = UiState(tmp_path)
    assert state.set(preview_sizes=[300, 700], image_visible=False, favorites=['local:a'],
                     last_save_directory=str(tmp_path), capture_adjust=True)
    loaded = UiState(tmp_path)
    assert loaded.get('preview_sizes') == [300, 700]
    assert loaded.get('image_visible') is False
    assert loaded.get('favorites') == ['local:a']
    assert loaded.get('capture_adjust') is True
    assert settings.read_bytes() == b'{"show_preview":false}'


def test_corrupt_ui_state_only_resets_ui(tmp_path):
    (tmp_path / 'ui-state.json').write_bytes(b'{broken')
    state = UiState(tmp_path)
    assert state.get('image_visible') is True
    assert state.get('capture_adjust') is False
    assert state.get('favorites') == []
    assert state.set(image_visible=False)
    assert json.loads((tmp_path / 'ui-state.json').read_text())['image_visible'] is False


def test_invalid_fields_are_ignored_and_get_does_not_leak_mutable_state(tmp_path):
    (tmp_path / 'ui-state.json').write_text(json.dumps({'preview_sizes': [-1, 'x'], 'capture_adjust': 'yes',
         'image_visible':False,'favorites':['local:a','local:a',9]}))
    state = UiState(tmp_path)
    assert state.get('preview_sizes') == [440,560]
    assert state.get('capture_adjust') is False
    assert state.get('image_visible') is False
    state.get('preview_sizes')[0] = 0
    assert state.get('preview_sizes') == [440,560]


def test_failed_write_preserves_previous_file(tmp_path, monkeypatch):
    import ctrlcctrlv.ui_state as module
    state = UiState(tmp_path)
    assert state.set(image_visible=False)
    before = (tmp_path / 'ui-state.json').read_bytes()
    monkeypatch.setattr(module.os, 'replace', lambda *a: (_ for _ in ()).throw(PermissionError('locked')))
    assert state.set(image_visible=True) is False
    assert (tmp_path / 'ui-state.json').read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['ui-state.json']


def test_oversized_preferences_do_not_poison_future_saves(tmp_path):
    state = UiState(tmp_path)
    assert state.set(favorites=['local:good'])
    assert not state.set(favorites=['local:' + str(i) + 'x' * 110 for i in range(700)])
    assert state.get('favorites') == ['local:good']
    assert state.set(image_visible=False)
    assert UiState(tmp_path).get('image_visible') is False


def test_surrogate_path_cannot_break_ui_state_save(tmp_path):
    (tmp_path/'ui-state.json').write_text('{"last_save_directory":"\\ud800"}')
    state = UiState(tmp_path)
    assert state.get('last_save_directory') == ''
    assert state.set(image_visible=False)


def test_history_pins_persist_exact_identity_without_text_and_are_defensively_copied(tmp_path):
    state = UiState(tmp_path)
    assert state.get('history_pins') == []
    pins = [{'id': 1, 'created': '2026-01-01T00:00:00+00:00'}]
    assert state.set(history_pins=pins)
    pins[0]['id'] = 2
    loaded = UiState(tmp_path)
    assert loaded.get('history_pins') == [{'id': 1, 'created': '2026-01-01T00:00:00+00:00'}]
    loaded.get('history_pins')[0]['id'] = 3
    assert loaded.get('history_pins') == state.get('history_pins')


@pytest.mark.parametrize('pins', [
    [{'id': True, 'created': '2000'}], [{'id': 0, 'created': '2000'}],
    [{'id': -1, 'created': '2000'}], [{'id': 1.0, 'created': '2000'}],
    [{'id': 1, 'created': 123}], [{'id': 1, 'created': 'x' * 65}],
    [{'id': 1}], [{'id': 1, 'created': '2000', 'text': 'private'}],
    [{'id': 1, 'created': '\ud800'}],
    [{'id': i, 'created': '2000'} for i in range(1, 102)],
])
def test_invalid_history_pins_do_not_replace_valid_state(tmp_path, pins):
    state = UiState(tmp_path)
    original = [{'id': 1, 'created': '2000'}]
    state.set(history_pins=original)
    state.set(history_pins=pins)
    assert state.get('history_pins') == original
    (tmp_path / 'ui-state.json').write_text(json.dumps({'history_pins': pins}))
    assert UiState(tmp_path).get('history_pins') == []


def test_history_pin_limit_accepts_100_and_failed_save_preserves_previous_identity(tmp_path, monkeypatch):
    import ctrlcctrlv.ui_state as module
    state = UiState(tmp_path)
    pins = [{'id': i, 'created': 'x' * 64} for i in range(1, 101)]
    assert state.set(history_pins=pins)
    monkeypatch.setattr(module.os, 'replace', lambda *args: (_ for _ in ()).throw(PermissionError('locked')))
    assert state.set(history_pins=[]) is False
    assert state.get('history_pins') == pins
    assert UiState(tmp_path).get('history_pins') == pins
