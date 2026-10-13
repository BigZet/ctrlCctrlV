"""Session-only capture coordinates; no screenshot or region is persisted."""
from collections.abc import Sequence
from dataclasses import dataclass
import math
import uuid

from PySide6.QtCore import QRect


@dataclass(frozen=True)
class ScreenDescriptor:
    identity: str
    geometry: tuple[int, int, int, int]
    dpr: float


@dataclass(frozen=True)
class CaptureRegion:
    screen: ScreenDescriptor
    rect: tuple[int, int, int, int]
    topology: tuple[ScreenDescriptor, ...]


def describe_screen(screen) -> ScreenDescriptor:
    # A Qt dynamic property survives Python wrapper recreation but not unplugging.
    token = screen.property('_ctrlc_capture_identity')
    if not token:
        token = uuid.uuid4().hex
        screen.setProperty('_ctrlc_capture_identity', token)
    identity = '|'.join((str(token), screen.name(), screen.manufacturer(), screen.model(), screen.serialNumber()))
    return ScreenDescriptor(identity, screen.geometry().getRect(), float(screen.devicePixelRatio()))


def physical_rect(rect: QRect, dpr: float) -> QRect:
    if not math.isfinite(dpr) or dpr <= 0:
        raise ValueError('Invalid screen scale')
    return QRect(round(rect.x() * dpr), round(rect.y() * dpr),
                 round(rect.width() * dpr), round(rect.height() * dpr))


def region_is_valid(region: CaptureRegion, screens: Sequence[ScreenDescriptor]) -> bool:
    current = tuple(screens)
    if (not current or len({screen.identity for screen in current}) != len(current)
            or len({screen.identity for screen in region.topology}) != len(region.topology)
            or set(current) != set(region.topology) or region.screen not in current):
        return False
    rect = QRect(*region.rect)
    width, height = region.screen.geometry[2:]
    return (math.isfinite(region.screen.dpr) and region.screen.dpr > 0
            and rect.width() >= 3 and rect.height() >= 3
            and QRect(0, 0, width, height).contains(rect))
