"""Optional syntax signals for OCR text; never compile or execute source code."""
import ast
from dataclasses import dataclass
import json
import re
import textwrap


@dataclass(frozen=True)
class SyntaxHint:
    language: str
    line: int | None
    message: str

    @property
    def label(self):
        location = f" · строка {self.line}" if self.line else ""
        return f"Возможная ошибка {self.language}{location}: {self.message}"


def readable_message(message):
    common = {
        "invalid syntax": "некорректный синтаксис",
        "expected ':'": "ожидается двоеточие",
        "unexpected indent": "неожиданный отступ",
        "unindent does not match any outer indentation level": "отступ не совпадает с предыдущими уровнями",
        "Expecting ',' delimiter": "ожидается запятая",
        "Expecting ':' delimiter": "ожидается двоеточие",
        "Expecting property name enclosed in double quotes": "имя свойства должно быть в двойных кавычках",
        "Expecting value": "ожидается значение",
        "Extra data": "лишние данные после JSON",
    }
    if message in common:
        return common[message]
    if "was never closed" in message:
        return "не закрыта скобка " + message.split("'")[1]
    if message.startswith(("unterminated string", "Unterminated string")):
        return "не закрыта строка"
    if message.startswith("expected an indented block"):
        return "ожидается блок с отступом"
    return message


def guess_language(text):
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if re.search(r"(?im)^\s*(?:SELECT|WITH\s+\w+\s+AS|INSERT\s+INTO|UPDATE\s+\w+\s+SET|CREATE\s+TABLE|DELETE\s+FROM)\b", text):
        return "sql"
    if re.search(r"\b(?:public|private|protected)\s+(?:static\s+)?(?:class|void|int|String)\b|\bSystem\.out\.", text):
        return "java"
    if re.search(r"(?m)^\s*(?:async\s+def|def|class|import|from|if|elif|for|while|try|with)\b", text):
        return "python"
    return "none"


def json_too_deep(text, limit=256):
    depth, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > limit:
                return True
        elif character in "]}":
            depth -= 1
    return False


def syntax_hint(text: str, language="auto") -> SyntaxHint | None:
    if not text.strip() or language == "none":
        return None
    if language == "auto":
        language = guess_language(text)
    if language not in {"python", "json"}:
        return None
    name = "Python" if language == "python" else "JSON"
    # Keep arbitrary clipboard/OCR input from consuming excessive parser memory.
    if len(text) > 100_000:
        return SyntaxHint(name, None, "фрагмент слишком большой для проверки")
    try:
        if language == "python":
            # A screenshot often contains an indented fragment of a larger file.
            ast.parse(textwrap.dedent(text), mode="exec")
        else:
            if json_too_deep(text):
                return SyntaxHint(name, None, "слишком много уровней вложенности для проверки")
            json.loads(text)
    except SyntaxError as error:
        return SyntaxHint(name, error.lineno, readable_message(error.msg))
    except json.JSONDecodeError as error:
        return SyntaxHint(name, error.lineno, readable_message(error.msg))
    except (ValueError, RecursionError, MemoryError):
        return SyntaxHint(name, None, "не удалось разобрать этот фрагмент")
    return None
