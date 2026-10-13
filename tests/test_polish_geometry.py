"""Window fitting respects available logical pixels, including small high-DPI screens."""
from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QPushButton

from ctrlcctrlv import theme


def test_fit_window_lowers_oversized_minimum_and_keeps_action_visible():
    app = QApplication.instance() or QApplication([])
    window = QDialog()
    layout = QVBoxLayout(window)
    layout.addStretch()
    action = QPushButton('Copy result')
    layout.addWidget(action)
    window.setMinimumSize(820, 620)
    window.resize(1100, 760)
    window.show()
    app.processEvents()
    try:
        available = QRect(0, 0, 960, 500)
        assert hasattr(theme, 'fit_window_to_screen')
        theme.fit_window_to_screen(window, available)
        app.processEvents()
        assert available.contains(window.frameGeometry())
        assert window.rect().contains(QRect(action.mapTo(window, action.rect().topLeft()), action.size()))
        assert action.height() >= action.minimumSizeHint().height()
    finally:
        window.close()


def test_fit_window_keeps_user_size_when_already_inside_available_screen():
    app = QApplication.instance() or QApplication([])
    window = QDialog()
    window.resize(875, 655)
    window.move(-1100, 80)
    window.show()
    app.processEvents()
    try:
        original = window.size()
        assert hasattr(theme, 'fit_window_to_screen')
        theme.fit_window_to_screen(window, QRect(-1280, 0, 1280, 900))
        assert window.size() == original
        assert QRect(-1280, 0, 1280, 900).contains(window.frameGeometry())
    finally:
        window.close()


def test_fit_window_restores_minimum_when_moving_to_larger_screen():
    app = QApplication.instance() or QApplication([])
    window = QDialog()
    window.setMinimumSize(820, 620)
    window.resize(900, 700)
    window.show()
    app.processEvents()
    try:
        assert hasattr(theme, 'fit_window_to_screen')
        theme.fit_window_to_screen(window, QRect(0, 0, 960, 500))
        theme.fit_window_to_screen(window, QRect(0, 0, 1600, 1000))
        assert window.minimumSize() == QSize(820, 620)
    finally:
        window.close()
