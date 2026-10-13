"""Small settings controls that keep the existing settings field interfaces."""
import sys

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QComboBox, QLineEdit, QDoubleSpinBox

from .hotkeys import parse_hotkey

CAPTURE_ACTION_LABEL = 'Распознать область'


class DelaySpinBox(QDoubleSpinBox):
    """Show concise locale-aware milliseconds without rounding the stored value."""
    def textFromValue(self, value):
        text = self.locale().toString(value, 'f', self.decimals())
        separator = self.locale().decimalPoint()
        return text.rstrip('0').rstrip(separator) if separator in text else text


def pretty_hotkey(value, platform=None):
    """Readable labels only; persisted shortcuts retain portable parser syntax."""
    platform = platform or sys.platform
    parts = value.lower().replace(" ", "").split("+")
    if platform == "darwin":
        names = {"ctrl": "⌃", "alt": "⌥", "cmd": "⌘", "win": "⌘", "shift": "⇧", "esc": "Esc"}
        return "".join(names.get(part, part.upper()) for part in parts)
    names = {"ctrl": "Ctrl", "alt": "Alt", "cmd": "Win", "win": "Win", "shift": "Shift", "esc": "Esc"}
    return "+".join(names.get(part, part.upper()) for part in parts)


class HotkeyEdit(QLineEdit):
    """Capture a single portable chord; Escape cancels without changing its value."""
    recording_changed = Signal(bool)

    def __init__(self, value):
        self._binding = value
        super().__init__(pretty_hotkey(value))
        self.recording = False
        self.setReadOnly(True)
        self.setToolTip("Нажмите «Записать», затем сочетание клавиш. Esc — отмена.")

    def text(self):
        return self._binding

    def setText(self, value):
        self._binding = value
        super().setText(pretty_hotkey(value))

    def start_recording(self):
        self.recording = True
        super().setText("Нажмите сочетание · Esc — отмена")
        self.recording_changed.emit(True)
        self.setPlaceholderText("Нажмите сочетание · Esc — отмена")
        self.setFocus()
        self.selectAll()

    def focusOutEvent(self, event):
        # A mouse click moves focus to the containing scroll area before the
        # Cancel button receives its click. Let that click finish recording.
        if event.reason() == Qt.FocusReason.MouseFocusReason:
            QTimer.singleShot(0, self._finish_if_unfocused)
        else:
            self.finish_recording()
        self.deselect()
        super().focusOutEvent(event)

    def _finish_if_unfocused(self):
        if not self.hasFocus():
            self.finish_recording()

    def finish_recording(self):
        if self.recording:
            self.recording = False
            super().setText(pretty_hotkey(self._binding))
            self.recording_changed.emit(False)

    def keyPressEvent(self, event):
        if not self.recording:
            return super().keyPressEvent(event)
        modifiers = event.modifiers()
        if event.key() == Qt.Key.Key_Escape and modifiers == Qt.KeyboardModifier.NoModifier:
            self.finish_recording()
            self.deselect()
            return
        if event.isAutoRepeat():
            return
        # Qt swaps Control and Meta to follow native macOS menu conventions.
        control = Qt.KeyboardModifier.MetaModifier if sys.platform == "darwin" else Qt.KeyboardModifier.ControlModifier
        meta = Qt.KeyboardModifier.ControlModifier if sys.platform == "darwin" else Qt.KeyboardModifier.MetaModifier
        parts = [name for flag, name in ((control, "ctrl"), (Qt.KeyboardModifier.AltModifier, "alt"),
                 (meta, "cmd" if sys.platform == "darwin" else "win"), (Qt.KeyboardModifier.ShiftModifier, "shift")) if modifiers & flag]
        key = event.key()
        # Windows layouts can report a symbol for a digit chord ('*' or '₽').
        # The native VK preserves the top-row digit needed by our chord parser.
        if sys.platform == "win32" and 0x30 <= event.nativeVirtualKey() <= 0x39:
            key = event.nativeVirtualKey()
        name = "esc" if key == Qt.Key.Key_Escape else chr(key).lower() if 0x30 <= key <= 0x5a else ""
        value = "+".join(parts + [name])
        try:
            parse_hotkey(value)
        except ValueError:
            self.setToolTip("Используйте Ctrl, Alt или " + ("Cmd" if sys.platform == "darwin" else "Win") + " и букву, цифру или Esc")
            return
        self.setText(value)
        self.finish_recording()
        self.deselect()


class SpeedPreset(QComboBox):
    def __init__(self, delay, newline):
        super().__init__()
        self.delay, self.newline = delay, newline
        for name, values in (("Быстро", (2., 20.)), ("Обычно", (8., 60.)), ("Медленно", (20., 120.)), ("Вручную", None)):
            self.addItem(name, values)
        self.currentIndexChanged.connect(self.apply_preset)
        delay.valueChanged.connect(self.sync)
        newline.valueChanged.connect(self.sync)
        self.sync()

    def apply_preset(self, *_):
        values = self.currentData()
        if values is not None:
            self.delay.blockSignals(True)
            self.delay.setValue(values[0])
            self.delay.blockSignals(False)
            self.newline.setValue(values[1])
            self.delay.valueChanged.emit(self.delay.value())

    def sync(self, *_):
        values = (self.delay.value(), self.newline.value())
        index = next((i for i in range(self.count()) if self.itemData(i) == values), self.count() - 1)
        self.blockSignals(True)
        self.setCurrentIndex(index)
        self.blockSignals(False)
