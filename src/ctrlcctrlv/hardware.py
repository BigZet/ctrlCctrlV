"""Read hardware capacity without loading a model or silently choosing CPU."""
import os
import platform
import re
import subprocess
import sys

from .core import Cancelled, OperationError

MIB = 1024 ** 2
GIB = 1024 ** 3


class InsufficientMemory(OperationError):
    """Preparation may be retried after releasing another owned model."""


def memory_capacity():
    if sys.platform == "win32":
        from .windows import memory_capacity as windows_capacity
        return windows_capacity()
    from .diagnostics import system_memory
    total = system_memory()
    available = None
    if sys.platform == "darwin":
        try:
            output = subprocess.run(["/usr/bin/vm_stat"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    encoding="utf-8", timeout=3, check=True).stdout
            page_size = re.search(r"page size of (\d+) bytes", output)
            pages = dict(re.findall(r"^(Pages (?:free|inactive|speculative)):\s*(\d+)\.", output, re.MULTILINE))
            if page_size and all(name in pages for name in ("Pages free", "Pages inactive", "Pages speculative")):
                # Free and reclaimable inactive/speculative pages, as an estimate.
                available = min(total, int(page_size[1]) * sum(map(int, pages.values()))) if total else None
        except (OSError, subprocess.SubprocessError):
            pass
    return total, available


def estimate_memory(model, slots=1):
    # Explicit, conservative preparation estimate; actual model allocations
    # depend on backend/context and are still checked by the inference engine.
    return int(sum(artifact.size for artifact in model.files) * 1.25 + GIB * slots)


def inspect_hardware(binary, acceleration, cancel):
    if cancel.is_set():
        raise Cancelled("Проверка оборудования отменена")
    total, available = memory_capacity()
    report = {"system": platform.system(), "architecture": platform.machine(),
              "physical_memory_bytes": total, "available_memory_bytes": available,
              "acceleration": acceleration, "devices": []}
    if acceleration == "gpu":
        if binary is None:
            raise OperationError("Сначала загрузите модель в разделе «Модели»")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            result = subprocess.run([str(binary), "--list-devices"], cwd=binary.parent,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    encoding="utf-8", errors="replace", timeout=10, **options)
        except (OSError, subprocess.SubprocessError):
            raise OperationError("Не удалось проверить GPU. Проверьте драйвер и установку движка или выберите CPU / OpenRouter.") from None
        if result.returncode != 0:
            raise OperationError("Движок не обнаружил доступный GPU. Проверьте драйвер или выберите CPU / OpenRouter.")
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            match = re.fullmatch(r"\s*([\w-]+):\s*(.{1,120}?)\s*\((\d+) MiB,\s*(\d+) MiB free\)\s*", line)
            if match and not match[1].upper().startswith("CPU"):
                report["devices"].append({"backend": match[1], "name": match[2].strip(),
                                          "total_bytes": int(match[3]) * MIB,
                                          "available_bytes": int(match[4]) * MIB})
        if not report["devices"]:
            raise OperationError("GPU недоступен. На Windows нужен драйвер NVIDIA; можно явно выбрать CPU или OpenRouter.")
        if sys.platform == "win32":
            from .windows import nvidia_memory_capacity
            global_devices = nvidia_memory_capacity()
            for device in report["devices"]:
                matches = [entry for entry in global_devices if entry["name"] == device["name"]]
                if matches:
                    # Identical cards may have different ordinals in CUDA and
                    # NVML; the least free matching card is a safe lower bound.
                    device["available_bytes"] = min(device["available_bytes"],
                                                     min(entry["available_bytes"] for entry in matches))
                    device["memory_source"] = "CUDA / NVML"
                else:
                    device["memory_source"] = "CUDA budget; global memory unknown"
    if cancel.is_set():
        raise Cancelled("Проверка оборудования отменена")
    return report


def require_capacity(report, model, slots=1):
    needed = estimate_memory(model, slots)
    available = report["available_memory_bytes"]
    if available is None:
        available = report["physical_memory_bytes"]
    if report["acceleration"] == "gpu":
        available = max((device["available_bytes"] for device in report["devices"]), default=0)
    if available is not None and available < needed:
        memory = "памяти GPU" if report["acceleration"] == "gpu" else "оперативной памяти"
        raise InsufficientMemory(f"Недостаточно {memory}: доступно {available / GIB:.1f} ГБ, "
                             f"предварительная оценка для модели {needed / GIB:.1f} ГБ. "
                             "Выберите меньшую модель, CPU или OpenRouter.")
    return needed


def describe_hardware(report, model):
    parts = [f"{report['system']} {report['architecture']}"]
    if report["physical_memory_bytes"]:
        ram = f"RAM {report['physical_memory_bytes'] / GIB:.1f} ГБ"
        if report["available_memory_bytes"] is not None:
            ram += f" · доступно около {report['available_memory_bytes'] / GIB:.1f} ГБ"
        parts.append(ram)
    for device in report["devices"]:
        parts.append(f"{device['name']} · свободно {device['available_bytes'] / GIB:.1f} ГБ")
        if device.get("memory_source") == "CUDA budget; global memory unknown":
            parts.append("Общая память GPU неизвестна; показана оценка CUDA, модели проверяются последовательно")
    if report["acceleration"] == "cpu":
        parts.append("CPU · GPU не используется")
    parts.append(f"Оценка для выбранной модели: {estimate_memory(model) / GIB:.1f} ГБ; "
                 "фактический расход зависит от контекста и снимка")
    return "\n".join(parts)
