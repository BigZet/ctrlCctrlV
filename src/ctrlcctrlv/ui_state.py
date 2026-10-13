"""Profile-local presentation preferences, independent of OCR settings."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile


DEFAULTS = {
    "preview_sizes": [440, 560], "image_visible": True,
    "last_save_directory": "", "favorites": [], "history_pins": [],
    "capture_adjust": False, "capture_help_seen": False,
}


def valid(key, value):
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (UnicodeError, TypeError, ValueError, RecursionError):
        return False
    if key in {"image_visible", "capture_adjust", "capture_help_seen"}:
        return type(value) is bool
    if key == "preview_sizes":
        return isinstance(value, list) and len(value) == 2 and all(type(n) is int and 0 < n <= 10000 for n in value)
    if key == "last_save_directory":
        return isinstance(value, str) and len(value) <= 4096 and "\0" not in value
    if key == "favorites":
        return isinstance(value, list) and len(value) <= 1000 and all(isinstance(s, str) and 0 < len(s) <= 256 for s in value)
    if key == "history_pins":
        return (isinstance(value, list) and len(value) <= 100
                and all(isinstance(ref, dict) and set(ref) == {"id", "created"}
                        and type(ref["id"]) is int and ref["id"] > 0
                        and isinstance(ref["created"], str) and len(ref["created"]) <= 64
                        for ref in value))
    return False


class UiState:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else None
        self.values = deepcopy(DEFAULTS)
        if self.directory is not None:
            try:
                with (self.directory / "ui-state.json").open("rb") as stream:
                    encoded = stream.read(65537)
                if len(encoded) > 65536:
                    return
                data = json.loads(encoded)
                if isinstance(data, dict):
                    self.values.update({key: value for key, value in data.items() if valid(key, value)})
            except (OSError, ValueError, TypeError, RecursionError):
                pass

    def get(self, key, default=None):
        return deepcopy(self.values.get(key, default))

    def set(self, **changes):
        candidate = deepcopy(self.values)
        candidate.update({key: deepcopy(value) for key, value in changes.items() if valid(key, value)})
        temporary = None
        try:
            encoded = json.dumps(candidate, ensure_ascii=False, indent=2).encode("utf-8")
            if len(encoded) > 65536:
                return False
            if self.directory is None:
                self.values = candidate
                return True
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".ui-state-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
            os.replace(temporary, self.directory / "ui-state.json")
            self.values = candidate
            return True
        except OSError:
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
