"""Mac native key identities are delivered locally; no global input is sent."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from ctrlcctrlv import settings_controls


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setattr(settings_controls, "sys", SimpleNamespace(platform="darwin"))
    field = settings_controls.HotkeyEdit("ctrl+k")
    yield field
    field.deleteLater()


def deliver(field, key, modifiers, native_key, native_modifiers, text=""):
    field.start_recording()
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers,
                      0, native_key, native_modifiers, text)
    QApplication.sendEvent(field, event)


@pytest.mark.parametrize("symbol,native_key,digit", [
    (")", 29, "0"), ("!", 18, "1"), ("@", 19, "2"), ("#", 20, "3"),
    ("$", 21, "4"), ("%", 23, "5"), ("^", 22, "6"), ("&", 26, "7"),
    ("*", 28, "8"), ("(", 25, "9"),
])
def test_mac_shifted_digit_records_physical_digit(recorder, symbol, native_key, digit):
    # Cocoa's charactersIgnoringModifiers still includes Shift; native keyCode
    # identifies the top-row digit used by MacPlatform.start_hotkeys.
    deliver(recorder, ord(symbol), Qt.MetaModifier | Qt.ShiftModifier,
            native_key, (1 << 18) | (1 << 17), symbol)
    assert recorder.text() == f"ctrl+shift+{digit}"
    assert not recorder.recording


@pytest.mark.parametrize("logical_key,native_key,text,want", [
    (ord("Й"), 12, "й", "q"),  # Russian layout, physical Q
    (ord("Ф"), 0, "ф", "a"),   # Native A has keyCode zero.
    (Qt.Key.Key_Q, 0, "q", "a"),  # AZERTY, physical A
    (Qt.Key.Key_Q, 7, "q", "x"),  # Dvorak, physical X
])
def test_mac_layout_records_key_used_by_native_registration(
        recorder, logical_key, native_key, text, want):
    deliver(recorder, logical_key, Qt.MetaModifier, native_key, 1 << 18, text)
    assert recorder.text() == f"ctrl+{want}"
    assert not recorder.recording


@pytest.mark.parametrize("modifier,native_modifier,want", [
    (Qt.MetaModifier, 1 << 18, "ctrl"),
    (Qt.ControlModifier, 1 << 20, "cmd"),
    (Qt.AltModifier, 1 << 19, "alt"),
])
def test_mac_single_modifier_keeps_native_meaning(recorder, modifier, native_modifier, want):
    deliver(recorder, Qt.Key.Key_Asterisk, modifier | Qt.ShiftModifier,
            28, native_modifier | (1 << 17), "*")
    assert recorder.text() == f"{want}+shift+8"
    assert not recorder.recording


def test_mac_command_control_option_modifiers_keep_native_meaning(recorder):
    deliver(recorder, Qt.Key.Key_Dollar,
            Qt.MetaModifier | Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier,
            21, (1 << 18) | (1 << 20) | (1 << 19) | (1 << 17), "$")
    assert recorder.text() == "ctrl+alt+cmd+shift+4"
    assert not recorder.recording


def test_mac_synthetic_zero_metadata_keeps_logical_key(recorder):
    # QTest and the short QKeyEvent constructor have zero native metadata.
    deliver(recorder, Qt.Key.Key_V, Qt.MetaModifier, 0, 0, "v")
    assert recorder.text() == "ctrl+v"
    assert not recorder.recording


def test_mac_keypad_digit_does_not_record_untriggerable_top_row_binding(recorder):
    # Keypad 8 has its own keyCode, absent from native registration.
    deliver(recorder, Qt.Key.Key_8, Qt.MetaModifier | Qt.KeypadModifier,
            91, (1 << 18) | (1 << 21), "8")
    assert recorder.text() == "ctrl+k"
    assert recorder.recording


def test_mac_escape_without_modifiers_cancels_recording(recorder):
    deliver(recorder, Qt.Key.Key_Escape, Qt.NoModifier, 53, 0)
    assert recorder.text() == "ctrl+k"
    assert not recorder.recording


@pytest.mark.parametrize("key,native_key,text,want", [
    (Qt.Key.Key_Semicolon, 28, "8", "ctrl+shift+8"),
    (ord("Ф"), 0, "\x01", "ctrl+shift+a"),
])
def test_mac_modifier_changes_wait_for_following_physical_key(recorder, key, native_key, text, want):
    # Captured from the real Cocoa window in Russian layout: flagsChanged
    # delivers modifier-only Qt events with nativeVirtualKey=0 before keyDown.
    recorder.start_recording()
    modifiers = Qt.MetaModifier | Qt.ShiftModifier
    for modifier_key, native_flags in ((Qt.Key.Key_Shift, 262144), (Qt.Key.Key_Meta, 131072)):
        event = QKeyEvent(QEvent.Type.KeyPress, modifier_key, modifiers, 0, 0, native_flags, "")
        QApplication.sendEvent(recorder, event)
        assert recorder.text() == "ctrl+k"
        assert recorder.recording
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, 0, native_key, 393216, text)
    QApplication.sendEvent(recorder, event)
    assert recorder.text() == want
    assert not recorder.recording


@pytest.mark.parametrize("key", [
    Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Meta, Qt.Key.Key_Alt,
    Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock, Qt.Key.Key_NumLock, Qt.Key.Key_ScrollLock,
])
def test_mac_modifier_only_native_event_never_becomes_letter_a(recorder, key):
    deliver(recorder, key, Qt.MetaModifier | Qt.ShiftModifier, 0, 262144)
    assert recorder.text() == "ctrl+k"
    assert recorder.recording
