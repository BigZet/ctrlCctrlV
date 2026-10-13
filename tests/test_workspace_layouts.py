"""Responsive logical groups retain usable geometry as their parent narrows."""
from PySide6.QtCore import QSize
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ctrlcctrlv.workspace_layouts import AdaptiveRow


class Group(QWidget):
    def __init__(self, width, minimum=None):
        super().__init__()
        self.natural_width = width
        self.setMinimumWidth(width if minimum is None else minimum)
        self.setMaximumHeight(36)

    def sizeHint(self):
        return QSize(self.natural_width, 36)

    def minimumSizeHint(self):
        return QSize(self.minimumWidth(), 36)


def settle():
    for _ in range(6):
        QApplication.processEvents()


def host(*groups, width=500):
    window = QWidget()
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    row = AdaptiveRow(*groups)
    layout.addWidget(row)
    layout.addStretch()
    window.resize(width, 200)
    window.show()
    settle()
    return window, row


def assert_in_bounds(row, *groups):
    for group in groups:
        assert group.isVisible()
        assert row.rect().contains(group.geometry()), (row.size(), group.geometry())
        assert group.height() == 36


def test_wide_groups_use_natural_widths_and_trailing_space():
    first, second = Group(230), Group(180)
    window, row = host(first, second)
    try:
        assert row.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
        assert row.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
        assert first.y() == second.y() == 0
        assert first.width() == 230
        assert second.width() == 180
        assert second.x() == 238
        assert second.geometry().right() < row.rect().right()
        assert_in_bounds(row, first, second)
    finally:
        window.close()


def test_narrow_groups_stack_and_expand_back_without_clipping():
    first, second = Group(230), Group(180)
    window, row = host(first, second)
    try:
        window.resize(320, 200)
        settle()
        assert window.width() == 320
        assert first.x() == second.x() == 0
        assert second.y() == first.height() + 8
        assert_in_bounds(row, first, second)
        window.resize(500, 200)
        settle()
        assert first.y() == second.y() == 0
        assert second.x() == 238
        assert_in_bounds(row, first, second)
    finally:
        window.close()


def test_horizontal_initial_layout_does_not_inflate_parent_minimum():
    first, second = Group(230), Group(180)
    window, row = host(first, second)
    try:
        assert row.minimumSizeHint().width() == 230
        assert window.minimumSizeHint().width() == 230
        assert window.minimumWidth() <= 230
    finally:
        window.close()


def test_hidden_optional_group_reserves_neither_space_nor_gap():
    first, second, optional = Group(230), Group(180), Group(90)
    window, row = host(first, second, optional)
    try:
        assert second.y() > first.y()
        optional.hide()
        settle()
        assert first.y() == second.y() == 0
        assert second.x() == 238
        assert row.height() == 36
        optional.show()
        settle()
        assert second.y() == 44
        assert optional.y() == 88
        assert_in_bounds(row, first, second, optional)
    finally:
        window.close()


def test_child_font_change_recalculates_without_parent_resize():
    first, second = QLabel('Format selection'), QLabel('Preview')
    larger = QFont(first.font())
    larger.setPointSize(24)
    # Place the breakpoint between one enlarged label and both labels. Font
    # families differ after the full application's font setup and across OSes.
    width = QFontMetrics(larger).horizontalAdvance(first.text()) + second.sizeHint().width() // 2
    window, row = host(first, second, width=width)
    try:
        assert first.y() == second.y()
        first.setFont(larger)
        settle()
        assert window.width() == width
        assert first.sizeHint().width() + second.sizeHint().width() + 8 > row.width()
        assert second.y() >= first.geometry().bottom() + 8
        assert row.rect().contains(first.geometry())
        assert row.rect().contains(second.geometry())
        first.setFont(second.font())
        settle()
        assert first.y() == second.y()
    finally:
        window.close()


def test_group_layout_request_recalculates_when_nested_label_changes():
    first = QWidget()
    inner = QVBoxLayout(first)
    inner.setContentsMargins(0, 0, 0, 0)
    label = QLabel('Format')
    inner.addWidget(label)
    second = Group(180)
    window, row = host(first, second, width=320)
    try:
        assert abs(first.geometry().center().y() - second.geometry().center().y()) <= 1
        label.setText('Choose the output format here')
        settle()
        assert first.sizeHint().width() + 180 + 8 > row.width()
        assert second.y() > first.y()
        label.setText('Format')
        settle()
        assert abs(first.geometry().center().y() - second.geometry().center().y()) <= 1
    finally:
        window.close()


def test_single_group_can_shrink_to_its_minimum_without_clipping():
    group = Group(400, minimum=100)
    window, row = host(group, width=320)
    try:
        assert window.width() == 320
        assert group.width() <= 320
        assert_in_bounds(row, group)
    finally:
        window.close()


def test_unequal_heights_are_centered_in_a_horizontal_row():
    first, second = Group(230), QWidget()
    second.setFixedSize(180, 26)
    window, row = host(first, second)
    try:
        assert abs(first.geometry().center().y() - second.geometry().center().y()) <= 1
        assert first.height() == 36 and second.height() == 26
        window.resize(320, 200)
        settle()
        assert first.y() == 0 and second.y() == 44
        assert first.height() == 36 and second.height() == 26
    finally:
        window.close()


def test_optional_hidden_before_show_is_not_counted():
    first, second, optional = Group(230), Group(180), Group(90)
    optional.hide()
    window, row = host(first, second, optional)
    try:
        assert optional.isHidden()
        assert row.height() == 36
        assert second.x() == 238
        assert row.minimumSizeHint().width() == 230
    finally:
        window.close()


def test_reflow_quiesces_after_layout_events():
    class CountingRow(AdaptiveRow):
        def __init__(self, *groups):
            self.reflows = 0
            super().__init__(*groups)

        def _reflow(self):
            self.reflows += 1
            super()._reflow()

    window = QWidget()
    layout = QVBoxLayout(window)
    row = CountingRow(Group(230), Group(180))
    layout.addWidget(row)
    layout.addStretch()
    window.resize(320, 200)
    window.show()
    settle()
    try:
        before = row.reflows
        for _ in range(20):
            QApplication.processEvents()
        assert row.reflows == before
        assert not row._timer.isActive()
    finally:
        window.close()
