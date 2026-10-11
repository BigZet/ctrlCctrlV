"""Portable hotkey descriptions; key codes are supplied by the platform adapter."""
KEY_CODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32,
    "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46, "esc": 53,
}


def parse_hotkey(value):
    # Command and Windows refer to the platform's meta key. Normalizing aliases
    # also prevents registering duplicate shortcuts under different spellings.
    value = value.lower().replace("win", "cmd")
    parts = value.lower().replace(" ", "").split("+")
    key, modifiers = parts[-1], parts[:-1]
    if key not in KEY_CODES or not modifiers or len(set(modifiers)) != len(modifiers):
        raise ValueError("Пример сочетания: ctrl+alt+cmd+4")
    if set(modifiers) - {"ctrl", "alt", "cmd", "shift"}:
        raise ValueError("Модификаторы: ctrl, alt, cmd (или win), shift")
    if not set(modifiers) & {"ctrl", "alt", "cmd"}:
        raise ValueError("Добавьте ctrl, alt или cmd к сочетанию")
    return frozenset(modifiers), key

