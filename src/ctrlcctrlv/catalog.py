"""Pinned public artifacts; hashes are publisher SHA256 values, not inferred file sizes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Artifact:
    filename: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    files: tuple[Artifact, ...]
    source: str
    license: str = "Apache-2.0"
    mode: str = "json"


def model(identifier, parameters, revision, weights_hash, weights_size, projector_hash, projector_size):
    repo = f"unsloth/Qwen3-VL-{parameters}B-Instruct-GGUF"
    base = f"https://huggingface.co/{repo}/resolve/{revision}/"
    weights = f"Qwen3-VL-{parameters}B-Instruct-Q4_K_M.gguf"
    return Model(identifier, f"Qwen3-VL {parameters}B · Q4_K_M", (
        Artifact(weights, base + weights, weights_hash, weights_size),
        Artifact("mmproj-F16.gguf", base + "mmproj-F16.gguf", projector_hash, projector_size),
    ), f"https://huggingface.co/{repo}/tree/{revision}")


MODELS = {
    "lightonocr-3-0.8b": Model("lightonocr-3-0.8b", "LightOnOCR-3 0.8B · Q8 · экспериментальный", (
        Artifact("LightOnOCR-3-0.8B.Q8_0.gguf",
            "https://huggingface.co/prithivMLmods/LightOnOCR-3-0.8B-GGUF/resolve/414757bdb78fd07c947a1e83c2b8d7e9a8b73583/LightOnOCR-3-0.8B.Q8_0.gguf",
            "660a8931ba33c8a604076f69b8785b36eadcdb1cc90004e9f89bf67615a0456d", 811843680),
        Artifact("LightOnOCR-3-0.8B.mmproj-bf16.gguf",
            "https://huggingface.co/prithivMLmods/LightOnOCR-3-0.8B-GGUF/resolve/414757bdb78fd07c947a1e83c2b8d7e9a8b73583/LightOnOCR-3-0.8B.mmproj-bf16.gguf",
            "23f0d5dba9b7ef73f760e6870826e72d265a8938fdb975ed3b751853f496ac06", 207346496),
    ), "https://huggingface.co/prithivMLmods/LightOnOCR-3-0.8B-GGUF", mode="transcription"),
    "lightonocr-3-4b": Model("lightonocr-3-4b", "LightOnOCR-3 4B · Q8 · экспериментальный", (
        Artifact("LightOnOCR-3-4B.Q8_0.gguf",
            "https://huggingface.co/prithivMLmods/LightOnOCR-3-4B-GGUF/resolve/557c2b008f7abff778985246b1a045c635f9abdf/LightOnOCR-3-4B.Q8_0.gguf",
            "eb9f84e81a27f1449bd1fc91eb9b9f7eeeec49bd03483af0423839bc1bf5ffd4", 4482403264),
        Artifact("LightOnOCR-3-4B.mmproj-bf16.gguf",
            "https://huggingface.co/prithivMLmods/LightOnOCR-3-4B-GGUF/resolve/557c2b008f7abff778985246b1a045c635f9abdf/LightOnOCR-3-4B.mmproj-bf16.gguf",
            "4c4f14c8bda8cd67cf4822f99c7c57b6353fd4907cba38d5535987d0db06dd8e", 675569216),
    ), "https://huggingface.co/prithivMLmods/LightOnOCR-3-4B-GGUF", mode="transcription"),
    "qwen3-vl-4b": model("qwen3-vl-4b", 4, "00c00da0690c4b14b5539b02c4ea5d7c9102b35e",
        "d4dcd426bfba75752a312b266b80fec8136fbaca13c62d93b7ac41fa67f0492b", 2497282336,
        "1b9f4e92f0fbda14d7d7b58baed86039b8a980fe503d9d6a9393f25c0028f1fc", 836180640),
    "qwen3-vl-8b": model("qwen3-vl-8b", 8, "b93a7ee713758252c555be4210c00540df954dc2",
        "108e7ff92b78eefd3db4741885104acba514255c11b617d3c7b197a5f46efe89", 5027785568,
        "d406d03ebabefdef86a2c86bf0c1b65f9e046f7a81c218f25de4931b46a07fc4", 1159030336),
}

ENGINE_VERSION = "b11552"
ENGINE = Artifact("llama-b11552-bin-macos-arm64.tar.gz",
    "https://github.com/ggml-org/llama.cpp/releases/download/b11552/llama-b11552-bin-macos-arm64.tar.gz",
    "f4b33fe40d098033fb1a19a8a4a84383c4ca84c37b6a60d9f9a253f349b48c07", 12086252)

WINDOWS_ENGINE = Artifact("llama-b11552-bin-win-cuda-12.4-x64.zip",
    "https://github.com/ggml-org/llama.cpp/releases/download/b11552/llama-b11552-bin-win-cuda-12.4-x64.zip",
    "e48331718aa82aa34571cfad8510e6d6b9a8e4dad11a73e065f94f212e280256", 265234947)
WINDOWS_CUDART = Artifact("cudart-llama-bin-win-cuda-12.4-x64.zip",
    "https://github.com/ggml-org/llama.cpp/releases/download/b11552/cudart-llama-bin-win-cuda-12.4-x64.zip",
    "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6", 391443627)
WINDOWS_CPU_ENGINE = Artifact("llama-b11552-bin-win-cpu-x64.zip",
    "https://github.com/ggml-org/llama.cpp/releases/download/b11552/llama-b11552-bin-win-cpu-x64.zip",
    "c00741179811ff903b515eae28bdeef1f47af6e81c49920c4e68b62954e056d6", 19528406)


def engine_artifacts(system, machine, acceleration="gpu"):
    if acceleration not in {"gpu", "cpu"}:
        raise ValueError("Неизвестный режим ускорения")
    if system == "Darwin" and machine.lower() in {"arm64", "aarch64"}:
        return (ENGINE,)
    if system == "Windows" and machine.lower() in {"amd64", "x86_64"}:
        if acceleration == "cpu":
            return (WINDOWS_CPU_ENGINE,)
        return WINDOWS_ENGINE, WINDOWS_CUDART
    raise ValueError("Локальный движок поддерживает macOS ARM64 и Windows x64")
