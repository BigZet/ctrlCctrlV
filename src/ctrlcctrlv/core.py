"""Cancellation and optional technical deadlines, separate from speed metrics."""
from dataclasses import dataclass, field
import threading
import time
from typing import Callable


class OperationError(Exception):
    pass


class Cancelled(OperationError):
    pass


class DeadlineExceeded(OperationError):
    pass


@dataclass
class Operation:
    started: float = field(default_factory=time.monotonic)
    budget: float | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)

    def check(self) -> float | None:
        if self.cancelled.is_set():
            raise Cancelled("Операция отменена")
        if self.budget is None:
            return None
        remaining = self.budget - (time.monotonic() - self.started)
        if remaining <= 0:
            message = "OCR превысил 4 секунды. Буфер не изменён." if self.budget == 4 else f"Операция превысила {self.budget:g} с. Буфер не изменён."
            raise DeadlineExceeded(message)
        return remaining

    def cancel(self):
        self.cancelled.set()

    def publish(self, text: str, write: Callable[[str], None]) -> float:
        self.check()
        if not isinstance(text, str) or not text.strip():
            raise OperationError("Текст не найден. Буфер не изменён.")
        write(text)
        elapsed = time.monotonic() - self.started
        if self.budget is not None and elapsed > self.budget:
            # The clipboard write itself cannot be rolled back atomically.
            raise DeadlineExceeded(f"Запись в буфер превысила технический срок {self.budget:g} с.")
        return elapsed
