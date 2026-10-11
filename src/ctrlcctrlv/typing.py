"""Cancellable typing policy shared by native adapters."""
import time

from .core import Cancelled, OperationError
from .platforms import active_target


def type_text(platform, text, settings, cancelled, progress,
              expected_target_pid=None, expected_target=None):
    if not platform.permissions()["accessibility"]:
        raise OperationError("Разрешите управление вводом в разделе «Система»")
    deadline = time.monotonic() + 5
    while platform.modifiers_pressed():
        if cancelled.wait(0.02):
            raise Cancelled("Печать остановлена")
        if time.monotonic() >= deadline:
            raise OperationError("Отпустите клавиши сочетания перед печатью")
    target = active_target(platform)
    if ((expected_target is not None and target != expected_target) or
            (expected_target_pid is not None and platform.active_pid() != expected_target_pid)):
        raise Cancelled("Печать остановлена: активное приложение или окно изменилось")
    if target is None:
        raise OperationError("Нет активного окна для печати")

    def check():
        if cancelled.is_set():
            raise Cancelled("Печать остановлена")
        if active_target(platform) != target:
            raise Cancelled("Печать остановлена: активное приложение или окно изменилось")
        if platform.modifiers_pressed():
            raise Cancelled("Печать остановлена: нажата клавиша-модификатор")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for index, char in enumerate(text):
        check()
        if char == "\n":
            platform.enter()
            cancelled.wait(settings.newline_delay)
            check()
            if settings.autoindent and index + 1 < len(text):
                platform.select_indentation()
        elif char == "\t":
            if settings.spaces_per_tab:
                for _ in range(settings.spaces_per_tab):
                    check()
                    platform.character(" ")
                    cancelled.wait(settings.character_delay)
            else:
                platform.tab()
        else:
            platform.character(char)
        progress(index + 1, len(text))
        cancelled.wait(settings.character_delay)
    check()
