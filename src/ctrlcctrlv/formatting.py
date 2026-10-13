"""Explicit formatting action; source text is never executed."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from .syntax import guess_language, json_too_deep


def format_text(text, language="auto"):
    if len(text) > 100_000:
        raise ValueError("Фрагмент слишком большой для форматирования (предел 100 000 символов)")
    language = guess_language(text) if language == "auto" else language
    if language == "python":
        import black
        source = textwrap.dedent(text)
        original = ast.parse(source)
        result = black.format_str(source, mode=black.Mode(line_length=88))
        if ast.dump(original) != ast.dump(ast.parse(result)):
            raise ValueError("Форматирование изменило структуру Python")
        # Preserve indentation when a screenshot contains an inner block.
        original_line = next((line for line in text.splitlines() if line.strip()), "")
        dedented_line = next((line for line in source.splitlines() if line.strip()), "")
        prefix = original_line[:len(original_line) - len(dedented_line)]
        result = textwrap.indent(result, prefix)
    elif language == "json":
        if json_too_deep(text):
            raise ValueError("JSON содержит слишком много уровней вложенности")
        # Preserve numeric literals, ordering and duplicate keys through a lexer,
        # rather than json.loads/dumps (which can round or discard data).
        json.loads(text)
        import clang_format
        result = run_clang(text, "json", clang_format)
    elif language == "sql":
        import sqlparse
        result = sqlparse.format(text, reindent=True, indent_width=4, strip_comments=False)
        def significant(value):
            return [(token.ttype, token.value) for statement in sqlparse.parse(value) for token in statement.flatten() if not token.is_whitespace]
        if significant(text) != significant(result):
            raise ValueError("Форматирование изменило токены SQL")
    elif language == "java":
        import clang_format
        result = run_clang(text, "java", clang_format)
        from pygments import lex
        from pygments.lexers import JavaLexer
        def significant(value):
            return [(token, content) for token, content in lex(value, JavaLexer(stripnl=False, ensurenl=False)) if not content.isspace()]
        if significant(text) != significant(result):
            raise ValueError("Форматирование изменило токены Java. Исходный текст сохранён.")
    else:
        raise ValueError("Выберите Python, SQL, Java или JSON для форматирования")
    return result.rstrip("\n") + ("\n" if text.endswith("\n") else "")


def run_clang(text, language, module):
    binary = Path(module.__file__).parent / "data" / "bin" / ("clang-format.exe" if sys.platform == "win32" else "clang-format")
    if not binary.is_file():
        raise ValueError("Форматтер отсутствует в сборке приложения")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        result = subprocess.run([str(binary), "--assume-filename=fragment." + language,
                                 "--style={BasedOnStyle: Google, IndentWidth: 4, SortIncludes: Never}",
                                 "--fail-on-incomplete-format"], input=text, text=True, encoding="utf-8", capture_output=True, timeout=10, **options)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Не удалось завершить форматирование") from None
    if result.returncode:
        raise ValueError("Фрагмент неполный или содержит ошибку. Исправьте текст перед форматированием.")
    return result.stdout
