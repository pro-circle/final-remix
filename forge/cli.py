"""Forge CLI - the primary interface. Run `forge` inside a project."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from forge import __version__
from forge.config import (
    Config,
    load_config,
    write_env_example,
    write_starter_config,
)
from forge.events import EventBus
from forge.models.client import FleetClient
from forge.models.router import PHASE_ROLE

from forge.orchestrator.engine import Orchestrator
from forge.repo.detector import detect
from forge.sandbox.checkpoints import CheckpointManager
from forge.sandbox.policies import ApprovalPolicy
from forge.store import Store
from forge.ui.render import CliRenderer, make_console
from forge.vscode import write_vscode_assets

app = typer.Typer(
    add_completion=False,
    help="Forge - autonomous coding agent for your terminal and VS Code.",
    invoke_without_command=True,
)
console = make_console()


def _project_root(path: Optional[str]) -> Path:
    return Path(path or os.getcwd()).resolve()


def _build_policy(cfg: Config, auto: bool) -> ApprovalPolicy:
    policy = ApprovalPolicy(auto_approve=auto or cfg.auto_approve)

    closed = {"stdin": False}

    def ask(kind: str, detail: str) -> bool:
        console.print(
            Panel(
                Text(detail[:4000], style="forge.text"),
                title=f"[forge.warn]Approve {kind}?[/]",
                border_style="forge.rule",
                padding=(1, 2),
            )
        )
        if closed["stdin"]:
            return False
        try:
            answer = Prompt.ask(
                "[forge.accent]allow[/]",
                choices=["y", "n", "a"],
                default="y",
                show_choices=True,
            )
        except (EOFError, KeyboardInterrupt):
            # No one is there to answer (piped input ended): decline everything from now on.
            closed["stdin"] = True
            console.print("[forge.dim]No input available; declining.[/]")
            return False
        if answer == "a":
            policy.allow_always(kind)
            return True
        return answer == "y"

    policy.asker = ask
    return policy


def _make_orchestrator(root: Path, cfg: Config, auto: bool, verbose: bool):
    profile = detect(root)
    bus = EventBus()
    store = Store()
    renderer = CliRenderer(console, verbose=verbose)
    bus.subscribe(renderer.handle)
    client = FleetClient(cfg)
    orchestrator = Orchestrator(
        root=root,
        cfg=cfg,
        bus=bus,
        store=store,
        policy=_build_policy(cfg, auto),
        client=client,
        profile=profile,
    )
    return orchestrator, renderer, store, profile



def _require_keys(cfg: Config) -> None:
    if not cfg.all_keys:
        console.print(
            Panel(
                Text(
                    "No API keys found.\n\n"
                    "Add up to five Groq keys and five Gemini keys to .env in this\n"
                    "folder (copy .env.example, or run `forge init`), or set\n"
                    "GROQ_API_KEY (… GROQ_API_KEY_5) and GEMINI_API_KEY\n"
                    "(… GEMINI_API_KEY_5) in your environment.",
                    style="forge.text",
                ),
                title="[forge.fail]Not configured[/]",
                border_style="forge.rule",
                padding=(1, 2),
            )
        )
        raise typer.Exit(code=1)



# ----------------------------------------------------------------- commands
@app.callback()
def default(
    ctx: typer.Context,
    path: Optional[str] = typer.Option(None, "--path", "-p", help="Project folder (defaults to current)"),
    auto: bool = typer.Option(False, "--auto", help="Approve file writes and commands automatically"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the interactive Forge session when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    cfg = load_config()
    root = _project_root(path)
    _require_keys(cfg)
    orchestrator, renderer, _store, profile = _make_orchestrator(root, cfg, auto, verbose)
    renderer.banner(str(root), profile.summary())
    console.print(
        "[forge.dim]Describe a task, or use /inspect, /undo, /keys, /exit.[/]\n"
    )
    while True:
        try:
            request = Prompt.ask("[forge.brand]>[/]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[forge.dim]bye[/]")
            return
        if not request:
            continue
        if request in {"/exit", "/quit", "exit", "quit"}:
            console.print("[forge.dim]bye[/]")
            return
        if request.startswith("/inspect"):
            target = request[len("/inspect") :].strip()
            result = orchestrator.run(target or "Audit the whole project.", mode="inspect")
            renderer.summary(result)
            continue
        if request.startswith("/undo"):
            _undo_last(root)
            continue
        if request.startswith("/keys"):
            _print_keys(orchestrator.client)
            continue
        result = orchestrator.run(request, mode="task")
        renderer.summary(result)


@app.command()
def ask(
    request: str = typer.Argument(..., help="What Forge should do"),
    path: Optional[str] = typer.Option(None, "--path"),
    auto: bool = typer.Option(False, "--auto"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run a single task non-interactively (used by the VS Code tasks)."""
    cfg = load_config()
    _require_keys(cfg)
    root = _project_root(path)
    orchestrator, renderer, _store, profile = _make_orchestrator(root, cfg, auto, verbose)
    renderer.banner(str(root), profile.summary())
    result = orchestrator.run(request, mode="task")
    renderer.summary(result)
    raise typer.Exit(code=0 if result.status in {"done", "needs_attention"} else 1)


@app.command()
def inspect(
    scope: str = typer.Argument("Audit the whole project.", help="What to inspect"),
    path: Optional[str] = typer.Option(None, "--path"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Audit the project and list findings without changing anything."""
    cfg = load_config()
    _require_keys(cfg)
    root = _project_root(path)
    orchestrator, renderer, _store, profile = _make_orchestrator(root, cfg, False, verbose)
    renderer.banner(str(root), profile.summary())
    result = orchestrator.run(scope, mode="inspect")
    renderer.summary(result)


@app.command()
def undo(
    run_id: Optional[str] = typer.Argument(None, help="Run to undo (defaults to the last one)"),
    path: Optional[str] = typer.Option(None, "--path"),
) -> None:
    """Restore the files a run changed."""
    root = _project_root(path)
    _undo_last(root, run_id)


def _undo_last(root: Path, run_id: str | None = None) -> None:
    store = Store()
    target = run_id or store.last_run_id(str(root))
    if not target:
        console.print("[forge.warn]No runs recorded for this project yet.[/]")
        return
    restored = CheckpointManager.restore(store, target, root)
    if not restored:
        console.print(f"[forge.dim]Nothing to restore for run {target}.[/]")
        return
    console.print(f"[forge.ok]Restored {len(restored)} file(s) from run {target}:[/]")
    for item in restored:
        console.print(f"  [forge.text]{item}[/]")


@app.command()
def runs(path: Optional[str] = typer.Option(None, "--path"), limit: int = 15) -> None:
    """List recent runs."""
    store = Store()
    table = Table(border_style="forge.rule")
    for column in ("run", "status", "request", "changed"):
        table.add_column(column, style="forge.text")
    for row in store.recent_runs(limit):
        events = store.events_for(row["id"])
        changed = {
            e["data"].get("path") for e in events if e["kind"] == "patch" and e["data"].get("path")
        }
        table.add_row(row["id"], row["status"], row["request"][:60], str(len(changed)))
    console.print(table)


@app.command()
def findings(run_id: Optional[str] = typer.Argument(None)) -> None:
    """Show the findings recorded by an inspection run (latest run by default)."""
    store = Store()
    if not run_id:
        run_id = store.last_run_id(str(Path.cwd().resolve()))
        if not run_id:
            console.print("[forge.dim]No runs yet in this folder.[/]")
            return
    table = Table(border_style="forge.rule")
    for column in ("severity", "title", "file", "line"):
        table.add_column(column, style="forge.text")
    for item in store.findings_for(run_id):
        table.add_row(
            str(item["severity"]), str(item["title"]), str(item["file"] or ""), str(item["line"] or "")
        )
    console.print(table)


@app.command()
def keys() -> None:
    """Show key health (Groq and Gemini) and the model chain used per phase."""
    cfg = load_config()
    _require_keys(cfg)
    _print_keys(FleetClient(cfg))


def _print_keys(client: FleetClient) -> None:
    table = Table(border_style="forge.rule", title="[forge.brand]Keys[/]")
    for column in ("provider", "key", "ok", "fails", "cooldown", "last error"):
        table.add_column(column, style="forge.text")
    for item in client.keys.health():
        table.add_row(
            str(item.get("provider", "groq")),
            str(item["label"]),
            str(item["successes"]),
            str(item["failures"]),
            f"{item['cooldown_s']}s",
            str(item["last_error"] or ""),
        )
    console.print(table)
    models = Table.grid(padding=(0, 2))
    models.add_column(style="forge.dim")
    models.add_column(style="forge.text")
    cfg = client.cfg
    for phase in PHASE_ROLE:
        chain = cfg.candidates(phase, PHASE_ROLE[phase])
        models.add_row(
            phase,
            "  →  ".join(f"{m} ({cfg.tpm_limit(m):,} tpm)" for m in chain),
        )
    console.print(Panel(models, title="[forge.accent]Model chain per phase[/]", border_style="forge.rule"))



@app.command()
def init(
    path: Optional[str] = typer.Option(None, "--path"),
    vscode: bool = typer.Option(True, help="Write .vscode tasks for this project"),
) -> None:
    """Create .env.example for keys, ~/.forge/config.toml, and VS Code tasks."""
    config_path = write_starter_config()
    console.print(f"[forge.ok]Config:[/] [forge.text]{config_path}[/]")
    root = _project_root(path)
    env_example = write_env_example(root)
    console.print(f"[forge.ok]Keys:[/] [forge.text]{env_example}[/]")
    if vscode:
        written = write_vscode_assets(root)
        for file in written:
            console.print(f"[forge.ok]Wrote:[/] [forge.text]{file}[/]")
    console.print(
        "\n[forge.dim]Copy .env.example to .env, paste your Groq keys, then run `forge` in this folder.[/]"
    )


@app.command()
def serve(
    path: Optional[str] = typer.Option(None, "--path"),
    host: str = "127.0.0.1",
    port: int = 8787,
) -> None:
    """Run the local Forge HTTP/SSE server (for the web workspace)."""
    import uvicorn

    from forge.server.app import create_app

    root = _project_root(path)
    console.print(f"[forge.brand]Forge server[/] [forge.dim]{root} → http://{host}:{port}[/]")
    uvicorn.run(create_app(root), host=host, port=port, log_level="info")


@app.command()
def version() -> None:
    """Print the Forge version."""
    console.print(f"[forge.brand]Forge[/] [forge.text]{__version__}[/]")


def _normalize_argv(argv: list[str]) -> list[str]:
    """Allow `forge ./project` while keeping subcommands like `forge version` intact.

    A positional on the root callback would swallow subcommand names, so the
    project folder is a --path option and a bare leading folder is rewritten to it.
    """
    import typer.main as _tm

    commands = set(_tm.get_command(app).commands)  # type: ignore[attr-defined]
    flags_with_value = {"--path", "-p"}
    out: list[str] = []
    i = 0
    rewritten = False
    while i < len(argv):
        arg = argv[i]
        if arg in flags_with_value:
            out += argv[i : i + 2]
            i += 2
            continue
        if arg.startswith("-") or rewritten:
            out += argv[i:] if not arg.startswith("-") else [arg]
            if not arg.startswith("-"):
                break
            i += 1
            continue
        if arg in commands:
            out += argv[i:]
            break
        out += ["--path", arg]
        rewritten = True
        i += 1
    return out


def main() -> None:
    import sys

    from forge.models.key_manager import NoKeysAvailable

    try:
        app(args=_normalize_argv(sys.argv[1:]), prog_name="forge", standalone_mode=True)
    except NoKeysAvailable as exc:
        console.print(f"[forge.err]{exc}[/]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
