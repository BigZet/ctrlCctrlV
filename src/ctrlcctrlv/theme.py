"""Small shared theme; follows the system light/dark palette."""
from PySide6.QtWidgets import QApplication
from pathlib import Path


def stylesheet():
    assets = (Path(__file__).parent / "assets").as_posix()
    dark = QApplication.palette().window().color().lightness() < 128
    bg, panel, text, muted, edge, hover, selected = (
        ('#151a23', '#1d2430', '#eef2f8', '#a1aec2', '#344052', '#273246', '#243b5d') if dark else
        ('#f5f7fa', '#ffffff', '#182433', '#64748b', '#dce3ec', '#edf2f8', '#e7efff'))
    return f'''
        QDialog, QMainWindow {{ background: {bg}; color: {text}; }}
        QWidget {{ color: {text}; font-size: 13px; }}
        QLabel {{ background: transparent; }}
        QLabel[role="title"] {{ font-size: 25px; font-weight: 600; }}
        QLabel[role="brand"] {{ font-size: 17px; font-weight: 600; }}
        QLabel[role="subtitle"], QLabel[role="muted"] {{ color: {muted}; }}
        QLabel[role="section"] {{ font-size: 14px; font-weight: 600; }}
        QFrame[role="card"] {{ background: {panel}; border: 1px solid {edge}; border-radius: 14px; }}
        QListWidget {{ background: transparent; border: none; outline: none; }}
        QListWidget::item {{ padding: 12px 10px; margin: 3px 0; border-radius: 9px; color: {muted}; }}
        QListWidget::item:selected {{ background: {selected}; color: #508bf5; font-weight: 600; }}
        QListWidget::item:hover:!selected {{ background: {hover}; }}
        QPushButton {{ background: {panel}; border: 1px solid {edge}; border-radius: 8px; padding: 8px 14px; font-weight: 500; }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:pressed {{ background: {selected}; }}
        QPushButton:disabled {{ color: {muted}; background: {bg}; }}
        QPushButton[role="primary"] {{ color: white; background: #3478ed; border-color: #3478ed; }}
        QPushButton[role="primary"]:hover {{ background: #2566d7; }}
        QPushButton[role="primary"]:disabled {{ background: {edge}; border-color: {edge}; color: {muted}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ background: {panel}; border: 1px solid {edge}; border-radius: 7px; padding: 7px; selection-background-color: #3478ed; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ min-height: 18px; }}
        QLineEdit:focus, QPlainTextEdit:focus {{ border-color: #3478ed; }}
        QComboBox {{ padding-right: 22px; }}
        QComboBox::drop-down {{ border: none; width: 22px; }}
        QComboBox::down-arrow {{ image: url("{assets}/chevron.png"); width: 12px; height: 12px; }}
        QComboBox QAbstractItemView {{ background: {panel}; selection-background-color: {selected}; color: {text}; }}
        QCheckBox {{ spacing: 9px; padding: 4px 0; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {muted}; border-radius: 4px; background: {panel}; }}
        QCheckBox::indicator:checked {{ background: #3478ed; border-color: #3478ed; image: url("{assets}/check.png"); }}
        QToolButton {{ color: {muted}; border: none; background: transparent; padding: 5px 0; }}
        QToolButton:hover {{ color: #3478ed; }}
        QToolButton[role="viewToggle"] {{ border-radius: 6px; padding: 5px 8px; }}
        QToolButton[role="viewToggle"]:checked {{ color: #3478ed; background: {selected}; }}
        QProgressBar {{ background: {edge}; border: none; border-radius: 3px; height: 5px; max-height: 5px; }}
        QProgressBar::chunk {{ background: #3478ed; border-radius: 3px; }}
        QScrollArea, QStackedWidget {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
        QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
        QScrollBar::handle {{ background: {edge}; border-radius: 3px; min-width: 24px; min-height: 24px; }}
        QScrollBar::handle:hover {{ background: {muted}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QSplitter::handle {{ background: {edge}; width: 1px; }}
    '''
