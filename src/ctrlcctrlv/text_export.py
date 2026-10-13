"""Suggest a text-file extension and save the edited content literally."""
import ast
import json
import os
from pathlib import Path
import re
import tempfile
import textwrap
import tomllib

from .syntax import guess_language


FORMATS = {
    "py": "Python", "sql": "SQL", "java": "Java", "json": "JSON",
    "js": "JavaScript", "html": "HTML", "xml": "XML", "toml": "TOML",
    "md": "Markdown", "txt": "Текст",
}


def suggest_extension(text, language="auto"):
    selected = {"python": "py", "sql": "sql", "java": "java", "json": "json"}.get(language)
    if selected:
        return selected
    sample = text[:100_000]
    stripped = sample.lstrip()
    if re.match(r"(?i)(?:<!doctype\s+html\b|<html\b)", stripped):
        return "html"
    if stripped.startswith("<?xml"):
        return "xml"
    try:
        if isinstance(json.loads(sample), (dict, list)):
            return "json"
    except (ValueError, RecursionError):
        pass
    if re.match(r'\{\s*"[^"\n]+"\s*:', stripped):
        return "json"
    try:
        opening = [line for line in sample.splitlines() if line.strip()][:2]
        configuration = tomllib.loads("\n".join(opening))
        if any(isinstance(value, dict) and value for value in configuration.values()):
            return "toml"
    except (tomllib.TOMLDecodeError, RecursionError):
        pass
    if re.search(r"(?m)^\s*(?:(?:export\s+)?(?:const|let|var)\s+\w+\s*=|(?:export\s+)?(?:async\s+)?function\s+\w+\s*\()", sample):
        return "js"
    detected = guess_language(sample)
    if detected in {"sql", "java"}:
        return detected
    if re.search(r"(?m)^\s*(?:(?:async\s+)?def\s+\w+\s*\(|class\s+\w+\s*[:(]|from\s+[\w.]+\s+import\s|import\s+[\w.]+\s*$)", sample):
        return "py"
    try:
        statements = ast.parse(textwrap.dedent(sample)).body
        if any(not isinstance(node, ast.Expr) or isinstance(node.value, ast.Call) for node in statements):
            return "py"
    except (SyntaxError, ValueError, RecursionError):
        pass
    if re.search(r"(?m)^#{1,6} \S", sample) and re.search(r"(?m)^(?:[-*] |```)", sample):
        return "md"
    return "txt"


def save_text(path, text):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ctrlc-save-", delete=False) as staged:
        temporary = Path(staged.name)
    try:
        temporary.write_bytes(text.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
