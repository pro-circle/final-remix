"""Code search: ripgrep when available, pure-Python fallback otherwise."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from forge.repo.detector import IGNORE_DIRS, iter_source_files


def search(root: Path, pattern: str, max_results: int = 80) -> list[dict[str, object]]:
    rg = shutil.which("rg")
    if rg:
        cmd = [
            rg,
            "--line-number",
            "--no-heading",
            "--color=never",
            "--max-count=5",
            "-e",
            pattern,
        ]
        for name in IGNORE_DIRS:
            cmd += ["--glob", f"!{name}/**"]
        try:
            proc = subprocess.run(
                cmd, cwd=root, capture_output=True, text=True, timeout=45, check=False
            )
            results = []
            for line in proc.stdout.splitlines()[:max_results]:
                parts = line.split(":", 2)
                if len(parts) == 3:
                    results.append(
                        {"file": parts[0], "line": int(parts[1]), "text": parts[2].strip()[:300]}
                    )
            return results
        except (subprocess.SubprocessError, OSError):
            pass

    try:
        regex = re.compile(pattern)
    except re.error:
        regex = re.compile(re.escape(pattern))
    results: list[dict[str, object]] = []
    for path in iter_source_files(root):
        try:
            for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
            ):
                if regex.search(line):
                    results.append(
                        {
                            "file": str(path.relative_to(root)),
                            "line": number,
                            "text": line.strip()[:300],
                        }
                    )
                    if len(results) >= max_results:
                        return results
        except OSError:
            continue
    return results
