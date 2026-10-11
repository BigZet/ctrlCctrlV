from types import SimpleNamespace
import threading

import pytest

from ctrlcctrlv.core import Cancelled
from ctrlcctrlv.macos import type_text
from ctrlcctrlv.settings import Settings


class FakePlatform:
    quartz = SimpleNamespace(kCGEventFlagMaskShift=1)

    def __init__(self):
        self.events = []
        self.pid = 100

    def permissions(self):
        return {"accessibility": True}

    def modifiers_pressed(self):
        return False

    def active_pid(self):
        return self.pid

    def character(self, text):
        self.events.append(("text", text))

    def key(self, key, modifiers=0):
        self.events.append(("key", key, modifiers))


def test_unicode_crlf_tabs_and_autoindent():
    platform = FakePlatform()
    settings = Settings(character_delay=0.001, newline_delay=0, autoindent=True, spaces_per_tab=2)
    type_text(platform, 'Ж\r\n\t🙂\n', settings, threading.Event(), lambda *args: None)
    assert platform.events == [("text", "Ж"), ("key", 36, 0), ("key", 115, 1),
        ("text", " "), ("text", " "), ("text", "🙂"), ("key", 36, 0)]


def test_focus_change_stops_before_next_character():
    platform = FakePlatform()

    def progress(done, total):
        platform.pid = 200

    with pytest.raises(Cancelled, match="приложение"):
        type_text(platform, "abc", Settings(character_delay=0.001), threading.Event(), progress)
    assert platform.events == [("text", "a")]


def test_cancel_stops_before_next_character():
    platform = FakePlatform()
    cancel = threading.Event()
    with pytest.raises(Cancelled):
        type_text(platform, "abc", Settings(character_delay=0.001), cancel, lambda *args: cancel.set())
    assert platform.events == [("text", "a")]


def test_cancel_mid_tab_expansion_stops_remaining_spaces():
    cancel = threading.Event()
    platform = FakePlatform()
    original = platform.character

    def character(text):
        original(text)
        cancel.set()

    platform.character = character
    with pytest.raises(Cancelled):
        type_text(platform, "\tabc", Settings(character_delay=0.001, spaces_per_tab=4), cancel, lambda *args: None)
    assert platform.events == [("text", " ")]


def test_focus_changes_before_worker_starts_never_type_into_new_app():
    platform = FakePlatform()
    platform.pid = 200
    with pytest.raises(Cancelled, match="приложение"):
        type_text(platform, "abc", Settings(), threading.Event(), lambda *args: None,
                  expected_target_pid=100)
    assert platform.events == []
