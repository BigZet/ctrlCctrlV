"""Shared, system-aware colours for every application surface."""
from pathlib import Path
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def colours():
    dark = QApplication.palette().window().color().lightness() < 128
    names = ('bg', 'panel', 'text', 'muted', 'edge', 'hover', 'selected', 'accent', 'soft')
    values = ('#10151e', '#19212e', '#edf2fb', '#a7b4c8', '#303e52', '#233147', '#263d61', '#8ab4ff', '#131b27') if dark else ('#f3f5f9', '#ffffff', '#172438', '#576980', '#d8e1ed', '#eef3fb', '#e7efff', '#245dc4', '#edf1f7')
    return dict(zip(names, values))


def editor_palette():
    c = colours()
    palette = QPalette(QApplication.palette())
    for role, value in ((QPalette.ColorRole.Base, c['panel']), (QPalette.ColorRole.Text, c['text']), (QPalette.ColorRole.WindowText, c['text']), (QPalette.ColorRole.ButtonText, c['text']), (QPalette.ColorRole.PlaceholderText, c['muted']), (QPalette.ColorRole.Highlight, '#326ce5'), (QPalette.ColorRole.HighlightedText, '#ffffff')):
        palette.setColor(role, QColor(value))
    return palette


def stylesheet():
    assets = (Path(__file__).parent / 'assets').as_posix()
    c = colours()
    danger = '#ffb1b1' if c['bg'] == '#10151e' else '#b32b3f'
    return f'''
        QDialog, QMainWindow, QWizard, QWizardPage {{ background: {c['bg']}; color: {c['text']}; }}
        QWidget {{ color: {c['text']}; font-size: 13px; }}
        QLabel {{ background: transparent; }}
        QLabel[role="title"] {{ font-size: 28px; font-weight: 600; }}
        QLabel[role="brand"] {{ font-size: 18px; font-weight: 600; }}
        QLabel[role="eyebrow"] {{ color: {c['accent']}; font-size: 11px; font-weight: 600; }}
        QLabel[role="subtitle"], QLabel[role="muted"] {{ color: {c['muted']}; }}
        QLabel[role="section"] {{ font-size: 14px; font-weight: 600; }}
        QLabel[role="navtitle"] {{ font-weight: 600; }}
        QLabel[role="navtitle"][active="true"] {{ color: {c['accent']}; }}
        QListWidget QWidget {{ background: transparent; }}
        QLabel[role="badge"] {{ background: {c['selected']}; color: {c['accent']}; padding: 6px 10px; border-radius: 6px; font-size: 12px; }}
        QLabel[role="notice"] {{ background: {c['soft']}; color: {c['muted']}; padding: 12px; border-radius: 8px; }}
        QFrame[role="card"], QFrame[role="workspace"] {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 12px; }}
        QFrame[role="sidebar"] {{ background: {c['soft']}; border-right: 1px solid {c['edge']}; }}
        QFrame[role="footer"] {{ background: {c['panel']}; border-top: 1px solid {c['edge']}; }}
        QListWidget {{ background: transparent; border: none; outline: none; }}
        QListWidget::item {{ padding: 0; margin: 3px 0; border-radius: 9px; color: {c['muted']}; }}
        QListWidget::item:selected {{ background: {c['selected']}; color: {c['accent']}; font-weight: 600; }}
        QListWidget::item:hover:!selected {{ background: {c['hover']}; }}
        QListWidget::item:focus {{ border: 1px solid {c['accent']}; }}
        QPushButton {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 8px; padding: 9px 14px; font-weight: 500; }}
        QPushButton:hover {{ background: {c['hover']}; border-color: {c['muted']}; }}
        QPushButton:focus {{ border: 1px solid {c['accent']}; }}
        QPushButton:pressed {{ background: {c['selected']}; }}
        QPushButton:disabled {{ color: {c['muted']}; background: {c['soft']}; }}
        QPushButton[role="primary"] {{ color: white; background: #326ce5; border-color: #326ce5; }}
        QPushButton[role="primary"]:hover {{ background: #245dcc; }}
        QPushButton[role="primary"]:disabled {{ background: {c['edge']}; border-color: {c['edge']}; color: {c['muted']}; }}
        QPushButton[role="danger"] {{ color: {danger}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ background: {c['panel']}; border: 1px solid {c['edge']}; border-radius: 7px; padding: 8px; selection-background-color: #326ce5; selection-color: white; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ min-height: 20px; }}
        QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {c['accent']}; }}
        QLineEdit:disabled, QComboBox:disabled {{ color: {c['muted']}; background: {c['soft']}; }}
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
        QCheckBox::indicator {{ width: 17px; height: 17px; border: 1px solid {c['muted']}; border-radius: 4px; background: {c['panel']}; }}
        QCheckBox::indicator:checked {{ background: #326ce5; border-color: #326ce5; image: url("{assets}/check.png"); }}
        QCheckBox:focus {{ color: {c['accent']}; }}
        QToolButton {{ color: {c['muted']}; border: 1px solid transparent; background: transparent; padding: 6px 0; }}
        QToolButton:hover, QToolButton:focus {{ color: {c['accent']}; }}
        QToolButton[role="viewToggle"] {{ border-radius: 6px; padding: 6px 9px; }}
        QToolButton[role="viewToggle"]:checked {{ color: {c['accent']}; background: {c['selected']}; }}
        QProgressBar {{ background: {c['edge']}; border: none; border-radius: 3px; height: 5px; max-height: 5px; }}
        QProgressBar::chunk {{ background: #326ce5; border-radius: 3px; }}
        QScrollArea, QStackedWidget {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
        QScrollBar::handle {{ background: {c['edge']}; border-radius: 3px; min-width: 24px; min-height: 24px; }}
        QScrollBar::handle:hover {{ background: {c['muted']}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QSplitter::handle {{ background: transparent; width: 12px; }}
        QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['edge']}; padding: 7px; }}
    '''
