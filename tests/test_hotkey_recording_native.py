"""Native key identity normalization; Qt only, no global shortcut hooks."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from ctrlcctrlv import settings_controls


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setattr(settings_controls, "sys", SimpleNamespace(platform="win32"))
    field = settings_controls.HotkeyEdit("ctrl+k")
    yield field
    field.deleteLater()


def deliver(field, key, modifiers, native_vk, text="", autorepeat=False):
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers,
                      0, native_vk, 0, text, autorepeat)
    QApplication.sendEvent(field, event)


@pytest.mark.parametrize("symbol,native_vk,want", [
    (Qt.Key.Key_ParenRight, 0x30, "ctrl+alt+shift+0"),
    (Qt.Key.Key_Exclam, 0x31, "ctrl+alt+shift+1"),
    (Qt.Key.Key_At, 0x32, "ctrl+alt+shift+2"),
    (Qt.Key.Key_NumberSign, 0x33, "ctrl+alt+shift+3"),
    (Qt.Key.Key_Dollar, 0x34, "ctrl+alt+shift+4"),
    (Qt.Key.Key_Percent, 0x35, "ctrl+alt+shift+5"),
    (Qt.Key.Key_AsciiCircum, 0x36, "ctrl+alt+shift+6"),
    (Qt.Key.Key_Ampersand, 0x37, "ctrl+alt+shift+7"),
    (Qt.Key.Key_Asterisk, 0x38, "ctrl+alt+shift+8"),
    (Qt.Key.Key_ParenLeft, 0x39, "ctrl+alt+shift+9"),
])
def test_windows_shifted_digit_records_native_digit_and_finishes(recorder, symbol, native_vk, want):
    # Dropping physical digit normalization leaves punctuation or the old chord.
    transitions = []
    recorder.recording_changed.connect(transitions.append)
    recorder.start_recording()
    deliver(recorder, symbol, Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier,
            native_vk, chr(symbol))
    assert recorder.text() == want
    assert not recorder.recording
    assert transitions == [True, False]


@pytest.mark.parametrize("platform,key,native_vk,modifiers,autorepeat", [
    ("darwin", Qt.Key.Key_Asterisk, 0x38, Qt.MetaModifier | Qt.ShiftModifier, False),
    ("linux", Qt.Key.Key_Asterisk, 0x38, Qt.ControlModifier | Qt.ShiftModifier, False),
    ("win32", Qt.Key.Key_Asterisk, 0, Qt.ControlModifier | Qt.ShiftModifier, False),
    ("win32", Qt.Key.Key_Asterisk, 0x6A, Qt.ControlModifier | Qt.ShiftModifier, False),
    ("win32", Qt.Key.Key_Asterisk, 0x38, Qt.NoModifier, False),
    ("win32", Qt.Key.Key_F8, 0x77, Qt.ControlModifier | Qt.ShiftModifier, False),
    ("win32", Qt.Key.Key_Shift, 0x10, Qt.ShiftModifier, False),
    ("win32", Qt.Key.Key_Asterisk, 0x38, Qt.ControlModifier | Qt.ShiftModifier, True),
    ("win32", Qt.Key.Key_Asterisk, 0x38, Qt.ShiftModifier, False),
])
def test_unsupported_or_non_windows_events_preserve_binding_and_recording(
        recorder, monkeypatch, platform, key, native_vk, modifiers, autorepeat):
    # An overbroad Windows identity fallback could accidentally accept these events.
    monkeypatch.setattr(settings_controls, "sys", SimpleNamespace(platform=platform))
    recorder.start_recording()
    deliver(recorder, key, modifiers, native_vk, autorepeat=autorepeat)
    assert recorder.text() == "ctrl+k"
    assert recorder.recording


@pytest.mark.parametrize("modifiers,want", [
    (Qt.ControlModifier, "ctrl+8"),
    (Qt.ControlModifier | Qt.AltModifier, "ctrl+alt+8"),
    (Qt.ControlModifier | Qt.AltModifier | Qt.GroupSwitchModifier, "ctrl+alt+8"),
])
def test_windows_unshifted_digit_and_group_switch_keep_supported_chord(recorder, modifiers, want):
    recorder.start_recording()
    deliver(recorder, Qt.Key.Key_8, modifiers, 0x38, "8")
    assert recorder.text() == want
    assert not recorder.recording


def test_windows_ctrl_shift_eight_records_digit_instead_of_asterisk(recorder):
    recorder.start_recording()
    deliver(recorder, Qt.Key.Key_Asterisk, Qt.ControlModifier | Qt.ShiftModifier,
            0x38, "*")
    assert recorder.text() == "ctrl+shift+8"
    assert recorder.displayText() == "Ctrl+Shift+8"
    assert not recorder.recording


@pytest.mark.parametrize("key,modifiers,text,want", [
    # Native Windows probe: Russian Ctrl+Alt+8 produces Qt key U+20BD (ruble).
    (8381, Qt.ControlModifier | Qt.AltModifier, "₽", "ctrl+alt+8"),
    (Qt.Key.Key_Asterisk, Qt.ControlModifier, "*", "ctrl+8"),
])
def test_windows_native_digit_survives_layout_symbol_without_shift(recorder, key, modifiers, text, want):
    recorder.start_recording()
    deliver(recorder, key, modifiers, 0x38, text)
    assert recorder.text() == want
    assert not recorder.recording


def test_mac_native_digit_metadata_does_not_override_qt_key_or_modifier_swap(recorder, monkeypatch):
    monkeypatch.setattr(settings_controls, "sys", SimpleNamespace(platform="darwin"))
    recorder.start_recording()
    deliver(recorder, Qt.Key.Key_K, Qt.MetaModifier | Qt.ControlModifier | Qt.ShiftModifier,
            0x38, "K")
    assert recorder.text() == "ctrl+cmd+shift+k"
    assert not recorder.recording
