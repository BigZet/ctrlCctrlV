import threading
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from ctrlcctrlv.app import Controller
from ctrlcctrlv.syntax import SyntaxHint, guess_language, syntax_hint


def test_python_parser_reports_location_without_executing_source(tmp_path):
    marker = tmp_path / "must-not-exist"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    assert syntax_hint(source, "python") is None
    assert not marker.exists()
    hint = syntax_hint('def greet():\n    print("hello"\n', "python")
    assert hint.language == "Python"
    assert hint.line == 2
    assert "строка 2" in hint.label


def test_indented_fragments_json_and_disabled_checks():
    assert syntax_hint('    if x:\n        print("Ж🙂")\n', "python") is None
    assert syntax_hint('{"name": "Ж🙂", "enabled": true}', "json") is None
    hint = syntax_hint('{\n    "name": "Ж"\n    "enabled": true\n}', "json")
    assert hint.language == "JSON"
    assert hint.line == 3
    assert syntax_hint("this is not code", "none") is None
    assert syntax_hint("", "python") is None


def test_auto_detection_is_optional_and_unknown_text_has_no_hint():
    assert guess_language('def collect():\n    pass') == "python"
    assert guess_language(' {"value": 1}') == "json"
    assert guess_language("SELECT * FROM items;") == "none"
    assert syntax_hint("Перенос текста из редактора.") is None


def test_parser_limits_malformed_and_deep_inputs():
    assert syntax_hint("x = 1\0", "python") is not None
    assert syntax_hint("[" * 3000 + "]" * 3000, "json") is not None
    assert syntax_hint('"' + "[" * 3000 + '"', "json") is None
    assert "слишком большой" in syntax_hint("x" * 100001, "python").message


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    c = Controller(app, tmp_path, native=False)
    yield c
    c.preview.syntax_timer.stop()
    c.capture_timer.stop()
    c.typing_timer.stop()
    c.watchdog.stop()
    c.idle_timer.stop()
    c.permissions_timer.stop()
    c.pool.waitForDone()
    c.tray.hide()
    c.window.close()
    c.preview.close()
    c.window.deleteLater()
    c.preview.deleteLater()
    c.deleteLater()
    app.sendPostedEvents()


def wait(c, condition, timeout=2):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        c.app.processEvents()
        time.sleep(0.002)
    c.app.processEvents()
    assert condition()


def test_background_hint_is_discarded_if_text_changes(controller, monkeypatch):
    c = controller
    entered, release = threading.Event(), threading.Event()
    def slow(text, language):
        assert QThread.currentThread() != c.app.thread()
        entered.set()
        release.wait(1)
        return SyntaxHint("Python", 1, "old error")
    monkeypatch.setattr("ctrlcctrlv.syntax.syntax_hint", slow)
    c.app.clipboard().setText("clipboard stays")
    c.preview.text.setPlainText("def old(")
    c.preview.syntax_timer.stop()
    revision = c.preview.syntax_revision
    try:
        c.check_syntax("def old(", "python", revision)
        wait(c, entered.is_set)
        assert not c.busy()
        c.preview.text.setPlainText("def fixed():\n    pass\n")
        c.preview.syntax_timer.stop()
        release.set()
        wait(c, lambda: c.pool.activeThreadCount() == 0)
        assert c.preview.syntax_message.isHidden()
        assert c.preview.syntax_message.text() == ""
        assert c.app.clipboard().text() == "clipboard stays"
    finally:
        release.set()


def test_edits_are_debounced_and_disabling_check_removes_hint(controller, monkeypatch):
    c = controller
    calls = []
    def checker(text, language):
        calls.append((text, language))
        return None if language == "none" else SyntaxHint("Python", 1, "invalid syntax")
    monkeypatch.setattr("ctrlcctrlv.syntax.syntax_hint", checker)
    for text in ("d", "de", "def", "def broken("):
        c.preview.text.setPlainText(text)
    wait(c, lambda: not c.preview.syntax_message.isHidden())
    assert calls == [("def broken(", "auto")]
    c.preview.syntax_language.setCurrentIndex(c.preview.syntax_language.findData("none"))
    assert c.preview.syntax_message.isHidden()
    wait(c, lambda: len(calls) == 2 and c.pool.activeThreadCount() == 0)
    assert c.preview.syntax_message.isHidden()
