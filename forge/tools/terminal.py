"""Terminal tool: run a shell command in the project's session."""

from __future__ import annotations

from typing import Any

from forge.config import redact
from forge.sandbox.policies import Denied
from forge.tools.context import ToolContext
from forge.tools.registry import Tool


def _run_command(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    command = str(args["command"]).strip()
    try:
        ctx.policy.check_command(command)
    except Denied as exc:
        return {"ok": False, "error": str(exc)}
    if not ctx.policy.request("run_command", command):
        return {"ok": False, "error": "user declined to run this command"}

    timeout = int(args.get("timeout") or ctx.cfg.budget.command_timeout_seconds)
    result = ctx.terminal.run(command, timeout=timeout)
    return {
        "ok": result.exit_code == 0,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "cwd": result.cwd,
        "output": redact(result.as_text(), ctx.cfg),
    }


TOOLS = [
    Tool(
        name="run_command",
        description=(
            "Run a shell command in the project. The working directory persists between calls, "
            "so `cd sub/dir` then a later command works. Use it to build, run tests, inspect logs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds; default 300"},
            },
            "required": ["command"],
        },
        run=_run_command,
        mutates=True,
    ),
]
