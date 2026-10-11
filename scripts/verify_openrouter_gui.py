"""Owned synthetic screen selection through the native Windows Qt GUI.

One inexpensive generation, then preview editing and explicit copying. Global
hotkeys are disabled in this diagnostic to preserve the user's running app.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import time
import uuid

from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtGui import QImage, QPixmap, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from ctrlcctrlv.app import Controller, diagnostic_print
from ctrlcctrlv.core import OperationError
from ctrlcctrlv.settings import Settings
from scripts.benchmark_review import edit_distance
from scripts.verify_openrouter import MODEL, MeteredProvider


def wait(app, condition, timeout):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    if not condition():
        raise OperationError("Owned GUI diagnostic timed out")


def pixels(image):
    return image.convertToFormat(QImage.Format.Format_RGB32)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/openrouter-gui.json"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This native GUI diagnostic requires Windows")
    os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    clipboard = app.clipboard()
    from PySide6.QtCore import QMimeData
    previous = QMimeData()
    current = clipboard.mimeData()
    if current:
        for name in current.formats():
            previous.setData(name, current.data(name))
    sequence_number = ctypes.WinDLL("user32").GetClipboardSequenceNumber
    sequence_number.restype = ctypes.c_uint32
    owned_sequence = None
    report = {"schema": 1, "model": MODEL, "fixture": "python-light-20",
              "generation_limit": 1, "max_output_tokens": 512,
              "application_build": "source_controller",
              "clipboard_backend": app.platformName(), "global_hotkeys_enabled": False,
              "checks": {}, "requests": []}
    checks = report["checks"]
    controller = None
    fixture_window = QLabel(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    directory = Path("artifacts/openrouter-gui") / uuid.uuid4().hex
    publication = {}
    try:
        reference = QImage("artifacts/fixtures/python-light-20.png")
        expected = Path("artifacts/fixtures/python-light-20.txt").read_text(encoding="utf-8")
        if reference.isNull() or reference.size().width() != 1000 or reference.size().height() != 252:
            raise OperationError("Unexpected synthetic fixture")
        screen = next((s for s in app.screens() if s.devicePixelRatio() == 1 and s.geometry().width() > 1100), None)
        if screen is None:
            raise OperationError("A native 100% display is required by this diagnostic")
        Settings(provider="openrouter", remote_model=MODEL, review_remote_model=MODEL,
                 show_preview=True, max_tokens=512).save(directory)
        controller = Controller(app, directory, native=False)
        production_provider = controller.provider
        metered = None

        def provider():
            nonlocal metered
            actual = production_provider()
            if actual.model != MODEL or actual.local or actual.max_tokens != 512:
                raise OperationError("Unexpected diagnostic provider configuration")
            if metered is None:
                metered = MeteredProvider(actual.endpoint, actual.model, actual.key,
                                         actual.max_tokens, actual.local, actual.mode)
                report["requests"] = metered.records
            return metered

        controller.provider = provider
        real_selected = controller.selected

        def selected(image):
            publication["image_ready_at"] = time.monotonic()
            matches = image.size() == reference.size() and pixels(image) == pixels(reference)
            checks["selected_pixels_match_owned_fixture"] = matches
            if not matches:
                controller.selection_cancelled()
                return
            real_selected(image)

        controller.selected = selected
        real_complete = controller.ocr_complete

        def complete(operation, image, actual_provider, text):
            real_publish = operation.publish

            def publish(value, write):
                def recorded_write(result):
                    nonlocal owned_sequence
                    write(result)
                    owned_sequence = sequence_number()
                    publication["clipboard_written_at"] = time.monotonic()
                    checks["publication_on_gui_thread"] = QThread.currentThread() == app.thread()
                return real_publish(value, recorded_write)

            operation.publish = publish
            real_complete(operation, image, actual_provider, text)

        controller.ocr_complete = complete
        controller.show_settings()
        fixture_window.setWindowTitle("ctrlCctrlV · Synthetic OCR fixture")
        fixture_window.setPixmap(QPixmap.fromImage(reference))
        fixture_window.setFixedSize(reference.size())
        fixture_window.move(screen.geometry().topLeft() + QPoint(60, 120))
        fixture_window.show()
        fixture_window.raise_()
        app.processEvents()
        QTest.qWait(700)
        checks["capture_button_available"] = controller.window.capture_button.isEnabled()
        QTest.mouseClick(controller.window.capture_button, Qt.MouseButton.LeftButton)
        wait(app, lambda: bool(controller.overlays), 5)
        overlay = next(o for o in controller.overlays if o.geometry() == screen.geometry())
        start = fixture_window.mapToGlobal(QPoint(0, 0)) - screen.geometry().topLeft()
        end = start + QPoint(reference.width() - 1, reference.height() - 1)
        QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(overlay, end)
        QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=end)
        if not checks.get("selected_pixels_match_owned_fixture"):
            raise OperationError("Selected crop is not the owned synthetic image; nothing uploaded")
        fixture_window.hide()
        wait(app, lambda: not controller.busy() and controller.pool.activeThreadCount() == 0, 30)
        original = controller.preview.text.toPlainText()
        checks["preview_visible"] = controller.preview.isVisible()
        checks["preview_matches_system_clipboard"] = bool(original) and clipboard.text() == original
        checks["preview_keeps_source_image"] = pixels(controller.preview.source_image) == pixels(reference)
        if "clipboard_written_at" not in publication:
            raise OperationError("OCR result was not published")
        elapsed = publication["clipboard_written_at"] - publication["image_ready_at"]
        owned_sequence = sequence_number()
        report["ocr"] = {"elapsed": elapsed, "passed_4s": elapsed <= 4,
                         "output_characters": len(original), "expected_characters": len(expected),
                         "exact": original == expected,
                         "exact_ignoring_final_newline": original.rstrip("\n") == expected.rstrip("\n"),
                         "character_errors": edit_distance(expected, original)}
        checks["slow_result_notice_correct"] = ("дольше цели 4 с" in controller.preview.status.text()) == (elapsed > 4)
        editor = controller.preview.text
        editor.moveCursor(QTextCursor.MoveOperation.End)
        QTest.keyClicks(editor, " # manual")
        checks["editing_does_not_automatically_publish"] = clipboard.text() == original
        copy_button = next(b for b in controller.preview.findChildren(QPushButton)
                           if b.text() == "Копировать текст")
        QTest.mouseClick(copy_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        edited = original + " # manual"
        checks["manual_edit_explicit_copy"] = editor.toPlainText() == edited and clipboard.text() == edited
        owned_sequence = sequence_number()
        checks["worker_released"] = not controller.jobs and controller.pool.activeThreadCount() == 0
        costs = [row["cost_usd"] for row in report["requests"]]
        report["total_cost_usd"] = sum(costs) if costs and all(cost is not None for cost in costs) else None
    except Exception as error:
        report["error_type"] = type(error).__name__
        checks["operation_succeeded"] = False
    finally:
        # Restore only our publication, never a later user clipboard update.
        if owned_sequence is not None and sequence_number() == owned_sequence:
            clipboard.setMimeData(previous)
            report["clipboard_restored"] = True
        else:
            report["clipboard_restored"] = False
        fixture_window.close()
        if controller:
            controller.quit()
            controller.pool.waitForDone(5000)
            app.processEvents()
            controller.finish_shutdown()
        report["passed"] = bool(checks) and all(checks.values())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        diagnostic_print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
