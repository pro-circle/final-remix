"""Terminal engine: a stateful shell session pinned to the project root.

State is kept by carrying the working directory and exported environment
between calls, which works identically on POSIX and Windows.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    cwd: str
    timed_out: bool = False

    def as_text(self, limit: int = 8000) -> str:
        body = (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()
        if len(body) > limit:
            head = body[: limit // 2]
            tail = body[-limit // 2 :]
            body = f"{head}\n…[{len(body) - limit} chars trimmed]…\n{tail}"
        status = "timed out" if self.timed_out else f"exit {self.exit_code}"
        return f"$ {self.command}\n({status}, {self.duration_s:.1f}s, cwd={self.cwd})\n{body}"


@dataclass
class TerminalSession:
    root: Path
    cwd: Path = field(init=False)
    env: dict[str, str] = field(
        default_factory=lambda: {
            k: v for k, v in os.environ.items() if not k.startswith(("GROQ_API_KEY", "GEMINI_API_KEY"))
        }
    )
    history: list[CommandResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cwd = self.root

    def chdir(self, relative: str) -> None:
        target = (self.cwd / relative).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError("cd outside the project root is not allowed")
        if not target.is_dir():
            raise ValueError(f"not a directory: {relative}")
        self.cwd = target

    def run(self, command: str, timeout: int = 300) -> CommandResult:
        stripped = command.strip()
        if stripped.startswith("cd "):
            try:
                self.chdir(stripped[3:].strip())
                result = CommandResult(command, 0, f"cwd -> {self.cwd}", "", 0.0, str(self.cwd))
            except ValueError as exc:
                result = CommandResult(command, 1, "", str(exc), 0.0, str(self.cwd))
            self.history.append(result)
            return result

        started = time.time()
        timed_out = False
        try:
            proc = subprocess.run(
                stripped,
                shell=True,
                cwd=self.cwd,
                env=self.env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            code = 124
        except OSError as exc:
            stdout, stderr, code = "", str(exc), 1

        result = CommandResult(
            command=stripped,
            exit_code=code,
            stdout=stdout,
            stderr=stderr,
            duration_s=time.time() - started,
            cwd=str(self.cwd),
            timed_out=timed_out,
        )
        self.history.append(result)
        return result
