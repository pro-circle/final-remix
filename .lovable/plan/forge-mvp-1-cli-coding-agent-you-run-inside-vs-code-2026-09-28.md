# Forge — MVP 1: CLI coding agent you run inside VS Code

Build Forge as a terminal-first agent, the way Claude Code works: you open your project in VS Code, type `forge` in the VS Code terminal, describe a task, and the agent reads your real files, runs commands, edits code, runs tests, and reports what changed.

No Lovable Cloud. Nothing hosted. Everything runs on your machine against your own Groq API keys.

## What you get

1. **A `forge` command** installed into your machine, usable from any project folder.
2. **A chat loop in the terminal**: project detection banner, your request, a live plan, per-agent activity lines, and a final summary of changed files.
3. **Real tool access**: read/write files, patch edits with diffs, run shell commands, git status/diff/commit, ripgrep search, run the project's tests.
4. **An approval gate**: writes, commands, and commits ask before they run (with an "always allow" per session), plus a blocklist for destructive commands.
5. **Checkpoints**: every run snapshots touched files so you can `forge undo` a bad change.
6. **VS Code integration**: a `.vscode` folder with tasks so "Forge: Ask" and "Forge: Inspect" run in the integrated terminal, and diffs open in VS Code's diff viewer.
7. **A local FastAPI server** (`forge serve`) exposing the run/terminal/git/inspection endpoints from your spec over SSE — so the web workspace can be added later without reworking the core.

Out of scope for MVP 1: web workspace UI, screenshot/visual agent, web research, Docker sandbox, Postgres/Redis, billing, teams.

## The run loop

```text
request -> detect project -> map repo -> select context -> plan
   -> execute tools -> observe -> patch -> run tests
   -> if failing: debug & repair (bounded retries) -> review -> report
```

## Models and keys

- Routing: `openai/gpt-oss-20b` for cheap classification, file selection, summarising; `openai/gpt-oss-120b` for planning, patching, debugging, review.
- Five-key manager: round-robin across up to 5 Groq keys with per-key health, rate-limit backoff, and a per-run token/spend ceiling that pauses the run when hit.
- Keys come from a local config file in your home folder (`~/.forge/config.toml`) or env vars. Never committed, redacted from all logs.
- Vision (`qwen/qwen3.8-27b`) is wired into the model router interface but unused until the visual agent phase.
- Must ensure that the complete context is given to the model over input context window of each model.
- Tune the models especially for working with React(Frontend), Golang(backend {fiber}), PostgreSQL. (not only).

## Technical notes

- Python 3.11+, packaged with `pyproject.toml`; install locally with `pipx install -e .` (or `uv tool install -e .`).
- CLI: Typer + Rich for panels, live status, and diff rendering.
- Repo intelligence for MVP: language/framework detection, ripgrep search, file-level import graph, and tree-sitter symbol extraction for Python/TS/JS.
- Terminal engine: persistent shell session per run via PTY, streaming output, timeouts, cwd pinned to the project root.
- Context engine: token-budgeted context builder with file ranking and summary compression of long files.
- Events: every step emits a structured event (run started, plan, tool call, tool result, patch, test result, finding, done) consumed by both the CLI renderer and the SSE endpoint.
- Persistence: SQLite at `~/.forge/forge.db` for runs, events, findings, checkpoints.
- Backend layout follows the spec's `app/` tree (orchestrator, agents, tools, repo, context, sandbox, persistence) build exactly as effective as codex, claude code and lovable (advanced UI/UX, software engineering, debugging).
- Tests: pytest covering key manager rotation, patch application, approval policy, and the debug-repair loop with a stubbed model.

## Look and feel

Dark terminal palette with a warm forge accent: near-black background, bone-white text, amber for active agent/status, orange-red for failures, muted green for passes. Mono throughout, boxed panels, no emoji-heavy output.

## Delivery order

1. Package skeleton, config, key manager, Groq client, event bus, SQLite store.
2. Tool engine: filesystem, patch, terminal, git, search, tests + approval policy.
3. Orchestrator state machine, agents (explorer, planner, coder, debugger, tester, reviewer), CLI renderer.
4. Checkpoints/undo, `.vscode` tasks, README with install steps.
5. `forge serve` FastAPI + SSE over the same event bus.