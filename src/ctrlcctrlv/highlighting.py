"""Pygments token offsets translated to Qt's UTF-16 coordinates."""
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QApplication
from bisect import bisect_right
from pygments import lex
from pygments.lexers import JavaLexer, JsonLexer, PythonLexer, SqlLexer
from pygments.token import Comment, Keyword, Name, Number, Operator, String
from .syntax import guess_language


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.language = "auto"
        self.tokens = []
        self.ends = []

    def update_text(self, text, language):
        self.language = language
        selected = guess_language(text) if language == "auto" else language
        lexer = {"python": PythonLexer, "sql": SqlLexer, "java": JavaLexer, "json": JsonLexer}.get(selected)
        self.tokens = []
        if lexer and len(text) <= 100_000:
            offset = 0
            # Lexer preserves initial whitespace and final newline, including multiline strings.
            for token, value in lex(text, lexer(stripnl=False, ensurenl=False)):
                length = len(value.encode("utf-16-le")) // 2
                self.tokens.append((offset, offset + length, token))
                offset += length
        self.ends = [token[1] for token in self.tokens]
        self.rehighlight()

    def highlightBlock(self, text):
        start = self.currentBlock().position()
        end = start + len(text.encode("utf-16-le")) // 2
        dark = QApplication.palette().window().color().lightness() < 128
        for left, right, token in self.tokens[bisect_right(self.ends, start):]:
            if left >= end:
                break
            if right <= start:
                continue
            colour = None
            for category, light, night in ((Comment, "#67746c", "#95ab9d"), (Keyword, "#7937a8", "#c9a2ee"),
                                           (String, "#17633c", "#9fd5af"), (Number, "#945712", "#edc68d"),
                                           (Name.Function, "#185d9e", "#97c8f5"), (Name.Class, "#185d9e", "#97c8f5")):
                if token in category:
                    colour = night if dark else light
                    break
            if colour:
                style = QTextCharFormat()
                style.setForeground(QColor(colour))
                self.setFormat(max(left, start) - start, min(right, end) - max(left, start), style)
