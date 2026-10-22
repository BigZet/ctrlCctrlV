from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys


def data_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/ctrlCctrlV"
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "ctrlCctrlV"


@dataclass
class Settings:
    provider: str = "local"
    review_provider: str = "local"
    capture_format: str = "auto"
    acceleration: str = "gpu"
    local_model: str = "lightonocr-3-0.8b"
    remote_model: str = "qwen/qwen3-vl-8b-instruct"
    remote_mode: str = "json"
    show_preview: bool = True
    review_local_model: str = "qwen3-vl-4b"
    review_remote_model: str = "qwen/qwen3-vl-8b-instruct"
    capture_hotkey: str = "ctrl+alt+4" if sys.platform == "win32" else "ctrl+alt+cmd+4"
    typing_hotkey: str = "ctrl+alt+v" if sys.platform == "win32" else "ctrl+alt+cmd+v"
    cancel_hotkey: str = "ctrl+alt+esc" if sys.platform == "win32" else "ctrl+alt+cmd+esc"
    character_delay: float = 0.008
    newline_delay: float = 0.060
    autoindent: bool = False
    spaces_per_tab: int = 0
    idle_minutes: int = 15
    max_tokens: int = 4096
    # Read by the Rust app: "layout" types with the keys of a keyboard layout (VDI).
    typing_method: str = "unicode"
    layout_delay: float = 0.150
    layout_switch: str = "sync"
    latin_layout_hotkey: str = "ctrl+shift+1"
    cyrillic_layout_hotkey: str = "ctrl+shift+2"
    delete_autoclose: bool = False
    hotkey_mode: str = "system"

    @classmethod
    def load(cls, directory: Path, models=None):
        try:
            with (directory / "settings.json").open("rb") as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                return cls()
            values = json.loads(data.decode("utf-8"))
            if not isinstance(values, dict):
                return cls()
            fields = cls.__dataclass_fields__
            values.setdefault("review_provider", values.get("provider", "local"))
            result = cls(**{k: v for k, v in values.items() if k in fields})
            result.validate(models)
            return result
        except (OSError, ValueError, TypeError, RecursionError):
            return cls()

    def validate(self, models=None):
        for name in ("show_preview", "autoindent", "delete_autoclose"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("Некорректная настройка: " + name)
        for name in ("provider", "review_provider", "capture_format", "acceleration", "local_model", "remote_model", "remote_mode", "review_local_model",
                     "review_remote_model", "capture_hotkey", "typing_hotkey", "cancel_hotkey", "typing_method",
                     "layout_switch", "latin_layout_hotkey", "cyrillic_layout_hotkey", "hotkey_mode"):
            if not isinstance(getattr(self, name), str):
                raise ValueError("Некорректная настройка: " + name)
        for name in ("spaces_per_tab", "idle_minutes", "max_tokens"):
            if type(getattr(self, name)) is not int:
                raise ValueError("Некорректная настройка: " + name)
        for name in ("character_delay", "newline_delay", "layout_delay"):
            if type(getattr(self, name)) not in (int, float):
                raise ValueError("Некорректная настройка: " + name)
        from .catalog import MODELS
        MODELS = models if models is not None else MODELS
        if self.local_model not in MODELS:
            raise ValueError("Неизвестная локальная модель OCR")
        if not self.remote_model.strip():
            raise ValueError("Укажите облачную модель OCR")
        if self.review_local_model not in MODELS or MODELS[self.review_local_model].mode != "json":
            raise ValueError("Для проверки выберите vision-модель с текстовыми инструкциями")
        if not isinstance(self.review_remote_model, str) or not self.review_remote_model.strip():
            raise ValueError("Укажите облачную модель проверки")
        from .hotkeys import parse_hotkey
        keys = [parse_hotkey(value) for value in (self.capture_hotkey, self.typing_hotkey, self.cancel_hotkey)]
        if len(set(keys)) != 3:
            raise ValueError("Горячие клавиши должны различаться")
        if self.provider not in {"local", "openrouter"}:
            raise ValueError("Неизвестный OCR-провайдер")
        if self.review_provider not in {"local", "openrouter"}:
            raise ValueError("Неизвестный провайдер проверки")
        if self.capture_format not in {"auto", "text", "csv"}:
            raise ValueError("Неизвестный формат распознавания")
        if self.acceleration not in {"gpu", "cpu"}:
            raise ValueError("Выберите GPU или CPU для локальной модели")
        if self.remote_mode not in {"json", "transcription"}:
            raise ValueError("Неизвестный протокол модели")
        if not 0.001 <= self.character_delay <= 0.2 or not 0 <= self.newline_delay <= 2 or not 0 <= self.layout_delay <= 2:
            raise ValueError("Некорректная задержка ввода")
        if self.typing_method not in {"unicode", "layout"}:
            raise ValueError("Неизвестный способ ввода")
        if self.layout_switch not in {"sync", "hotkeys"} or self.hotkey_mode not in {"system", "rawinput"}:
            raise ValueError("Неизвестный способ переключения раскладки или регистрации горячих клавиш")
        if parse_hotkey(self.latin_layout_hotkey) == parse_hotkey(self.cyrillic_layout_hotkey):
            raise ValueError("Сочетания раскладок должны различаться")
        if not 0 <= self.spaces_per_tab <= 8 or not 1 <= self.idle_minutes <= 120:
            raise ValueError("Некорректный профиль ввода или время простоя")
        if not 128 <= self.max_tokens <= 8192:
            raise ValueError("Некорректный предел ответа")

    def save(self, directory: Path, models=None):
        self.validate(models)
        encoded = json.dumps(asdict(self), ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > 65536:
            raise ValueError("Настройки слишком большие")
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "settings.json.tmp"
        temporary.write_bytes(encoded)
        temporary.replace(directory / "settings.json")


class Credentials:
    service = "ctrlCctrlV.OpenRouter"

    def read(self):
        import keyring
        return keyring.get_password(self.service, "api-key") or ""

    def write(self, value):
        import keyring
        if value:
            keyring.set_password(self.service, "api-key", value)
        elif self.read():
            keyring.delete_password(self.service, "api-key")
