"""Approval policy and command safety rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Commands Forge refuses outright, even with approval.
BLOCKED = [
    re.compile(r"\brm\s+-rf\s+/(?:\s|$)"),
    re.compile(r"\brm\s+-rf\s+~"),
    re.compile(r":\(\)\s*\{.*\};\s*:"),  # fork bomb
    re.compile(r"\bmkfs(\.|\s)"),
    re.compile(r"\bdd\s+if=.*of=/dev/"),
    re.compile(r"\b(?:shutdown|reboot)\b"),
    re.compile(r"\bgit\s+push\b.*--force"),
    re.compile(r"\bcurl\b[^|]*\|\s*(?:ba)?sh"),
    re.compile(r"\bchmod\s+-R\s+777\s+/"),
]

# Shell commands that would expose secrets files or the key-bearing environment.
SECRET_COMMANDS = [
    re.compile(r"(?:^|[\s/'\"=<>|;&])\.env(?!\.example)(?:\.[\w-]+)?(?=$|[\s'\";|&<>)])"),
    re.compile(r"(?:^|[\s;|&])(?:env|printenv|set|export\s+-p)\s*(?:$|[;|&>])"),
    re.compile(r"GROQ_API_KEY|GEMINI_API_KEY"),
    re.compile(r"\b(?:id_rsa|\.npmrc|\.netrc)\b"),
]

# Sensitive files Forge never reads or writes.
SECRET_FILES = {".env", ".env.local", ".env.production", "id_rsa", ".npmrc", ".netrc"}


class Denied(RuntimeError):
    pass


@dataclass
class ApprovalPolicy:
    """Decides whether a side-effecting tool call may run.

    `asker` is supplied by the CLI (an interactive prompt) or by the server
    (auto-deny unless the run was started in auto-approve mode).
    """

    auto_approve: bool = False
    asker: Callable[[str, str], bool] | None = None
    always_allowed: set[str] = field(default_factory=set)

    def check_command(self, command: str) -> None:
        for pattern in BLOCKED:
            if pattern.search(command):
                raise Denied(f"Blocked by safety policy: {command}")
        for pattern in SECRET_COMMANDS:
            if pattern.search(command):
                raise Denied(f"Refusing a command that touches secrets: {command}")

    def request(self, kind: str, detail: str) -> bool:
        if self.auto_approve or kind in self.always_allowed:
            return True
        if self.asker is None:
            return False
        return self.asker(kind, detail)

    def allow_always(self, kind: str) -> None:
        self.always_allowed.add(kind)


def is_secret_path(path: Path) -> bool:
    if path.name == ".env.example":
        return False
    return path.name in SECRET_FILES or path.name.startswith(".env.")


def resolve_inside(root: Path, candidate: str) -> Path:
    """Resolve a tool-supplied path, refusing anything outside the project."""
    path = (root / candidate).resolve() if not Path(candidate).is_absolute() else Path(candidate).resolve()
    root_resolved = root.resolve()
    if root_resolved != path and root_resolved not in path.parents:
        raise Denied(f"Path escapes the project root: {candidate}")
    if is_secret_path(path):
        raise Denied(f"Refusing to touch a secrets file: {candidate}")
    return path
