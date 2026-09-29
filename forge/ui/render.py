"""CLI renderer: turns the event stream into a live terminal transcript."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from forge.events import (
    APPROVAL,
    Event,
    FINDING,
    NOTICE,
    PATCH,
    PHASE,
    PLAN,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    TEST_RESULT,
    THOUGHT,
    TOOL_CALL,
    TOOL_RESULT,
    USAGE,
)
from forge.ui.theme import FORGE_THEME, SEVERITY_STYLE

PHASE_LABEL = {
    "explore": "Explorer",
    "plan": "Planner",
    "code": "Coder",
    "debug": "Debugger",
    "test": "Tester",
    "review": "Reviewer",
    "inspect": "Inspector",
}


def make_console() -> Console:
    return Console(theme=FORGE_THEME, highlight=False, soft_wrap=True)


class CliRenderer:
    def __init__(self, console: Console, show_diffs: bool = True, verbose: bool = False) -> None:
        self.console = console
        self.show_diffs = show_diffs
        self.verbose = verbose
        self._phase = ""

    def banner(self, project: str, profile: str) -> None:
        body = Text()
        body.append("Project\n", style="forge.dim")
        body.append(f"{project}\n\n", style="forge.text")
        body.append("Detected\n", style="forge.dim")
        body.append(profile, style="forge.text")
        self.console.print(
            Panel(body, title="[forge.brand]FORGE[/]", border_style="forge.rule", padding=(1, 2))
        )

    def handle(self, event: Event) -> None:
        kind = event.kind
        data = event.data
        if kind == RUN_STARTED:
            self.console.rule(f"[forge.accent]{data.get('mode', 'task')}[/]", style="forge.rule")
        elif kind == PHASE:
            phase = data.get("phase", "")
            self._phase = phase
            label = PHASE_LABEL.get(phase, phase.title())
            self.console.print(
                f"\n[forge.agent]{label:<10}[/] [forge.dim]{data.get('model','')}[/]"
            )
        elif kind == PLAN:
            self.console.print(
                Panel(
                    Text(str(data.get("plan", "")), style="forge.text"),
                    title="[forge.accent]Plan[/]",
                    border_style="forge.rule",
                    padding=(1, 2),
                )
            )
        elif kind == THOUGHT:
            text = str(data.get("text", "")).strip()
            if text:
                self.console.print(Text(text, style="forge.text"))
        elif kind == TOOL_CALL:
            args = data.get("args") or {}
            hint = args.get("path") or args.get("command") or args.get("pattern") or ""
            self.console.print(
                f"  [forge.tool]→ {data.get('tool')}[/] [forge.dim]{str(hint)[:110]}[/]"
            )
        elif kind == TOOL_RESULT:
            if not data.get("ok"):
                detail = str(data.get("detail") or "")[:200]
                self.console.print(f"    [forge.fail]✗ {data.get('tool')}[/] [forge.dim]{detail}[/]")
            elif self.verbose:
                self.console.print(f"    [forge.ok]✓ {data.get('tool')}[/]")
        elif kind == PATCH:
            self.console.print(f"    [forge.ok]✓ patched[/] [forge.text]{data.get('path')}[/]")
            if self.show_diffs and data.get("diff"):
                self.console.print(
                    Syntax(str(data["diff"])[:4000], "diff", theme="ansi_dark", word_wrap=True)
                )
        elif kind == TEST_RESULT:
            style = "forge.ok" if data.get("passed") else "forge.fail"
            mark = "✓" if data.get("passed") else "✗"
            self.console.print(
                f"    [{style}]{mark} {data.get('command')}[/] [forge.dim]{data.get('summary','')}[/]"
            )
        elif kind == FINDING:
            severity = str(data.get("severity", "medium")).lower()
            style = SEVERITY_STYLE.get(severity, "forge.warn")
            where = f" {data.get('file')}:{data.get('line')}" if data.get("file") else ""
            self.console.print(
                f"  [{style}]● {severity.upper()}[/] [forge.text]{data.get('title')}[/]"
                f"[forge.dim]{where}[/]"
            )
        elif kind == NOTICE:
            self.console.print(f"  [forge.warn]! {data.get('message')}[/]")
        elif kind == RUN_FAILED:
            self.console.print(f"\n[forge.fail]Run failed:[/] {data.get('error')}")
        elif kind == USAGE and self.verbose:
            self.console.print(
                f"[forge.dim]tokens in {data.get('in')} / out {data.get('out')} "
                f"over {data.get('requests')} requests[/]"
            )

    def summary(self, result) -> None:  # RunResult
        table = Table.grid(padding=(0, 2))
        table.add_column(style="forge.dim")
        table.add_column(style="forge.text")
        status_style = {
            "done": "forge.ok",
            "needs_attention": "forge.warn",
            "failed": "forge.fail",
            "stopped": "forge.warn",
        }.get(result.status, "forge.text")
        table.add_row("Status", f"[{status_style}]{result.status}[/]")
        if result.tests_passed is not None:
            table.add_row("Verified", "yes" if result.tests_passed else "no")
        if result.changed_files:
            table.add_row("Changed", "\n".join(result.changed_files))
        if result.findings:
            counts: dict[str, int] = {}
            for finding in result.findings:
                key = str(finding.get("severity", "medium")).lower()
                counts[key] = counts.get(key, 0) + 1
            table.add_row(
                "Findings",
                ", ".join(f"{count} {name}" for name, count in counts.items()),
            )
        if result.risk:
            table.add_row("Risk", result.risk)
        table.add_row("Tokens", f"in {result.tokens.get('in',0)} / out {result.tokens.get('out',0)}")
        table.add_row("Undo", f"forge undo {result.run_id}")
        self.console.print(
            Panel(table, title="[forge.brand]Result[/]", border_style="forge.rule", padding=(1, 2))
        )
        if result.summary:
            self.console.print(Text(result.summary, style="forge.text"))
