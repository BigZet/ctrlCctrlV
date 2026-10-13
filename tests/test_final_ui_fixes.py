import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QImage, QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ctrlcctrlv.settings import Settings
from ctrlcctrlv.syntax import SyntaxHint
from ctrlcctrlv.ui import Preview
from ctrlcctrlv.review import ReviewResult
from ctrlcctrlv.workspace_ui import SettingsWindow


@pytest.fixture
def settings_window():
    window = SettingsWindow(Settings())
    window.show_configuration('system')
    window.show()
    window.activateWindow()
    QApplication.instance().processEvents()
    yield window
    window.close()
    window.deleteLater()


@pytest.mark.parametrize('index',[0,1,2])
def test_tab_space_records_each_hotkey_and_escape_restores_binding(settings_window,index,physical_control_modifier):
    field = (settings_window.capture_hotkey,settings_window.typing_hotkey,settings_window.cancel_hotkey)[index]
    record = settings_window.hotkey_record_buttons[index]
    original = field.text()
    field.setFocus()
    QTest.keyClick(field,Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is record
    QTest.keyClick(record,Qt.Key.Key_Space)
    assert field.recording and field.hasFocus()
    assert record.text() == 'Отмена'
    QTest.keyClick(field,Qt.Key.Key_Escape)
    assert not field.recording and field.text() == original
    assert record.text() == 'Записать'
    field.setFocus()
    QTest.keyClick(field,Qt.Key.Key_Tab)
    QTest.keyClick(record,Qt.Key.Key_Space)
    QTest.keyClick(field,Qt.Key.Key_K,physical_control_modifier | Qt.KeyboardModifier.AltModifier)
    assert field.text() == 'ctrl+alt+k'
    assert not field.recording


def test_mouse_record_cancel_does_not_restart_after_focus_changes(settings_window):
    field = settings_window.capture_hotkey
    record = settings_window.hotkey_record_buttons[0]
    original = field.text()
    transitions = []
    field.recording_changed.connect(transitions.append)
    QTest.mouseClick(record,Qt.MouseButton.LeftButton)
    assert field.recording
    QTest.mouseClick(record,Qt.MouseButton.LeftButton)
    QApplication.instance().processEvents()
    assert not field.recording and field.text() == original
    assert transitions == [True,False]


@pytest.fixture
def preview():
    window = Preview()
    window.display_history('{bad json')
    yield window
    window.close()
    window.deleteLater()


def warn(window):
    window.syntax_language.setCurrentIndex(window.syntax_language.findData('json'))
    window.show_syntax_hint(window.syntax_revision,SyntaxHint('JSON',1,'invalid JSON'))


def test_disabling_syntax_without_edit_clears_owned_warning_and_tooltip(preview):
    preview.status.setToolTip('history model')
    warn(preview)
    assert preview.status.property('role') == 'warning'
    preview.syntax_language.setCurrentIndex(preview.syntax_language.findData('none'))
    preview.show_syntax_hint(preview.syntax_revision,None)
    assert preview.status.text() == 'Открыто из истории'
    assert preview.status.toolTip() == 'history model'
    assert preview.status.property('role') == 'muted'
    assert preview.text.syntax_hint is None
    assert preview.text.toPlainText() == '{bad json'


def test_current_null_hint_clears_warning_without_selector_change(preview):
    previous = preview.status.text()
    warn(preview)
    preview.show_syntax_hint(preview.syntax_revision,None)
    assert preview.status.text() == previous
    assert 'invalid' not in preview.status.toolTip()


def test_syntax_cleanup_preserves_new_save_or_unrelated_error(preview,monkeypatch,tmp_path):
    warn(preview)
    target = tmp_path / 'saved.txt'
    monkeypatch.setattr(preview,'choose_text_path',lambda _:str(target))
    assert preview.save_current_text()
    saved = preview.status.text()
    preview.syntax_language.setCurrentIndex(preview.syntax_language.findData('none'))
    preview.show_syntax_hint(preview.syntax_revision,None)
    assert preview.status.text() == saved
    assert 'invalid JSON' not in preview.status.toolTip()
    warn(preview)
    preview.status.setText('Unrelated failure')
    preview.status.setToolTip('Unrelated details')
    preview.status.setProperty('role','error')
    preview.schedule_syntax()
    preview.show_syntax_hint(preview.syntax_revision,None)
    assert preview.status.text() == 'Unrelated failure'
    assert preview.status.toolTip() == 'Unrelated details'
    assert preview.status.property('role') == 'error'


def test_work_format_keeps_preview_option_adjacent(settings_window):
    settings_window.navigation.setCurrentRow(0)
    settings_window.resize(840,620)
    QApplication.instance().processEvents()
    assert 160 <= settings_window.capture_format.width() <= 180
    format_right = settings_window.capture_format.mapTo(settings_window,QPoint()).x() + settings_window.capture_format.width()
    option_left = settings_window.quick_preview.mapTo(settings_window,QPoint()).x()
    assert 0 <= option_left-format_right <= 16


@pytest.mark.parametrize('size',[(840,620),(960,500)])
@pytest.mark.parametrize('dark',[False,True])
def test_table_mode_label_fits_in_compact_toolbar(preview,size,dark,request):
    app = QApplication.instance()
    previous_palette = app.palette()
    request.addfinalizer(lambda: app.setPalette(previous_palette))
    palette = QPalette(previous_palette)
    palette.setColor(QPalette.ColorRole.Window,QColor('#111318' if dark else '#f5f6f8'))
    app.setPalette(palette)
    preview.display(QImage(600,300,QImage.Format.Format_RGB32),'Name,Qty\nPaper,001\n',.1,'test')
    preview.set_result_view('table')
    preview.resize(*size)
    preview.context_toggle.setChecked(True)
    app.processEvents()
    assert preview.result_mode.currentText() == 'Таблица'
    assert preview.result_mode.width() >= preview.result_mode.sizeHint().width()
    assert preview.result_mode.parentWidget().rect().contains(preview.result_mode.geometry())
    assert preview.result_stack.mapTo(preview.centralWidget(),QPoint()).y() <= 150
    for control in (preview.export_csv_button,preview.export_xlsx_button):
        assert control.parentWidget().rect().contains(control.geometry())


@pytest.mark.parametrize('dark',[False,True])
def test_review_footer_preserves_full_actions_and_editors_when_narrow(preview,dark,request):
    app = QApplication.instance()
    previous_palette = app.palette()
    request.addfinalizer(lambda: app.setPalette(previous_palette))
    palette = QPalette(previous_palette)
    palette.setColor(QPalette.ColorRole.Window,QColor('#111318' if dark else '#f5f6f8'))
    app.setPalette(palette)
    preview.display_history('first\nsecond\nthird\n')
    assert preview.propose(ReviewResult('first\nsecond\nthird\n','first\nSecond\nthird\n','test',.1))
    from PySide6.QtWidgets import QPushButton
    buttons = {button.text():button for button in preview.review_panel.findChildren(QPushButton)}
    actions = [buttons[text] for text in ('Предыдущее','Следующее','Отклонить','Применить и скопировать')]
    for width,height in ((600,380),(840,620),(1180,680),(600,380)):
        preview.review_panel.resize(width,height)
        app.processEvents()
        for control in (*actions,preview.comparison_orientation):
            assert control.isVisible()
            assert control.width() >= control.sizeHint().width(), control.text()
            assert preview.review_panel.rect().contains(control.mapTo(preview.review_panel,QPoint()))
            bottom_right = control.mapTo(preview.review_panel,control.rect().bottomRight())
            assert preview.review_panel.rect().contains(bottom_right), (width,height,control.text(),control.geometry(),preview.review_footer.geometry())
        for editor in (preview.before_text,preview.after_text):
            assert editor.isVisible()
            assert editor.viewport().height() >= 30
        navigation_y = actions[0].mapTo(preview.review_panel,QPoint()).y()
        accept_y = preview.accept_button.mapTo(preview.review_panel,QPoint()).y()
        assert (navigation_y < accept_y) == (width == 600)


@pytest.mark.parametrize('table',[False,True])
@pytest.mark.parametrize('dark',[False,True])
def test_image_hide_belongs_to_image_tools_without_extra_editor_row(preview,table,dark,request):
    app = QApplication.instance()
    previous_palette = app.palette()
    request.addfinalizer(lambda: app.setPalette(previous_palette))
    palette = QPalette(previous_palette)
    palette.setColor(QPalette.ColorRole.Window,QColor('#111318' if dark else '#f5f6f8'))
    app.setPalette(palette)
    text = 'Name,Qty\nPaper,001\n' if table else 'a = 1\n' * 15
    preview.display(QImage(600,300,QImage.Format.Format_RGB32),text,.1,'test')
    preview.resize(840,620)
    preview.context_toggle.setChecked(True)
    QApplication.instance().processEvents()
    assert preview.hide_image_button.parentWidget() is preview.image_panel
    assert preview.hide_image_button.accessibleName() == 'Скрыть снимок'
    assert preview.image_panel.rect().contains(preview.hide_image_button.geometry())
    for control in (preview.zoom_out,preview.image_zoom,preview.zoom_in,preview.actual_size,preview.fit_image,preview.hide_image_button):
        assert control.width() >= control.sizeHint().width()
        assert control.parentWidget().rect().contains(control.geometry())
    assert preview.result_stack.mapTo(preview.centralWidget(),QPoint()).y() <= 150
    assert preview.result_stack.mapTo(preview.centralWidget(),QPoint()).y() == preview.image_view.mapTo(preview.centralWidget(),QPoint()).y()
    preview.hide_image_button.click()
    assert not preview.image_panel.isVisible()
    assert preview.show_image_button.isVisible()
    preview.show_image_button.click()
    assert preview.image_panel.isVisible()
