import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from ctrlcctrlv.ui import Preview, ImageView


def test_fit_emits_actual_zoom_and_manual_zoom_is_clamped():
    image = ImageView()
    image.resize(250,180)
    image.show()
    values = []
    image.zoom_changed.connect(lambda percent,fit: values.append((percent,fit)))
    image.display(QImage(1000,500,QImage.Format.Format_RGB32))
    QApplication.instance().processEvents()
    percent,fit = values[-1]
    assert fit
    assert 15 <= percent <= 25
    image.set_zoom(500)
    assert values[-1] == (400.0,False)
    image.close()
    image.deleteLater()


@pytest.mark.parametrize('table',[False,True])
@pytest.mark.parametrize('size',[(840,620),(960,500)])
def test_compact_preview_reaches_editor_and_actions_with_prompt(table,size):
    window = Preview()
    text = 'Name,Value\nPaper,001\n' if table else '\n'.join('a = 1' for _ in range(15))
    window.display(QImage(600,300,QImage.Format.Format_RGB32),text,.1,'test')
    window.resize(*size)
    window.context_toggle.setChecked(True)
    QApplication.instance().processEvents()
    assert window.size().width() == size[0] and window.size().height() == size[1]
    assert window.result_stack.mapTo(window.centralWidget(),QPoint()).y() <= 150
    assert window.text.height() >= window.text.fontMetrics().height() * 8
    assert window.copy_button.isVisible()
    assert window.editor_view.isVisible()
    zoom_text = window.image_zoom.lineEdit()
    assert zoom_text.width() >= zoom_text.fontMetrics().horizontalAdvance(window.image_zoom.text())
    assert window.review_context.height() <= window.review_context.fontMetrics().height() * 3 + 20
    assert window.image_view.mapTo(window.centralWidget(),QPoint()).y() == window.result_stack.mapTo(window.centralWidget(),QPoint()).y()
    window.hide_image_button.click()
    assert not window.image_panel.isVisible()
    assert window.show_image_button.isVisible()
    window.show_image_button.click()
    assert window.image_panel.isVisible()
    window.close()
    window.deleteLater()


def test_expanded_prompt_only_applies_explicitly_and_never_requests_review():
    window = Preview()
    window.review_context.setPlainText('old')
    requested = []
    window.review_requested.connect(lambda: requested.append(True))
    window.expand_prompt()
    window.prompt_editor.setPlainText('new')
    window.prompt_dialog.reject()
    assert window.review_context.toPlainText() == 'old'
    window.expand_prompt()
    window.prompt_editor.setPlainText('applied')
    window.apply_prompt()
    assert window.review_context.toPlainText() == 'applied'
    assert requested == []
    window.close()
    window.deleteLater()
