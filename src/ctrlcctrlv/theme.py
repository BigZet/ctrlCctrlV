"""Shared, system-aware colours for every application surface."""
from pathlib import Path
from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget


GAP = 8
SECTION_GAP = 16
PAGE_MARGIN = 24
DIALOG_MARGIN = 16
CONTROL_HEIGHT = 38
COMPACT_HEIGHT = 32


def fit_window_to_screen(window: QWidget, available: QRect | None = None) -> None:
    """Keep a window's frame inside logical screen space without shrinking its font."""
    if available is None:
        screen = window.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
    if available.isEmpty():
        return
    if not hasattr(window, '_fit_original_minimum'):
        window._fit_original_minimum = QSize(window.minimumSize())
    original = window._fit_original_minimum
    frame, client = window.frameGeometry(), window.geometry()
    frame_width = max(0, frame.width() - client.width())
    frame_height = max(0, frame.height() - client.height())
    usable = QSize(max(1, available.width() - frame_width),
                   max(1, available.height() - frame_height))
    window.setMinimumSize(min(original.width(), usable.width()),
                          min(original.height(), usable.height()))
    if not window.isMaximized() and not window.isFullScreen():
        size = window.size().boundedTo(usable).expandedTo(window.minimumSize())
        if size != window.size():
            window.resize(size)
        frame = window.frameGeometry()
        x = min(max(frame.x(), available.x()), available.right() - frame.width() + 1)
        y = min(max(frame.y(), available.y()), available.bottom() - frame.height() + 1)
        if frame.topLeft().x() != x or frame.topLeft().y() != y:
            window.move(x, y)
    handle = window.windowHandle()
    if handle is not None and not getattr(window, '_fit_screen_connected', False):
        handle.screenChanged.connect(lambda _screen: fit_window_to_screen(window))
        window._fit_screen_connected = True


def colours():
    dark = QApplication.palette().window().color().lightness() < 128
    names = ('bg', 'panel', 'text', 'muted', 'edge', 'hover', 'selected', 'accent', 'soft')
    values = ('#111318', '#1b1e25', '#f0f2f6', '#a7adba', '#323743', '#292e39', '#283951', '#a7c5ff', '#171a21') if dark else ('#f5f6f8', '#ffffff', '#202936', '#626d7d', '#dfe4ec', '#f0f3f8', '#eaf0fb', '#285ec5', '#eef1f5')
    result = dict(zip(names, values))
    result.update(warning='#f5c16c' if dark else '#965b06', danger='#ffb1b1' if dark else '#b32b3f')
    return result


def editor_palette():
    c = colours()
    palette = QPalette(QApplication.palette())
    for role, value in ((QPalette.ColorRole.Window, c['bg']), (QPalette.ColorRole.Button, c['panel']),
                        (QPalette.ColorRole.Light, c['edge']), (QPalette.ColorRole.Mid, c['edge']),
                        (QPalette.ColorRole.Dark, c['edge']), (QPalette.ColorRole.AlternateBase, c['soft']),
                        (QPalette.ColorRole.Base, c['panel']), (QPalette.ColorRole.Text, c['text']),
                        (QPalette.ColorRole.WindowText, c['text']), (QPalette.ColorRole.ButtonText, c['text']),
                        (QPalette.ColorRole.PlaceholderText, c['muted']), (QPalette.ColorRole.Highlight, '#326ce5'),
                        (QPalette.ColorRole.HighlightedText, '#ffffff')):
        palette.setColor(role, QColor(value))
    return palette


def stylesheet():
    assets = (Path(__file__).parent / 'assets').as_posix()
    c = colours()
    danger = '#ffb1b1' if QApplication.palette().window().color().lightness() < 128 else '#b32b3f'
    return f'''
        QDialog, QMainWindow, QWizard, QWizardPage {{ background: {c['bg']}; color: {c['text']}; }}
        QWidget {{ color: {c['text']}; font-size: 13px; }}
        QLabel {{ background: transparent; }}
        QLabel[role="title"] {{ font-size: 22px; font-weight: 600; }}
        QLabel[role="brand"] {{ font-size: 18px; font-weight: 600; }}
        QLabel[role="eyebrow"] {{ color: {c['accent']}; font-size: 11px; font-weight: 600; }}
        QLabel[role="subtitle"], QLabel[role="muted"] {{ color: {c['muted']}; }}
        QLabel[role="section"] {{ font-size: 14px; font-weight: 600; }}
        QLabel[role="warning"] {{ color: {c['warning']}; }}
        QLabel[role="error"] {{ color: {c['danger']}; }}
        QLabel[role="downloadVolume"] {{ color: {c['muted']}; font-size: 11px; }}
        QLabel[role="navtitle"] {{ font-weight: 600; }}
        QLabel[role="navtitle"][active="true"] {{ color: {c['accent']}; }}
        QListWidget QWidget {{ background: transparent; }}
        QLabel[role="badge"] {{ background: {c['selected']}; color: {c['accent']}; padding: 6px 10px; border-radius: 6px; font-size: 12px; }}
        QLabel[role="notice"] {{ background: {c['soft']}; color: {c['muted']}; padding: 12px; border-radius: 8px; }}
        QFrame[role="card"], QFrame[role="workspace"] {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 10px; }}
        QFrame[role="sidebar"] {{ background: {c['soft']}; border-right: 1px solid {c['edge']}; }}
        QFrame[role="footer"] {{ background: {c['panel']}; border-top: 1px solid {c['edge']}; }}
        QListWidget {{ background: transparent; border: none; outline: none; }}
        QListWidget::item {{ padding: 0; margin: 3px 0; border-radius: 9px; color: {c['muted']}; }}
        QListWidget::item:selected {{ background: {c['selected']}; color: {c['accent']}; font-weight: 600; }}
        QListWidget::item:hover:!selected {{ background: {c['hover']}; }}
        QListWidget::item:focus {{ border: 1px solid {c['accent']}; }}
        QListWidget#workspaceNav::item {{ padding-left: 12px; }}
        QTableWidget {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['edge']}; border-radius: 10px; outline: none; selection-background-color: {c['selected']}; selection-color: {c['text']}; }}
        QTableWidget::item {{ padding: 6px; border-bottom: 1px solid {c['edge']}; }}
        QTableWidget::item:selected {{ background: {c['selected']}; }}
        QTableWidget QWidget {{ background: transparent; }}
        QTableWidget QLineEdit {{ background: {c['panel']}; }}
        QHeaderView::section {{ background: {c['soft']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['edge']}; padding: 7px 8px; font-size: 12px; }}
        QPushButton {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 7px; min-height: 20px; padding: 7px 12px; font-weight: 500; }}
        QPushButton[role="compact"], QTableWidget QPushButton {{ min-height: 20px; padding: 5px 10px; }}
        QPushButton:hover {{ background: {c['hover']}; border-color: {c['muted']}; }}
        QPushButton:focus {{ border: 1px solid {c['accent']}; }}
        QPushButton:pressed {{ background: {c['selected']}; }}
        QPushButton:disabled {{ color: {c['muted']}; background: {c['soft']}; }}
        QPushButton[role="primary"] {{ color: white; background: #326ce5; border-color: #326ce5; }}
        QPushButton[role="primary"]:hover {{ background: #245dcc; }}
        QPushButton[role="primary"]:disabled {{ background: {c['edge']}; border-color: {c['edge']}; color: {c['muted']}; }}
        QPushButton[role="danger"] {{ color: {danger}; }}
        QPushButton[role="danger"]:disabled {{ color: {c['muted']}; background: {c['soft']}; border-color: {c['edge']}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 7px; padding: 7px; selection-background-color: #326ce5; selection-color: white; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ min-height: 20px; }}
        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {c['accent']}; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QToolButton:disabled {{ color: {c['muted']}; background: {c['soft']}; }}
        QComboBox {{ padding-right: 26px; }}
        QComboBox::drop-down {{ border: none; width: 26px; }}
        QComboBox::down-arrow {{ image: url("{assets}/chevron.png"); width: 12px; height: 12px; }}
        QComboBox QAbstractItemView {{ background: {c['panel']}; selection-background-color: {c['selected']}; color: {c['text']}; border: 1px solid {c['edge']}; padding: 4px; }}
        QSpinBox, QDoubleSpinBox {{ padding-right: 28px; }}
        QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 24px; border: none; border-top-right-radius: 6px; }}
        QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; border: none; border-bottom-right-radius: 6px; }}
        QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {c['hover']}; }}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{assets}/chevron-up.png"); width: 12px; height: 12px; }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{assets}/chevron.png"); width: 12px; height: 12px; }}
        QCheckBox {{ spacing: 9px; padding: 4px 0; }}
        QCheckBox::indicator, QTableWidget::indicator {{ width: 17px; height: 17px; border: 1px solid {c['muted']}; border-radius: 4px; background: {c['panel']}; }}
        QCheckBox::indicator:checked, QTableWidget::indicator:checked {{ background: #326ce5; border-color: #326ce5; image: url("{assets}/check.png"); }}
        QCheckBox:focus {{ color: {c['accent']}; }}
        QToolButton {{ color: {c['muted']}; border: 1px solid transparent; background: transparent; padding: 6px 0; }}
        QToolButton:hover, QToolButton:focus {{ color: {c['accent']}; }}
        QToolButton[role="viewToggle"] {{ border-radius: 6px; padding: 6px 9px; }}
        QToolButton[role="viewToggle"]:checked {{ color: {c['accent']}; background: {c['selected']}; }}
        QProgressBar {{ background: {c['edge']}; border: none; border-radius: 3px; height: 5px; max-height: 5px; }}
        QProgressBar::chunk {{ background: #326ce5; border-radius: 3px; }}
        QScrollArea, QStackedWidget {{ background: transparent; border: none; }}
        QTabWidget::pane {{ border: none; background: transparent; }}
        QTabBar::tab {{ background: transparent; color: {c['muted']}; border: none; border-bottom: 2px solid transparent; padding: 10px 18px; margin-right: 12px; }}
        QTabBar::tab:selected {{ color: {c['accent']}; border-bottom-color: {c['accent']}; }}
        QTabBar::tab:hover {{ color: {c['text']}; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
        QScrollBar::handle {{ background: {c['edge']}; border-radius: 3px; min-width: 24px; min-height: 24px; }}
        QScrollBar::handle:hover {{ background: {c['muted']}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QSplitter::handle {{ background: {c['edge']}; width: 3px; height: 3px; }}
        QSplitter::handle:hover {{ background: {c['muted']}; }}
        QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['edge']}; padding: 7px; }}
        QMenu {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 8px; padding: 5px; }}
        QDialog#workspaceWindow QLabel[role="title"] {{ font-size: 24px; font-weight: 600; }}
        QDialog#workspaceWindow QLabel[role="brand"] {{ font-size: 17px; font-weight: 600; }}
        QDialog#workspaceWindow QLabel[role="workTitle"] {{ font-size: 16px; font-weight: 600; }}
        QDialog#workspaceWindow QLabel[role="shortcut"] {{ color: {c['muted']}; background: transparent; font-size: 12px; padding: 0; }}
        QDialog#workspaceWindow QPushButton[workAction="true"] {{ min-width: 150px; }}
        QDialog#workspaceWindow QFrame[role="workCard"] {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 12px; }}
        QDialog#workspaceWindow QFrame#workspaceRail {{ background: {c['soft']}; border-right: 1px solid {c['edge']}; }}
        QDialog#workspaceWindow QListWidget#workspaceNav::item {{ padding-left: 10px; border: 1px solid transparent; border-radius: 8px; }}
        QDialog#workspaceWindow QListWidget#workspaceNav::item:selected {{ background: {c['selected']}; color: {c['accent']}; }}
        QDialog#workspaceWindow QListWidget#workspaceNav::item:focus {{ border-color: {c['accent']}; }}
        QDialog#workspaceWindow QPushButton[role="quiet"] {{ color: {c['muted']}; background: transparent; border: 1px solid transparent; border-radius: 6px; min-height: 20px; padding: 5px 9px; }}
        QDialog#workspaceWindow QPushButton[role="quiet"]:hover {{ color: {c['text']}; background: {c['hover']}; }}
        QDialog#workspaceWindow QPushButton[role="quiet"]:focus {{ color: {c['text']}; border-color: {c['accent']}; }}
        QDialog#workspaceWindow QPushButton[role="quiet"]:pressed {{ background: {c['selected']}; }}
        QDialog#workspaceWindow QPushButton[role="quiet"]:disabled {{ color: {c['muted']}; background: transparent; }}
        QDialog#workspaceWindow QTabWidget#workspaceTabs QTabBar::tab {{ color: {c['muted']}; background: {c['soft']}; border: 1px solid transparent; border-radius: 7px; padding: 7px 12px; margin: 0 3px 8px 0; }}
        QDialog#workspaceWindow QTabWidget#workspaceTabs QTabBar::tab:selected {{ color: {c['accent']}; background: {c['selected']}; border-color: {c['edge']}; }}
        QDialog#workspaceWindow QTabWidget#workspaceTabs QTabBar::tab:hover {{ color: {c['text']}; }}
        QDialog#workspaceWindow QTableWidget#modelLibrary {{ border-radius: 12px; }}
        QDialog#workspaceWindow QPushButton#downloadSelected:disabled {{ color: {c['muted']}; background: transparent; border-color: {c['edge']}; }}
        QDialog#workspaceWindow QTableWidget#modelLibrary QPushButton {{ font-size: 12px; padding: 5px 8px; }}
        QDialog#workspaceWindow QFrame#runtimeCard {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 12px; }}
        QDialog#workspaceWindow QSplitter#historySplit {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 10px; }}
        QDialog#workspaceWindow QSplitter#historySplit::handle {{ background: {c['panel']}; width: 6px; border-left: 1px solid {c['edge']}; }}
        QDialog#workspaceWindow QSplitter#historySplit::handle:hover {{ background: {c['hover']}; }}
        QDialog#workspaceWindow QListWidget#historyEntries {{ background: {c['soft']}; border: none; padding: 4px; }}
        QDialog#workspaceWindow QListWidget#historyEntries::item {{ color: {c['text']}; border: 1px solid transparent; margin: 2px 0; border-radius: 7px; }}
        QDialog#workspaceWindow QListWidget#historyEntries::item:selected {{ color: {c['text']}; background: {c['selected']}; }}
        QDialog#workspaceWindow QListWidget#historyEntries::item:focus {{ border-color: {c['accent']}; }}
        QDialog#workspaceWindow QPlainTextEdit#historyText {{ background: transparent; border: 1px solid transparent; border-radius: 0; padding: 10px; }}
        QDialog#workspaceWindow QPlainTextEdit#historyText:focus {{ border-color: {c['accent']}; }}
    '''
