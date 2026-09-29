"""Token-budgeted context: rank files, include whole ones while they fit,
outline the rest, and keep the running conversation inside the model window."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.repo.detector import iter_source_files
from forge.repo.symbols import outline


def estimate_tokens(text: str) -> int:
    """Cheap, deliberately conservative estimate (~3.6 chars per token)."""
    return max(1, int(len(text) / 3.6) + 1)


def messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content) + 4
        for call in message.get("tool_calls") or []:
            total += estimate_tokens(str(call)) + 4
    return total


@dataclass
class FileSlice:
    path: str
    text: str
    truncated: bool


def _score(rel: str, terms: list[str]) -> int:
    lowered = rel.lower()
    score = sum(6 for term in terms if term and term in lowered)
    if any(part in lowered for part in ("/src/", "src/", "internal/", "app/", "cmd/")):
        score += 2
    if lowered.endswith((".ts", ".tsx", ".go", ".py", ".sql")):
        score += 2
    if "test" in lowered or "spec" in lowered:
        score -= 1
    return score


def rank_files(root: Path, request: str, limit: int = 40) -> list[Path]:
    terms = [t.lower() for t in re.findall(r"[A-Za-z0-9_]{3,}", request)]
    files = iter_source_files(root)
    scored = sorted(files, key=lambda p: _score(str(p.relative_to(root)), terms), reverse=True)
    return scored[:limit]


def build_repo_context(root: Path, request: str, token_budget: int) -> str:
    """Whole-file context for the most relevant files, outlines for the rest."""
    budget = max(2_000, token_budget)
    used = 0
    whole: list[str] = []
    outlines: list[str] = []

    for path in rank_files(root, request):
        rel = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        cost = estimate_tokens(text) + 30
        if used + cost <= budget * 0.75:
            whole.append(f"--- FILE {rel} ---\n{text}")
            used += cost
        else:
            sketch = outline(path)
            if sketch:
                line = f"{rel}: {sketch}"
                line_cost = estimate_tokens(line)
                if used + line_cost <= budget:
                    outlines.append(line)
                    used += line_cost

    blocks = []
    if whole:
        blocks.append("# Full file contents\n" + "\n\n".join(whole))
    if outlines:
        blocks.append("# Symbol outlines for other files\n" + "\n".join(outlines))
    return "\n\n".join(blocks)


def compress_history(
    messages: list[dict[str, Any]], token_budget: int, keep_recent: int = 14
) -> list[dict[str, Any]]:
    """Keep the system prompt and the most recent turns; summarise what falls out.

    Tool-call and tool-result messages are dropped as a pair so the transcript
    stays valid for the API.
    """
    if messages_tokens(messages) <= token_budget:
        return messages

    system = [m for m in messages[:1] if m.get("role") == "system"]
    rest = messages[len(system) :]
    recent = rest[-keep_recent:]

    # Never start the retained window with an orphan tool result.
    while recent and recent[0].get("role") == "tool":
        recent = recent[1:]

    dropped = rest[: len(rest) - len(recent)]
    if dropped:
        lines = ["(Older tool output was dropped to stay under the token limit; "
                 "re-read any file you still need with read_file.)"]
        for message in dropped:
            role = message.get("role")
            if role == "assistant" and message.get("tool_calls"):
                names = ", ".join(c["function"]["name"] for c in message["tool_calls"])
                lines.append(f"- called tools: {names}")
            elif role == "tool":
                body = str(message.get("content", ""))[:160]
                lines.append(f"- tool result: {body}")
            elif isinstance(message.get("content"), str):
                lines.append(f"- {role}: {message['content'][:200]}")
        note = {
            "role": "system",
            "content": "Earlier steps in this run (compressed):\n" + "\n".join(lines[-60:]),
        }
        return [*system, note, *recent]
    return [*system, *recent]


def fit_messages(
    messages: list[dict[str, Any]], token_budget: int
) -> list[dict[str, Any]]:
    out = list(compress_history(messages, token_budget))
    # Last resort: hard-trim the largest individual contents until we fit.
    guard = 0
    while messages_tokens(out) > token_budget and len(out) > 1 and guard < 200:
        guard += 1
        biggest = max(range(1, len(out)), key=lambda i: len(str(out[i].get("content") or "")))
        content = str(out[biggest].get("content") or "")
        if len(content) < 800:
            if len(out) > 2:
                out.pop(biggest)
            else:
                break
        else:
            keep = max(400, len(content) // 4)
            note = "\n…[truncated by context budget"
            if out[biggest].get("role") == "tool":
                note += " - re-run the tool (read_file with start_line/end_line) to see the rest"
            out[biggest] = {**out[biggest], "content": content[:keep] + note + "]"}
    return out
