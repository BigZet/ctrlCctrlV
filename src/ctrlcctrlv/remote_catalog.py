"""Public OpenRouter vision catalog. Listing never requests inference."""
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import json
import httpx


@dataclass(frozen=True)
class VisionModel:
    id: str
    name: str
    input_price: str
    output_price: str
    structured: bool
    temperature: bool = False
    max_completion_tokens: int | None = None
    default_temperature: float | None = None

    @property
    def label(self):
        return f"{self.name} · ${self.input_price} / ${self.output_price} за 1M токенов"


def price(value):
    try:
        number = Decimal(str(value)) * 1_000_000
        return format(number, ".4f").rstrip("0").rstrip(".") if number.is_finite() and number >= 0 else "?"
    except (InvalidOperation, ValueError):
        return "?"


def parse_models(payload):
    result = {}
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        architecture = item.get("architecture") or {}
        if "image" not in architecture.get("input_modalities", []) or "text" not in architecture.get("output_modalities", []):
            continue
        identifier, name = item.get("id"), item.get("name")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 256 or not isinstance(name, str):
            continue
        pricing = item.get("pricing") or {}
        supported = item.get("supported_parameters") or []
        limit = (item.get("top_provider") or {}).get("max_completion_tokens")
        limit = limit if type(limit) is int and limit > 0 else None
        default_temperature = (item.get("default_parameters") or {}).get("temperature")
        if type(default_temperature) not in (int, float) or not 0 <= default_temperature <= 2:
            default_temperature = None
        result[identifier] = VisionModel(identifier, name[:200], price(pricing.get("prompt")), price(pricing.get("completion")),
                                         "response_format" in supported, "temperature" in supported, limit, default_temperature)
    return sorted(result.values(), key=lambda model: (Decimal(model.input_price) if model.input_price != "?" else Decimal("Infinity"), model.name))


def fetch_models(transport=None):
    with httpx.Client(timeout=20, transport=transport) as client:
        with client.stream("GET", "https://openrouter.ai/api/v1/models", params={"input_modalities": "image", "output_modalities": "text"}) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 8_000_000:
                    raise ValueError("Каталог моделей слишком большой")
    models = parse_models(json.loads(body))
    if not models:
        raise ValueError("В каталоге нет моделей с изображениями и текстовым ответом")
    return models


def save_cache(directory, models):
    target = directory / "openrouter-models.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps([asdict(model) for model in models], ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def load_cache(directory):
    try:
        path = directory / "openrouter-models.json"
        if path.stat().st_size > 8_000_000:
            return []
        return [VisionModel(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []
