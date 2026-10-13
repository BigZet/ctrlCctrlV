import pytest

from ctrlcctrlv.text_export import suggest_extension, save_text


@pytest.mark.parametrize(('source', 'extension'), [
    ('def hello(name):\n    print(name)\n', 'py'),
    ('    print("Привет")\n', 'py'),
    ('x = 1\n', 'py'),
    ('def broken(:\n    pass\n', 'py'),
    ('SELECT name FROM products;', 'sql'),
    ('public class Main { public static void main(String[] args) {} }', 'java'),
    ('{"name": "Иван", "count": 1}', 'json'),
    ('<!DOCTYPE html>\n<html><body>Hello</body></html>', 'html'),
    ('<?xml version="1.0"?><root/>', 'xml'),
    ('[build-system]\nrequires = ["setuptools>=68"]', 'toml'),
    ('const total = items.reduce((a, b) => a + b, 0);', 'js'),
    ('# Заголовок\n\n- Первый\n- Второй\n', 'md'),
    ('Обычный текст для сохранения\n', 'txt'),
    ('if you need help, read the documentation.', 'txt'),
    ('[Примечание]\nОбычный текст', 'txt'),
])
def test_suggest_extension(source, extension):
    assert suggest_extension(source) == extension


def test_explicit_editor_language_overrides_detection():
    assert suggest_extension('incomplete fragment', 'python') == 'py'
    assert suggest_extension('print(1)', 'none') == 'py'


def test_save_text_preserves_utf8_whitespace_and_newlines(tmp_path):
    source = '\n    print("Привет 🙂")  \r\n\treturn 1\n'
    target = tmp_path / 'код.py'
    save_text(target, source)
    assert target.read_bytes() == source.encode('utf-8')


def test_save_error_keeps_previous_file_and_removes_temporary(tmp_path, monkeypatch):
    import ctrlcctrlv.text_export as exports
    target = tmp_path / 'код.py'
    target.write_bytes(b'old')
    def fail(*args):
        raise PermissionError('locked')
    monkeypatch.setattr(exports.os, 'replace', fail)
    with pytest.raises(PermissionError):
        save_text(target, 'new')
    assert target.read_bytes() == b'old'
    assert list(tmp_path.iterdir()) == [target]
