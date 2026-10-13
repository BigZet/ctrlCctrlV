import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from ctrlcctrlv.review import ReviewResult
from ctrlcctrlv.ui import Preview


@pytest.mark.parametrize('old,new,expected', [
    ('a\nb\nc\n', 'x\na\nb\nz\nc\n', [(0,0,0,1),(2,2,3,4)]),
    ('a\nb\nc\n', 'a\nc\n', [(1,2,1,1)]),
    ('😀\r\n\r\nb\r\n', '😀\r\nx\r\nb\r\n', [(1,2,1,2)]),
    ('a\n', 'a', [(0,1,0,1)]),
])
def test_line_ranges_match_insert_delete_replace_and_newline(old,new,expected):
    from ctrlcctrlv.comparison import change_line_ranges
    assert change_line_ranges(ReviewResult(old,new,'test',.1)) == expected


def test_navigation_scrolls_both_sides_without_toggling_choices():
    window = Preview()
    original = '\n'.join(f'line {n}' for n in range(160))
    proposed = original.replace('line 80', 'changed 80').replace('line 120', 'changed 120')
    window.display_history(original)
    assert window.propose(ReviewResult(original,proposed,'test',.1))
    QApplication.instance().processEvents()
    window.focus_review_change(1)
    assert window.before_text.textCursor().blockNumber() == 120
    assert window.after_text.textCursor().blockNumber() == 120
    assert all(choice.isChecked() for choice in window.review_choices)
    window.review_choices[0].setChecked(False)
    window.focus_review_change(1)
    assert not window.review_choices[0].isChecked()
    assert window.after_text.textCursor().blockNumber() == 120
    window.review_panel.resize(680,420)
    QApplication.instance().processEvents()
    assert window.comparison_split.orientation() == Qt.Orientation.Vertical
    assert window.review_choice_toggle.isVisible()
    assert not window.review_choice_scroll.isVisible()
    window.review_choice_toggle.setChecked(True)
    assert window.review_choice_scroll.isVisible()
    window.set_comparison_orientation(Qt.Orientation.Horizontal)
    window.review_panel.resize(700,440)
    QApplication.instance().processEvents()
    assert window.comparison_split.orientation() == Qt.Orientation.Horizontal
    window.review_panel.close()
    window.close()
    window.deleteLater()


def test_comparison_marks_changed_lines_including_empty_insertion_side():
    from ctrlcctrlv.comparison import show_comparison
    window = Preview()
    show_comparison(window.before_text,window.after_text,ReviewResult('', '😀\n','test',.1))
    assert window.before_text.changed_lines == {0:'−'}
    assert window.after_text.changed_lines == {0:'+'}
    assert any(selection.format.property(selection.format.Property.FullWidthSelection)
               for selection in window.before_text.extraSelections())
    window.close()
    window.deleteLater()


def test_navigation_maps_insertions_after_unchecked_preceding_change():
    window = Preview()
    window.display_history('a\nb\nc\n')
    window.propose(ReviewResult('a\nb\nc\n','x\na\nb\nz\nc\n','test',.1))
    window.focus_review_change(1)
    assert window.before_text.textCursor().blockNumber() == 2
    assert window.after_text.textCursor().blockNumber() == 3
    window.review_choices[0].setChecked(False)
    window.focus_review_change(1)
    assert window.before_text.textCursor().blockNumber() == 2
    assert window.after_text.textCursor().blockNumber() == 2
    assert window.review_result.proposed == 'a\nb\nz\nc\n'
    window.review_panel.close()
    window.close()
    window.deleteLater()


def test_comparison_reflows_after_manual_sidebar_resize_and_respects_override():
    window = Preview()
    window.display_history('a\nb\nc\n')
    window.propose(ReviewResult('a\nb\nc\n','A\nb\nC\n','test',.1))
    window.review_panel.resize(1180,680)
    QApplication.instance().processEvents()
    assert window.comparison_split.orientation() == Qt.Orientation.Horizontal
    window.review_changes_split.moveSplitter(700,1)
    QApplication.instance().processEvents()
    assert window.comparison_split.width() < 600
    assert window.comparison_split.orientation() == Qt.Orientation.Vertical
    window.review_changes_split.moveSplitter(220,1)
    QApplication.instance().processEvents()
    assert window.comparison_split.width() > 600
    assert window.comparison_split.orientation() == Qt.Orientation.Horizontal
    window.set_comparison_orientation(Qt.Orientation.Horizontal)
    window.review_changes_split.moveSplitter(700,1)
    QApplication.instance().processEvents()
    assert window.comparison_split.orientation() == Qt.Orientation.Horizontal
    window.set_comparison_orientation(Qt.Orientation.Vertical)
    window.review_changes_split.moveSplitter(220,1)
    QApplication.instance().processEvents()
    assert window.comparison_split.orientation() == Qt.Orientation.Vertical
    window.review_panel.close()
    window.close()
    window.deleteLater()
