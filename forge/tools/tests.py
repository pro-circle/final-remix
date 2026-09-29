"""Test tool: run the project's detected test/build commands and report results."""

from __future__ import annotations

import re
from typing import Any

from forge.config import redact
from forge.events import TEST_RESULT
from forge.sandbox.policies import Denied
from forge.tools.context import ToolContext
from forge.tools.registry import Tool

SUMMARY_PATTERNS = [
    re.compile(r"(\d+) passed[^\n]*?(?:(\d+) failed)?", re.I),
    re.compile(r"Tests:\s+(?:(\d+) failed, )?(\d+) passed", re.I),
    re.compile(r"ok\s+\S+\s+[\d.]+s", re.I),
]


def _run_tests(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        candidates = ctx.profile.test_commands or ctx.profile.build_commands
        if not candidates:
            return {
                "ok": False,
                "error": "no test command detected; pass one explicitly in `command`",
            }
        command = candidates[0]
    try:
        ctx.policy.check_command(command)
    except Denied as exc:
        return {"ok": False, "error": str(exc)}
    if not ctx.policy.request("run_command", command):
        return {"ok": False, "error": "user declined to run tests"}

    result = ctx.terminal.run(command, timeout=int(args.get("timeout") or 900))
    passed = result.exit_code == 0
    summary = ""
    for pattern in SUMMARY_PATTERNS:
        match = pattern.search(result.stdout or "")
        if match:
            summary = match.group(0)
            break
    ctx.bus.emit(
        TEST_RESULT,
        run_id=ctx.run_id,
        command=command,
        passed=passed,
        summary=summary,
        exit_code=result.exit_code,
    )
    return {
        "ok": passed,
        "command": command,
        "exit_code": result.exit_code,
        "summary": summary,
        "output": redact(result.as_text(12000), ctx.cfg),
    }


TOOLS = [
    Tool(
        name="run_tests",
        description=(
            "Run the project's tests (auto-detected if no command given). "
            "Always use this to verify a change before reporting it done."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer"},
            },
        },
        run=_run_tests,
        mutates=True,
    ),
]
