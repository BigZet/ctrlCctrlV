"""Per-profile GGUF bundles pinned to publisher hashes and immutable revisions."""
from dataclasses import asdict
import hashlib
import json
import re
from urllib.parse import quote, unquote, urlsplit
import httpx

from .catalog import Artifact, Model, MODELS


def validate(model):
    if not re.fullmatch(r"custom-[a-f0-9]{16}", model.id) or model.mode not in {"json", "transcription"}:
        raise ValueError("Некорректная пользовательская модель")
    if len(model.files) != 2 or tuple(file.filename for file in model.files) != ("model.gguf", "mmproj.gguf"):
        raise ValueError("Для vision-модели нужны GGUF и mmproj")
    for file in model.files:
        url = urlsplit(file.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Нужны публичные HTTPS-ссылки без паролей и параметров")
        if not re.fullmatch(r"[a-f0-9]{64}", file.sha256) or type(file.size) is not int or not 0 < file.size < 200_000_000_000:
            raise ValueError("Не получены SHA256 и размер GGUF")
    return model


class ModelRegistry:
    def __init__(self, directory):
        self.path = directory / "custom-models.json"
        self.models = dict(MODELS)
        self.errors = []
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > 1_000_000:
                raise ValueError("Каталог слишком большой")
            for item in json.loads(self.path.read_text(encoding="utf-8")):
                item["files"] = tuple(Artifact(**file) for file in item["files"])
                model = validate(Model(**item))
                self.models[model.id] = model
        except (OSError, TypeError, ValueError, KeyError):
            self.errors.append("Не удалось прочитать каталог добавленных моделей")

    def add(self, model):
        validate(model)
        updated = dict(self.models)
        updated[model.id] = model
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps([asdict(value) for key, value in updated.items() if key not in MODELS], ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self.models[model.id] = model


def discover(link, transport=None):
    url = urlsplit(link.strip())
    if url.scheme != "https" or url.hostname != "huggingface.co" or url.username or url.password or url.query or url.fragment:
        raise ValueError("Укажите публичную ссылку Hugging Face на GGUF-модель или файл")
    parts = unquote(url.path).strip("/").split("/")
    if len(parts) < 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in {".", ".."} for part in parts):
        raise ValueError("Некорректная ссылка на репозиторий")
    repo = "/".join(parts[:2])
    revision = "main"
    selected = None
    if len(parts) > 2:
        if parts[2] not in {"resolve", "blob", "tree"} or len(parts) < 4:
            raise ValueError("Нужна ссылка на репозиторий или GGUF-файл")
        revision = parts[3]
        if len(parts) > 4:
            selected = "/".join(parts[4:])
    with httpx.Client(timeout=20, transport=transport) as client:
        response = client.get(f"https://huggingface.co/api/models/{repo}/revision/{quote(revision, safe='')}", params={"blobs": "true"})
        response.raise_for_status()
        if len(response.content) > 4_000_000:
            raise ValueError("Метаданные репозитория слишком большие")
        info = response.json()
    commit = info.get("sha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Не получена фиксированная версия репозитория")
    files = {}
    for file in info.get("siblings", []):
        name = file.get("rfilename", "")
        lfs = file.get("lfs") or {}
        if name.lower().endswith(".gguf") and isinstance(lfs.get("sha256"), str) and isinstance(lfs.get("size"), int):
            files[name] = Artifact("", f"https://huggingface.co/{repo}/resolve/{commit}/{quote(name, safe='/')}", lfs["sha256"], lfs["size"])
    weights = [name for name in files if "mmproj" not in name.lower()]
    projectors = [name for name in files if "mmproj" in name.lower()]
    if not weights or not projectors:
        raise ValueError("В репозитории нужны GGUF-веса и совместимый mmproj с SHA256. Выберите vision-комплект для llama.cpp.")
    return {"repo": repo, "commit": commit, "files": files, "weights": weights, "projectors": projectors,
            "selected": selected, "license": (info.get("cardData") or {}).get("license", "Смотрите карточку модели")}


def make_model(info, weights, projector, mode="json"):
    if weights not in info["weights"] or projector not in info["projectors"]:
        raise ValueError("Выберите веса и соответствующий mmproj")
    identifier = "custom-" + hashlib.sha256((info["repo"] + info["commit"] + weights + projector + mode).encode()).hexdigest()[:16]
    artifacts = tuple(Artifact(filename, info["files"][name].url, info["files"][name].sha256, info["files"][name].size)
                      for filename, name in (("model.gguf", weights), ("mmproj.gguf", projector)))
    return validate(Model(identifier, f"{info['repo'].split('/')[-1]} · {weights}", artifacts,
                          f"https://huggingface.co/{info['repo']}", str(info["license"]), mode))
