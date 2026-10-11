import pytest

from ctrlcctrlv.settings import Settings


def test_settings_roundtrip_without_key(tmp_path):
    settings = Settings(remote_model="another/vision", autoindent=True, capture_hotkey="ctrl+cmd+5", show_preview=False)
    settings.save(tmp_path)
    assert Settings.load(tmp_path) == settings
    assert "api-key" not in (tmp_path / "settings.json").read_text()


def test_duplicate_hotkeys_rejected():
    settings = Settings(typing_hotkey=Settings().capture_hotkey)
    with pytest.raises(ValueError, match="различаться"):
        settings.validate()


def test_broken_configuration_recovers_defaults(tmp_path):
    (tmp_path / "settings.json").write_text('{"provider":"wrong"}')
    assert Settings.load(tmp_path) == Settings()


def test_older_settings_enable_preview_by_default(tmp_path):
    (tmp_path / "settings.json").write_text('{"provider":"local"}')
    assert Settings.load(tmp_path).show_preview is True


@pytest.mark.parametrize('content', ['[]', 'null', 'true', '123', '"text"', '[' * 3000 + '0' + ']' * 3000, ' ' * 65537],
                         ids=['array', 'null', 'boolean', 'number', 'string', 'deep', 'oversized'])
def test_non_object_deep_or_oversized_settings_recover_without_deleting_file(tmp_path, content):
    path = tmp_path / 'settings.json'
    path.write_text(content)
    assert Settings.load(tmp_path) == Settings()
    assert path.read_text() == content


@pytest.mark.parametrize('name,value', [
    ('capture_hotkey', None), ('cancel_hotkey', []), ('provider', {}), ('local_model', []),
    ('local_model', 'removed-model'), ('remote_model', {}), ('remote_model', ' '),
    ('remote_mode', []), ('review_local_model', []), ('review_remote_model', []),
    ('show_preview', 1), ('autoindent', 'yes'), ('idle_minutes', 1.5),
    ('spaces_per_tab', True), ('max_tokens', 256.5), ('character_delay', '0.01'),
    ('newline_delay', False), ('character_delay', float('nan')), ('newline_delay', float('inf')),
])
def test_invalid_field_types_and_values_never_reach_widgets(tmp_path, name, value):
    import json
    (tmp_path / 'settings.json').write_text(json.dumps({name: value}))
    assert Settings.load(tmp_path) == Settings()
    with pytest.raises(ValueError):
        Settings(**{name: value}).validate()


def test_oversized_save_preserves_existing_settings(tmp_path):
    Settings(show_preview=False).save(tmp_path)
    path = tmp_path / 'settings.json'
    previous = path.read_bytes()
    with pytest.raises(ValueError, match='большие'):
        Settings(remote_model='x' * 65536).save(tmp_path)
    assert path.read_bytes() == previous
