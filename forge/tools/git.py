"""Git tools: status, diff, log and an approval-gated commit."""

from __future__ import annotations

from typing import Any

from forge.config import redact
from forge.tools.context import ToolContext
from forge.tools.registry import Tool


def _git(ctx: ToolContext, command: str, timeout: int = 60) -> dict[str, Any]:
    result = ctx.terminal.run(command, timeout=timeout)
    return {
        "ok": result.exit_code == 0,
        "exit_code": result.exit_code,
        "output": redact(result.as_text(), ctx.cfg),
    }


def _status(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return _git(ctx, "git status --short --branch")


def _diff(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = str(args.get("path") or "")
    staged = " --staged" if args.get("staged") else ""
    suffix = f" -- {path}" if path else ""
    return _git(ctx, f"git --no-pager diff{staged}{suffix}")


def _log(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit") or 10)
    return _git(ctx, f"git --no-pager log --oneline -n {limit}")


def _commit(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    message = str(args["message"]).replace('"', "'")
    if not ctx.policy.request("git_commit", f"commit: {message}"):
        return {"ok": False, "error": "user declined the commit"}
    staged = _git(ctx, "git add -A")
    if not staged["ok"]:
        return staged
    return _git(ctx, f'git commit -m "{message}"')


TOOLS = [
    Tool(
        name="git_status",
        description="Show the working tree status.",
        parameters={"type": "object", "properties": {}},
        run=_status,
    ),
    Tool(
        name="git_diff",
        description="Show the current diff, optionally for one path.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "staged": {"type": "boolean"}},
        },
        run=_diff,
    ),
    Tool(
        name="git_log",
        description="Show recent commits.",
        parameters={"type": "object", "properties": {"limit": {"type": "integer"}}},
        run=_log,
    ),
    Tool(
        name="git_commit",
        description="Stage everything and commit with a message. Requires approval.",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        run=_commit,
        mutates=True,
    ),
]
