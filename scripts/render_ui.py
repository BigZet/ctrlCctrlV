"""Render synthetic UI states without screen capture, permissions or clipboard access."""
import argparse
import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEvent, QPoint, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QToolButton

from ctrlcctrlv.review import ReviewResult
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.syntax import syntax_hint
from ctrlcctrlv.ui import DiagnosticsDialog, Preview, SelectionOverlay, SettingsWindow
from scripts.generate_fixtures import generate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/ui"))
    parser.add_argument("--compact", action="store_true", help="Проверить минимальный размер окон")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    fixtures = args.output / "fixtures"
    generate(fixtures)
    windows = []
    for dark in (False, True):
        theme = "dark" if dark else "light"
        palette = QPalette(app.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor("#151a23" if dark else "#f5f7fa"))
        app.setPalette(palette)
        screen = app.primaryScreen()
        snapshot = QImage(screen.geometry().size(), QImage.Format.Format_RGB32)
        snapshot.fill(QColor('#202733' if dark else '#e4eaf3'))
        painter = QPainter(snapshot)
        painter.drawImage(20, 100, QImage(str(fixtures / f'python-{theme}-20.png')))
        painter.end()
        overlay = SelectionOverlay(screen, QPixmap.fromImage(snapshot))
        overlay.origin, overlay.position = QPoint(20, 100), QPoint(619, 351)
        windows.append(overlay)
        overlay.show()
        app.processEvents()
        overlay.grab().save(str(args.output / f'{theme}-selection.png'))
        overlay.hide()
        from ctrlcctrlv.ui import SetupWizard
        setup = SetupWizard()
        windows.append(setup)
        setup.show()
        app.processEvents()
        setup.grab().save(str(args.output / f"{theme}-setup.png"))
        setup.next()
        app.processEvents()
        setup.grab().save(str(args.output / f"{theme}-setup-local.png"))
        setup.back()
        setup.provider.setCurrentIndex(1)
        setup.next()
        app.processEvents()
        setup.grab().save(str(args.output / f"{theme}-setup-cloud.png"))
        setup.hide()
        settings = SettingsWindow(Settings())
        if args.compact:
            settings.resize(820, 620)
        settings.show_permissions({"screen": True, "accessibility": True, "input_monitoring": True})
        settings.show()
        windows.append(settings)
        for page, name in enumerate(("ocr", "typing", "system")):
            settings.navigation.setCurrentRow(page)
            app.processEvents()
            settings.grab().save(str(args.output / f"{theme}-{name}.png"))
        settings.navigation.setCurrentRow(0)
        settings.hardware_info.setText("Windows AMD64\nRAM 63.9 ГБ\nNVIDIA GeForce RTX 3080 · свободно 8.8 ГБ\nОценка для выбранной модели: 2.2 ГБ; фактический расход зависит от контекста и снимка")
        advanced = next(tool for tool in settings.findChildren(QToolButton) if tool.text() == "Дополнительно" and tool.isVisible())
        advanced.click()
        app.processEvents()
        settings.grab().save(str(args.output / f"{theme}-hardware.png"))
        advanced.click()
        settings.status.setText("Не удалось подключиться к OCR. Буфер не изменён.")
        settings.retry_button.show()
        app.processEvents()
        settings.grab().save(str(args.output / f"{theme}-ocr-retry.png"))
        settings.retry_button.hide()
        settings.status.setText("Распознавание… 7 с · цель 4 с")
        settings.set_busy(True)
        settings.progress.setRange(0, 0)
        app.processEvents()
        settings.grab().save(str(args.output / f"{theme}-ocr-slow.png"))
        settings.set_busy(False)
        settings.status.setText("Подготовьте модель и выделите область")
        settings.provider.setCurrentIndex(1)
        app.processEvents()
        settings.grab().save(str(args.output / f"{theme}-cloud.png"))
        settings.remote_model.setText("example/vision-model")
        app.processEvents()
        settings.grab().save(str(args.output / f"{theme}-pending.png"))
        preview = Preview()
        if args.compact:
            preview.resize(840, 620)
        windows.append(preview)
        expected = (fixtures / f"python-{theme}-20.txt").read_text(encoding="utf-8")
        original = expected.replace("Hello,", "He11o,")
        preview.display(QImage(str(fixtures / f"python-{theme}-20.png")), original, 0.8, "Синтетический пример")
        app.processEvents()
        preview.grab().save(str(args.output / f"{theme}-preview.png"))
        preview.fit_image.setChecked(True)
        app.processEvents()
        preview.grab().save(str(args.output / f"{theme}-preview-fitted.png"))
        preview.fit_image.setChecked(False)
        preview.propose(ReviewResult(original, expected, "checker", 2.7))
        app.processEvents()
        preview.grab().save(str(args.output / f"{theme}-review.png"))
        preview.reject_review()
        preview.text.insertPlainText("# Ручная правка\n")
        preview.whitespace.setChecked(True)
        preview.wrap_text.setChecked(True)
        preview.font_size.setCurrentIndex(2)
        app.processEvents()
        preview.grab().save(str(args.output / f"{theme}-edited.png"))
        preview.set_review_busy(True)
        preview.review_panel.hide()
        preview.status.setText("Сверка текста со снимком…")
        app.processEvents()
        preview.grab().save(str(args.output / f"{theme}-checking.png"))
        preview.set_review_busy(False)
        source = (fixtures / f"json-{theme}-20.txt").read_text(encoding="utf-8")
        broken = source.replace('"name": "Тест",', '"name": "Тест"')
        preview.display(QImage(str(fixtures / f"json-{theme}-20.png")), broken, 0.8, "Синтетический пример")
        preview.syntax_language.setCurrentIndex(preview.syntax_language.findData("json"))
        preview.show_syntax_hint(preview.syntax_revision, syntax_hint(broken, "json"))
        app.processEvents()
        preview.grab().save(str(args.output / f"{theme}-syntax-warning.png"))
        diagnostics = DiagnosticsDialog()
        diagnostics.display('{\n  "application": "0.1.0",\n  "system": "Windows",\n  "architecture": "AMD64",\n  "permissions": {\n    "accessibility": true,\n    "screen": true,\n    "input_monitoring": true\n  },\n  "ocr_runtime": {\n    "state": "stopped",\n    "ready": false\n  }\n}')
        windows.append(diagnostics)
        app.processEvents()
        diagnostics.grab().save(str(args.output / f"{theme}-diagnostics.png"))
    def finish():
        for window in windows:
            window.close()
            window.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.quit()
    QTimer.singleShot(100, finish)
    app.exec()
    print(f"Rendered UI states in {args.output.resolve()}")


if __name__ == "__main__":
    main()
