# Forge — Complete CLI Coding Agent (from scratch, Groq-only)

A terminal coding agent like Claude Code. You open any project in VS Code, type `forge`, describe a task, and Forge reads your files, plans, edits, runs commands and tests, fixes failures, and reports what changed. Everything runs on your machine. No web UI, no server, no Gemini. Only six Groq keys, each from a different Groq organisation, so each key has its own free-tier limits.

## What you get

1. A `forge` command usable from any folder.
2. An interactive session: project banner, live plan, a spinner showing what Forge is doing ("reading…", "editing app.py…", "writing…", "deleting…", "running tests…", "thinking…"), and a final summary of changed files.
3. Real tools: read/write/patch files with diffs, search, shell commands, git, and the project's tests.
4. Parallel work: independent reads, searches and checks run at the same time. Sub-tasks can go to helper agents that run side by side on different keys.
5. Safety: approval before writes, commands and commits (y / n / always), a blocklist for destructive commands, project-root sandbox, `.env*` off limits.
6. Undo: every run snapshots touched files, and `forge undo` restores them.
7. History: runs, events and findings saved locally.
8. VS Code tasks for "Forge: Session", "Forge: Ask", "Forge: Inspect" and "Forge: Undo".

Not included: web workspace, HTTP API, Docker sandbox, Gemini or any other provider.

## Keys and models

- **Six Groq keys** in `.env`: `GROQ_API_KEY_1` … `GROQ_API_KEY_6`. Each key is labelled with its own organisation (`GROQ_API_KEY_1_ORG=team-a`, optional), so Forge tracks limits for each org separately.
- **Optional per-key model lists** (`GROQ_API_KEY_3_MODELS=...`). Forge also learns from "model not found" replies and stops sending that model to that key.
- **Models:**

| Role | Model | Used for |
| --- | --- | --- |
| deep | `openai/gpt-oss-120b` | planning, coding, debugging |
| fast | `openai/gpt-oss-20b` | exploring, summaries, test parsing, review |
| vision | `qwen/qwen3.8-27b` | screenshots and images (when given) |

- **Choice order for each type of work:**

```text
explore / inspect / summarise : 20b -> 120b
plan / code / debug           : 120b -> 20b
test / review                 : 20b -> 120b
visual                        : qwen -> (skip)
```

- **Rotation:** for each request, Forge picks the first model in that list that still has spare capacity on a key. Each key is tracked per model over a rolling 60 seconds (tokens per minute and requests per minute). Keys are picked least-used first. A key that gets "429 too many requests" pauses until its reset time. A key that gets "busy" (5xx) twice is skipped quickly. If every key is full, Forge waits for whichever frees up first and shows the countdown.
- **Request sizing:** each request is kept under the model's per-minute token limit (default 8,000 on free tier, adjustable in `~/.forge/config.toml`). Large files are read in pages. A "request too large" (413) reply shrinks the request and retries it.

## How a run works

```text
request -> detect project -> map repo -> select context -> plan
   -> execute tools (parallel where safe) -> observe -> patch -> run tests
   -> if failing: debug & repair (bounded retries) -> review -> report
```

Roles: Explorer, Planner, Coder, Debugger, Tester, Reviewer, Inspector. Each has its own instructions and model. Explorer, Reviewer and Inspector get read-only tools.

Built-in knowledge of React front ends, Go with Fiber, and PostgreSQL, applied by default.

## Look and feel

Dark terminal palette: near-black background, bone-white text, amber for activity, orange-red for failures, muted green for passes. Monospace, boxed panels, no emoji.

## Build order

1. **Foundation:** package skeleton, config and `.env` loading (6 keys, orgs, model lists, redaction), event bus, local history store.
2. **Model layer:** key manager (per org, per key, per model, 60s window), Groq client (retries, fast failover, 413 shrink), model router.
3. **Repo understanding:** stack detection, ripgrep search, symbol outlines, import graph.
4. **Context engine:** fit to the token budget, rank files, compress history while keeping tool-call pairs together.
5. **Tools and safety:** files, patch, search, terminal session, git, tests, approval policy, blocklist, checkpoints.
6. **Orchestrator:** phase state machine, roles, debug-repair loop, parallel tool batches, helper sub-agents.
7. **Terminal experience:** session REPL, live spinner verbs, diff rendering, summary panel, commands.
8. **VS Code + docs:** `.vscode` tasks and keybindings, README, `.env.example`.
9. **Tests + live check:** full test suite and a live script that exercises all six keys.

## Commands

| Command | What it does |
| --- | --- |
| `forge` | Interactive session |
| `forge ask "task"` | One-shot task |
| `forge inspect` | Audit, no changes |
| `forge undo [run]` | Restore a run's files |
| `forge runs` / `forge findings <run>` | History |
| `forge keys` | Health of each key and org, remaining capacity, and the model order for each type of work |
| `forge init` | Writes `.env.example`, `~/.forge/config.toml`, and the `.vscode` tasks |

In a session: `/inspect`, `/undo`, `/keys`, `/parallel on|off`, `/exit`.

## Technical details

- Python 3.11+, `pyproject.toml`, install with `pipx install -e .`. Dependencies: typer, rich, httpx, pydantic, tomli-w. FastAPI, uvicorn and sse-starlette are dropped.
- Layout:

```text
forge/
  cli.py  config.py  events.py  store.py  vscode.py
  models/     key_manager.py  client.py (GroqClient + FleetClient)  router.py
  repo/       detector.py  search.py  symbols.py  graph.py
  context/    builder.py
  tools/      registry.py filesystem.py search.py terminal.py git.py tests.py context.py
  sandbox/    policies.py  checkpoints.py  terminal.py
  orchestrator/ engine.py  prompts.py  parallel.py  subagents.py
  ui/         theme.py  render.py
tests/  scripts/live_check.py
```

- **Config:** `GROQ_API_KEY` plus `GROQ_API_KEY_1..6`, capped at 6 and de-duplicated. Placeholder values are ignored. Environment variables win over `.env`, which wins over `~/.forge/config.toml`. `<KEY>_ORG` and `<KEY>_MODELS` are optional. Per-model context, TPM and RPM tables can be overridden under `[limits]`.
- **KeyManager** (the only place usage is tracked): a `KeyState` holds the key, org, a supported-models set, a learned-unsupported set, deque windows for each model, `cooldown_until` and health. It provides `acquire(model, need)`, `record`, `headroom`, `wait_estimate`, `report_429(retry_after)`, `report_unsupported` and `health()`. Keys in the same org share one budget bucket, in case the user adds two keys from the same org.
- **Client:** `FleetClient` stays the single entry point (it wraps one `GroqClient` today and can take more providers later). `UpstreamUnavailable` / `ModelUnavailable` exceptions. Fast 5xx failover with a budget of max(2, number of keys). Honours `retry-after`. Tool calls are normalised and extra fields are preserved.
- **Router:** `ModelRouter.choose(phase, need, client, exclude)` walks `Config.candidates(phase)` and returns the first model with live headroom, otherwise the one with the shortest wait.
- **Parallel execution:** in a single model turn, tool calls are sorted into read-only calls (read, search, list, git status/diff) and side-effect calls. Read-only calls run concurrently in a thread pool (default 6 workers, one per key). Writes and commands run in order. Results go back to the model in the original call order. A `delegate(task, files)` tool starts helper sub-agents with read-only or scoped-write tools. Each helper picks its own key through the router, and helpers with overlapping write sets are run one after another. The renderer shows one live line per active worker.
- **Engine:** re-picks the model at each step. On `UpstreamUnavailable`, it excludes that model and moves to the next one. Debug-repair loop bounded to 3 attempts. Run-level token ceiling.
- **Persistence:** SQLite at `~/.forge/forge.db` (runs, events, findings, checkpoints).
- **Tests (pytest):** 6-key loading and cap, org bucket sharing, rotation fairness, 429 cooldown, learned 404s, router spill 120b to 20b, 413 shrink, parallel read batch order and concurrency, sub-agent write-conflict ordering, approval policy, blocklist, patch application, checkpoints/undo, history compression pairing, debug loop with a stub model, spinner verbs.
- **Live check:** `scripts/live_check.py` sends a tiny request through each key for each model and prints a pass/fail grid. No key is ever printed.
- **AGENTS.md** is updated: the FleetClient/KeyManager/router rules stay, Gemini references are removed, and new rules are added for org buckets and parallel batches.
