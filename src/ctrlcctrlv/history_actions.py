"""One recent history deletion, held only in this process's memory."""
from collections.abc import Callable
import time

from .history import Entry


class HistoryUndo:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._pending: tuple[Entry, bool] | None = None
        self._deadline = 0.0

    def remember(self, entry: Entry, pinned: bool) -> None:
        self._pending = (entry, pinned)
        self._deadline = self._clock() + 30.0

    def peek(self) -> tuple[Entry, bool] | None:
        if self._pending is not None and self._clock() >= self._deadline:
            self.clear()
        return self._pending

    def clear(self) -> None:
        self._pending = None
        self._deadline = 0.0
