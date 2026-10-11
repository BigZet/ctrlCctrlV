import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage

from ctrlcctrlv.review import ReviewResult
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.ui import Preview, SettingsWindow
from scripts.benchmark_tiles import known_tiles


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def tab_cycle(app, window, first, modifier=Qt.KeyboardModifier.NoModifier):
    window.show()
    window.activateWindow()
    first.setFocus()
    app.processEvents()
    reached = []
    for _ in range(50):
        focused = app.focusWidget()
        reached.append(focused)
        QTest.keyClick(focused or window, Qt.Key.Key_Tab, modifier)
        app.processEvents()
    return reached


def test_local_and_cloud_settings_are_reachable_without_tabbing_into_hidden_pages(app):
    window = SettingsWindow(Settings())
    try:
        window.resize(780, 610)
        reached = tab_cycle(app, window, window.navigation)
        assert window.provider in reached
        assert window.local_model in reached
        assert window.show_preview in reached
        assert window.capture_button in reached
        assert window.key not in reached
        window.provider.setCurrentIndex(1)
        reached = tab_cycle(app, window, window.navigation)
        assert window.remote_model in reached
        assert window.key in reached
        assert window.local_model not in reached
    finally:
        window.close()
        window.deleteLater()


def test_preview_can_leave_editor_and_select_review_changes_using_keyboard(app):
    window = Preview()
    window.original_text = "first = O\nunchanged\nlast = O\n"
    window.text.setPlainText(window.original_text)
    window.propose(ReviewResult(window.original_text, "first = 0\nunchanged\nlast = 0\n", "m", 1))
    try:
        window.show()
        window.activateWindow()
        window.text.setFocus()
        app.processEvents()
        original = window.text.toPlainText()
        QTest.keyClick(window.text, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert app.focusWidget() is not window.text
        assert window.text.toPlainText() == original
        choice = window.review_choices[0]
        choice.setFocus()
        app.processEvents()
        QTest.keyClick(choice, Qt.Key.Key_Space)
        assert not choice.isChecked()
        assert window.review_result.proposed == "first = O\nunchanged\nlast = 0\n"
        assert window.text.accessibleName()
        assert window.syntax_language.accessibleName()
        assert window.diff.accessibleName()
    finally:
        window.syntax_timer.stop()
        window.close()
        window.deleteLater()


def test_experimental_tiles_cover_entire_image_even_when_rows_are_fewer_than_slots(app):
    image = QImage(100, 100, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    for row in range(image.height()):
        image.setPixel(0, row, 0xff000000 + row)
    tiles = known_tiles(image, {"lines": 2, "font_pixels": 16}, 4)
    assert len(tiles) == 2
    assert sum(tile.height() for tile in tiles) == image.height()
    pixels = [tile.pixel(0, row) for tile in tiles for row in range(tile.height())]
    assert pixels == [image.pixel(0, row) for row in range(image.height())]


@pytest.mark.parametrize('pixel_ratio', [1.0, 2.0])
def test_preview_fits_and_restores_full_source_image_on_standard_and_retina_screens(app, pixel_ratio):
    window = Preview()
    image = QImage(1200, 1600, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    image.setPixel(11, 17, 0xff123456)
    image.setDevicePixelRatio(pixel_ratio)
    try:
        window.display(image, 'literal text', 1, 'synthetic')
        window.show()
        app.processEvents()
        assert window.image.pixmap().deviceIndependentSize() == image.deviceIndependentSize()
        assert window.image_view.original_pixmap.size() == image.size()
        window.fit_image.click()
        app.processEvents()
        fitted = window.image.pixmap().deviceIndependentSize()
        viewport = window.image_view.viewport().size()
        assert fitted.width() <= viewport.width()
        assert fitted.height() <= viewport.height()
        window.resize(1280, 900)
        app.processEvents()
        assert window.image.pixmap().deviceIndependentSize().height() <= window.image_view.viewport().height()
        assert window.source_image.size() == image.size()
        assert window.source_image.pixel(11, 17) == 0xff123456
        assert window.source_image.devicePixelRatio() == pixel_ratio
        assert window.text.toPlainText() == 'literal text'
        window.fit_image.click()
        app.processEvents()
        # QLabel may adapt its returned pixmap to the current screen's DPR.
        assert window.image.pixmap().deviceIndependentSize() == image.deviceIndependentSize()
        assert window.image_view.original_pixmap.size() == image.size()
        assert window.image_view.original_pixmap.devicePixelRatio() == pixel_ratio
    finally:
        window.syntax_timer.stop()
        window.close()
        window.deleteLater()


def test_pending_settings_reset_only_after_save_and_busy_fields_cannot_change(app):
    window = SettingsWindow(Settings())
    try:
        assert window.save_hint.text() == 'Нет изменений'
        initial = window.delay.value()
        window.delay.setValue(initial + 1)
        assert window.save_hint.text() == 'Есть изменения'
        window.delay.setValue(initial)
        assert window.save_hint.text() == 'Нет изменений'
        window.key.setText('synthetic-key')
        window.show_key.setChecked(True)
        window.key.clear()
        assert not window.show_key.isChecked()
        assert window.key.echoMode() == window.key.EchoMode.Password
        window.cancel_hotkey.setText('ctrl+shift+esc')
        assert 'ctrl+shift+esc' in window.typing_hint.text()
        window.mark_saved()
        assert window.save_hint.text() == 'Нет изменений'
        window.set_busy(True)
        assert not window.delay.isEnabled()
        assert not window.capture_hotkey.isEnabled()
        assert not window.provider.isEnabled()
        window.set_busy(False)
        assert window.delay.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_editor_view_controls_preserve_literal_text_and_track_explicit_copy(app):
    from PySide6.QtGui import QTextOption
    window = Preview()
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(0xffffffff)
    original = 'def greet():\n\tprint("Ж")\n\n'
    try:
        window.display(image, original, 1, 'synthetic')
        window.whitespace.setChecked(True)
        window.wrap_text.setChecked(True)
        window.font_size.setCurrentIndex(4)
        app.processEvents()
        assert window.text.toPlainText() == original
        assert window.text.document().defaultTextOption().flags() & QTextOption.Flag.ShowTabsAndSpaces
        assert window.text.lineWrapMode() == window.text.LineWrapMode.WidgetWidth
        assert window.source_image == image
        window.text.moveCursor(window.text.textCursor().MoveOperation.End)
        window.text.insertPlainText('# manual')
        assert window.copy_state.text() == 'Правки ещё не скопированы'
        assert window.status.text() == 'Текст изменён · нажмите «Копировать текст»'
        published = []
        window.copy_requested.connect(published.append)
        window.copy_shortcut.activated.emit()
        assert published == [original + '# manual']
        window.mark_copied(published[0])
        assert window.copy_state.text().startswith('Совпадает с последней копией')
        window.text.undo()
        assert window.text.toPlainText() == original
        assert window.copy_state.text() == 'Правки ещё не скопированы'
    finally:
        window.syntax_timer.stop()
        window.close()
        window.deleteLater()


def test_existing_windows_follow_system_theme_changes(app):
    from PySide6.QtGui import QColor, QPalette
    original = QPalette(app.palette())
    window = SettingsWindow(Settings())
    try:
        window.show()
        for dark in (True, False):
            palette = QPalette(original)
            palette.setColor(QPalette.ColorRole.Window, QColor('#10151e' if dark else '#f3f5f9'))
            app.setPalette(palette)
            app.processEvents()
            assert ('#10151e' if dark else '#f3f5f9') in window.styleSheet()
            assert window.palette().color(QPalette.ColorRole.Text).lightness() > 128 if dark else window.palette().color(QPalette.ColorRole.Text).lightness() < 128
    finally:
        window.close()
        window.deleteLater()
        app.setPalette(original)
