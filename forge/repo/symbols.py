"""Lightweight symbol extraction for TS/JS, Go, Python and SQL."""

from __future__ import annotations

import re
from pathlib import Path

PATTERNS = {
    ".py": [
        (re.compile(r"^\s*class\s+(\w+)", re.M), "class"),
        (re.compile(r"^\s*(?:async\s+)?def\s+(\w+)", re.M), "func"),
    ],
    ".go": [
        (re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)", re.M), "func"),
        (re.compile(r"^\s*type\s+(\w+)\s+(?:struct|interface)", re.M), "type"),
    ],
    ".ts": [
        (re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)", re.M), "func"),
        (re.compile(r"^\s*export\s+(?:const|let)\s+(\w+)", re.M), "const"),
        (re.compile(r"^\s*(?:export\s+)?(?:interface|type|class|enum)\s+(\w+)", re.M), "type"),
    ],
    ".sql": [
        (re.compile(r"create\s+(?:or\s+replace\s+)?table\s+(?:if\s+not\s+exists\s+)?([\w.\"]+)", re.I), "table"),
        (re.compile(r"create\s+(?:or\s+replace\s+)?function\s+([\w.\"]+)", re.I), "function"),
    ],
}
PATTERNS[".tsx"] = PATTERNS[".ts"]
PATTERNS[".js"] = PATTERNS[".ts"]
PATTERNS[".jsx"] = PATTERNS[".ts"]


def symbols_in(path: Path) -> list[dict[str, str]]:
    rules = PATTERNS.get(path.suffix)
    if not rules:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out: list[dict[str, str]] = []
    for pattern, kind in rules:
        for match in pattern.finditer(text):
            line = text[: match.start()].count("\n") + 1
            out.append({"name": match.group(1), "kind": kind, "line": str(line)})
    return out[:120]


def outline(path: Path) -> str:
    items = symbols_in(path)
    if not items:
        return ""
    return ", ".join(f"{i['kind']} {i['name']}:{i['line']}" for i in items[:60])
