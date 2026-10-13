"""Read-only character differences and coordinated comparison scrolling."""
from difflib import SequenceMatcher

from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtWidgets import QApplication, QTextEdit


def change_line_ranges(result):
    """Half-open line ranges in changes() order, accounting for earlier insertions."""
    ranges, delta = [], 0
    for change in result.changes():
        start = change.start + delta
        ranges.append((change.start, change.end, start, start + len(change.replacement)))
        delta += len(change.replacement) - (change.end - change.start)
    return ranges


def synchronize_scroll(before, after):
    def connect(first, second):
        following = False

        def follow(source, target, value):
            nonlocal following
            if following:
                return
            following = True
            try:
                # Qt needs valueChanged to update the visible document. Suppress
                # only our reciprocal callback, not the scrollbar's signals.
                target.setValue(round(value * target.maximum() / max(1, source.maximum())))
            finally:
                following = False

        first.valueChanged.connect(lambda value: follow(first, second, value))
        second.valueChanged.connect(lambda value: follow(second, first, value))

    for name in ('verticalScrollBar', 'horizontalScrollBar'):
        first, second = getattr(before, name)(), getattr(after, name)()
        connect(first, second)


def show_comparison(before, after, result):
    """Match only changed line groups; bound expensive character matching."""
    scroll = before.verticalScrollBar().value()
    before.setPlainText(result.original)
    after.setPlainText(result.proposed)
    old_lines = result.original.splitlines(keepends=True)
    offsets = [0]
    for line in old_lines:
        offsets.append(offsets[-1] + len(line))
    spans = [[], []]
    delta = 0
    for change in result.changes():
        start, end = offsets[change.start], offsets[change.end]
        original = result.original[start:end]
        proposed = ''.join(change.replacement)
        if len(original) + len(proposed) <= 20000:
            operations = SequenceMatcher(None, original, proposed).get_opcodes()
        else:
            operations = [('replace', 0, len(original), 0, len(proposed))]
        for tag, a, b, c, d in operations:
            if tag != 'equal':
                if a < b:
                    spans[0].append((start + a, start + b))
                if c < d:
                    spans[1].append((start + delta + c, start + delta + d))
        delta += len(proposed) - len(original)
    dark = QApplication.palette().window().color().lightness() < 128
    line_ranges = change_line_ranges(result)
    for side, (editor, text, ranges, color) in enumerate(((before, result.original, spans[0], '#704044' if dark else '#ffd9de'),
                                        (after, result.proposed, spans[1], '#285d45' if dark else '#c5f0d5'))):
        selections = []
        editor.changed_lines = {}
        for changed in line_ranges:
            start,end = changed[side*2:side*2+2]
            for line in range(start,max(start+1,end)):
                line = min(line,editor.blockCount()-1)
                editor.changed_lines[line] = '−' if side == 0 else '+'
        for line in editor.changed_lines:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(editor.document().findBlockByNumber(line))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection,True)
            selection.format.setBackground(QColor('#2b2428' if dark and side == 0 else '#1b2922' if dark else
                                                  '#fff1f3' if side == 0 else '#effbf3'))
            selections.append(selection)
        # QTextCursor positions count UTF-16 code units, including supplementary characters.
        positions = [0]
        for index, character in enumerate(text):
            units = 0 if character == '\r' and text[index:index + 2] == '\r\n' else 2 if ord(character) > 0xffff else 1
            positions.append(positions[-1] + units)
        for start, end in ranges:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = editor.textCursor()
            selection.cursor.setPosition(positions[start])
            selection.cursor.setPosition(positions[end], QTextCursor.MoveMode.KeepAnchor)
            selection.format.setBackground(QColor(color))
            selections.append(selection)
        editor.setExtraSelections(selections)
        editor.gutter.update()
    before.verticalScrollBar().setValue(scroll)
