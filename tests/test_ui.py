import pytest
import csv
import io
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit
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
        window.show_configuration("ocr")
        reached = tab_cycle(app, window, window.navigation)
        assert window.provider in reached
        assert window.local_model in reached
        assert window.show_preview in reached
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
        window.propose(ReviewResult(window.original_text, "first = 0\nunchanged\nlast = 0\n", "m", 1))
        app.processEvents()
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
        assert window.fit_image.isChecked()
        assert window.image_view.original_pixmap.size() == image.size()
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
        assert window.save_hint.text() == ''
        initial = window.delay.value()
        window.delay.setValue(initial + 1)
        assert window.save_hint.text() == 'Изменено'
        window.delay.setValue(initial)
        assert window.save_hint.text() == ''
        window.key.setText('synthetic-key')
        window.show_key.setChecked(True)
        window.key.clear()
        assert not window.show_key.isChecked()
        assert window.key.echoMode() == window.key.EchoMode.Password
        window.cancel_hotkey.setText('ctrl+shift+esc')
        assert 'ctrl+shift+esc' in window.typing_hint.text()
        window.mark_saved()
        assert window.save_hint.text() == ''
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
        assert window.copy_state.text() == 'Есть правки'
        assert 'правки' in window.status.text()
        published = []
        window.copy_requested.connect(published.append)
        window.copy_shortcut.activated.emit()
        assert published == [original + '# manual']
        window.mark_copied(published[0])
        assert window.copy_state.text() == ''
        window.text.undo()
        assert window.text.toPlainText() == original
        assert window.copy_state.text() == 'Есть правки'
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
            assert ('#111318' if dark else '#f5f6f8') in window.styleSheet()
            assert window.palette().color(QPalette.ColorRole.Text).lightness() > 128 if dark else window.palette().color(QPalette.ColorRole.Text).lightness() < 128
    finally:
        window.close()
        window.deleteLater()
        app.setPalette(original)


def test_csv_preview_edits_cells_and_copies_valid_csv_with_undo(app):
    window = Preview()
    original = 'Товар,Цена,Примечание\n"Папка, А4","89,00","строка 1\nстрока 2"\nРучка,35,\n'
    try:
        window.display(QImage(100, 60, QImage.Format.Format_RGB32), original, .1, 'ocr')
        assert window.table.isVisible()
        assert not window.text.isVisible()
        assert window.table.item(1, 0).text() == 'Папка, А4'
        assert window.table.item(1, 2).text() == 'строка 1\nстрока 2'
        assert window.table.item(2, 2).text() == ''
        window.table.item(1, 1).setText('90,50')
        copied = []
        window.copy_requested.connect(copied.append)
        window.copy_button.click()
        rows = list(csv.reader(io.StringIO(copied[0])))
        assert rows[1] == ['Папка, А4', '90,50', 'строка 1\nстрока 2']
        assert rows[2] == ['Ручка', '35', '']
        window.text.undo()
        assert window.table.item(1, 1).text() == '89,00'
        assert window.text.toPlainText() == original
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize('source', ['a,b\n1,2,3', 'a,b\n"unterminated,2', 'print("hello")'])
def test_unrenderable_csv_falls_back_to_unchanged_editable_text(app, source):
    window = Preview()
    try:
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), source, .1, 'ocr', format_mode='csv')
        assert not window.table.isVisible()
        assert window.text.isVisible()
        assert window.text.toPlainText() == source
        window.text.insertPlainText('edit ')
        assert 'edit ' in window.text.toPlainText()
    finally:
        window.close()
        window.deleteLater()


def test_csv_render_error_preserves_original_in_text_editor(app, monkeypatch):
    window = Preview()
    original = 'Товар,Цена\n"Папка, картонная","89,00"\n'
    monkeypatch.setattr(window.table, 'display_rows', lambda *a: (_ for _ in ()).throw(RuntimeError('render failed')))
    try:
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), original, .1, 'ocr')
        assert window.text.isVisible()
        assert window.text.toPlainText() == original
    finally:
        window.close()
        window.deleteLater()


def test_csv_copy_shortcut_commits_active_cell_editor(app):
    window = Preview()
    try:
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), 'Item,Qty\nPaper,1\n', .1, 'ocr')
        window.table.editItem(window.table.item(1, 1))
        editor = window.table.findChild(QLineEdit)
        assert editor is not None
        editor.setText('999')
        copied = []
        window.copy_requested.connect(copied.append)
        window.copy_shortcut.activated.emit()
        assert list(csv.reader(io.StringIO(copied[0])))[1] == ['Paper', '999']
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize('source', ['print(1, 2)\nprint(3, 4)', 'x = a,b\ny = c,d', 'SELECT a,b\nFROM x,y'])
def test_code_with_commas_is_not_automatically_rendered_as_csv(app, source):
    window = Preview()
    try:
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), source, .1, 'ocr')
        assert window.text.isVisible()
        assert not window.is_csv
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize('kind', ['csv', 'xlsx'])
def test_table_export_buttons_commit_active_edit_and_keep_preview_open(app, monkeypatch, tmp_path, kind):
    import ctrlcctrlv.table_export as exports
    window = Preview()
    target = tmp_path / ('Таблица.' + kind)
    calls = []
    real_export = exports.export_table
    def record(path, text, format_name):
        calls.append((path, text, format_name))
        return real_export(path, text, format_name)
    monkeypatch.setattr(exports, 'export_table', record)
    monkeypatch.setattr(window, 'choose_export_path', lambda fmt: str(target))
    try:
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), 'Name,Qty\nPaper,1\n', .1, 'ocr')
        assert window.export_csv_button.isVisible() and window.export_xlsx_button.isVisible()
        window.table.editItem(window.table.item(1, 1))
        window.table.findChild(QLineEdit).setText('00123')
        getattr(window, 'export_' + kind + '_button').click()
        assert calls == [(str(target), 'Name,Qty\nPaper,00123\n', kind)]
        assert target.exists() and window.isVisible()
        assert window.copied_text == 'Name,Qty\nPaper,1\n'
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), 'plain text', .1, 'ocr')
        assert not window.export_csv_button.isVisible() and not window.export_xlsx_button.isVisible()
    finally:
        window.close()
        window.deleteLater()


def test_table_export_cancel_and_write_error_preserve_preview(app, monkeypatch):
    import ctrlcctrlv.ui as ui
    import ctrlcctrlv.table_export as exports
    window = Preview()
    try:
        original = 'Name,Qty\nPaper,1\n'
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), original, .1, 'ocr')
        monkeypatch.setattr(window, 'choose_export_path', lambda fmt: None)
        monkeypatch.setattr(exports, 'export_table', lambda *a: pytest.fail('Cancelled export must not write'))
        window.export_csv_button.click()
        assert window.isVisible() and window.text.toPlainText() == original
        monkeypatch.setattr(window, 'choose_export_path', lambda fmt: 'locked.csv')
        def fail(*args):
            raise PermissionError('File is locked')
        monkeypatch.setattr(exports, 'export_table', fail)
        errors = []
        monkeypatch.setattr(ui.QMessageBox, 'critical', lambda parent, title, detail: errors.append(detail))
        window.export_csv_button.click()
        assert errors == ['File is locked']
        assert window.isVisible() and window.text.toPlainText() == original
        assert 'Сохранено' not in window.status.text()
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize('filename', ['код', 'код.custom'])
def test_save_file_dialog_suggests_python_and_saves_current_edits(app, monkeypatch, tmp_path, filename):
    import ctrlcctrlv.ui as ui
    window = Preview()
    original = 'plain text'
    edited = '\n    print("Привет 🙂")  \n'
    expected_path = tmp_path / (filename + '.py' if '.' not in filename else filename)
    def accept(dialog):
        assert dialog.acceptMode() == ui.QFileDialog.AcceptMode.AcceptSave
        assert dialog.defaultSuffix() == 'py'
        assert dialog.selectedNameFilter() == 'Python (*.py)'
        assert not dialog.testOption(ui.QFileDialog.Option.DontConfirmOverwrite)
        dialog.selectFile(str(tmp_path / filename))
        return ui.QDialog.DialogCode.Accepted
    monkeypatch.setattr(ui.QFileDialog, 'exec', accept)
    try:
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), original, .1, 'ocr')
        assert window.save_file_button.isVisible()
        window.text.setPlainText(edited)
        window.save_file_button.click()
        assert expected_path.read_bytes() == edited.encode('utf-8')
        assert window.isVisible() and window.copied_text == original
        assert window.text.toPlainText() == edited
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), 'Name,Qty\nPaper,1\n', .1, 'ocr')
        assert not window.save_file_button.isVisible()
        assert window.export_csv_button.isVisible() and window.export_xlsx_button.isVisible()
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), '', .1, 'ocr')
        assert not window.save_file_button.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_save_file_cancel_and_error_keep_text_and_preview(app, monkeypatch):
    import ctrlcctrlv.ui as ui
    import ctrlcctrlv.text_export as exports
    window = Preview()
    try:
        original = 'print("Привет")\n'
        window.display(QImage(10, 10, QImage.Format.Format_RGB32), original, .1, 'ocr')
        monkeypatch.setattr(window, 'choose_text_path', lambda extension: None)
        monkeypatch.setattr(exports, 'save_text', lambda *a: pytest.fail('Cancelled save must not write'))
        window.save_file_button.click()
        monkeypatch.setattr(window, 'choose_text_path', lambda extension: 'locked.py')
        def fail(*args):
            raise PermissionError('File is locked')
        monkeypatch.setattr(exports, 'save_text', fail)
        errors = []
        monkeypatch.setattr(ui.QMessageBox, 'critical', lambda parent, title, detail: errors.append(detail))
        window.save_file_button.click()
        assert errors == ['File is locked']
        assert window.isVisible() and window.text.toPlainText() == original
        assert window.copied_text == original and 'Сохранено' not in window.status.text()
    finally:
        window.close()
        window.deleteLater()
