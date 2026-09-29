# Forge

A terminal-first autonomous coding agent you run inside VS Code, like Claude Code.
You open a project, type `forge`, describe a task, and Forge reads your real files, runs
commands, edits code, runs the tests and reports what changed.

Everything runs on your machine. Nothing is hosted. The only outbound calls go to Groq
with your own API keys.

## Install

```bash
pipx install -e .          # or: uv tool install -e .  |  pip install -e .
forge init                 # writes .env.example here, ~/.forge/config.toml, and .vscode tasks
```

Then paste your Groq keys into `.env.example` in the project folder — one file,
up to five keys, no other setup (real environment variables still take priority):

```dotenv
GROQ_API_KEY=gsk_…          # required
GROQ_API_KEY_2=gsk_…        # optional, up to 5 keys for rotation
```

You can also put up to five keys in `~/.forge/config.toml`:

```toml
[groq]
api_keys = ["gsk_…", "gsk_…"]
```

`GROQ_API_KEY` and `GROQ_API_KEY_2` … `GROQ_API_KEY_5` work everywhere and win over both files.
Keys are never logged, and are redacted from all output. Since `.env.example`
holds real keys here, make sure it is listed in your project's `.gitignore`.
Forge's own sandbox already blocks the agent from reading or writing `.env*` files.

## Use it in VS Code

Open the project, then either:

- Open the integrated terminal and run `forge`
- Or run a task: **Terminal → Run Task →**
  - `Forge: Session` — interactive agent session
  - `Forge: Ask` — one-shot task from a prompt box
  - `Forge: Inspect project` — audit, no changes
  - `Forge: Undo last run`
  - `Forge: Serve workspace API`

`.vscode/forge-keybindings.json` holds suggested shortcuts (`ctrl+alt+f`, `ctrl+alt+a`) —
paste them into your own `keybindings.json` if you want them.

## Commands

| Command | What it does |
| --- | --- |
| `forge` | Interactive session in the current folder |
| `forge ask "fix the login bug"` | One task, non-interactive |
| `forge inspect` | Audit the project and list findings |
| `forge undo [run]` | Restore the files a run changed |
| `forge runs` / `forge findings <run>` | History |
| `forge keys` | Key health and configured models |
| `forge serve` | Local HTTP/SSE API on 127.0.0.1:8787 |

In a session: `/inspect`, `/undo`, `/keys`, `/exit`.

## How a run works

```text
request -> detect project -> map repo -> select context -> plan
   -> execute tools -> observe -> patch -> run tests
   -> if failing: debug & repair (bounded) -> review -> report
```

Each phase runs as a role with its own prompt and model: Explorer, Planner, Coder,
Debugger, Tester, Reviewer (Inspector in `forge inspect`). Explorer, Reviewer and
Inspector get read-only tools, so they cannot change your code.

## Safety

- Every file write, command and commit asks first. Answer `y`, `n`, or `a` (always allow
  that kind for the rest of the session). `--auto` skips the prompts.
- Destructive commands (`rm -rf /`, fork bombs, `git push --force`, `curl | sh`, …) are
  refused even with approval.
- Paths outside the project root and `.env*` files are off limits.
- Every touched file is snapshotted first — `forge undo` puts it all back.

## Models

| Role | Model | Used for |
| --- | --- | --- |
| fast | `openai/gpt-oss-20b` | exploring, test parsing, review summary |
| deep | `openai/gpt-6…`→`openai/gpt-oss-120b` | planning, patching, debugging |
| vision | `qwen/qwen3.8-27b` | wired for the screenshot phase (not used yet) |

Context is budgeted against each model's real window: the most relevant files go in whole,
the rest as symbol outlines, and long transcripts are compressed with tool-call pairs kept
intact so nothing overflows.

Forge knows the stacks it is aimed at — React front ends, Go with Fiber, PostgreSQL — and
applies their conventions by default (hooks-only React, thin Fiber handlers with wrapped
errors and `context.Context`, parameterised SQL and forward-only migrations).

## Local API (`forge serve`)

| Endpoint | Purpose |
| --- | --- |
| `GET /api/projects/current` `\|` `/tree` `\|` `/file` | project + file reads |
| `POST /api/agent/run` → `GET /api/agent/runs/{token}/events` | start a run, stream events (SSE) |
| `GET /api/agent/runs` `\|` `/{id}` , `POST /{id}/undo` | history and undo |
| `POST /api/terminal/run` | run a command in the project session |
| `GET /api/git/status` `\|` `/diff`, `POST /api/git/commit` | git |
| `GET /api/usage/keys` | key health |

Bound to localhost only. This is the seam the web workspace will plug into later.

## Layout

```text
forge/
  cli.py              CLI commands and the interactive session
  config.py           ~/.forge config, models, budgets, redaction
  events.py store.py  event bus + SQLite (runs, events, findings, checkpoints)
  models/             key manager, Groq client, model router
  repo/               stack detection, search, symbols
  context/            token-budgeted context and history compression
  tools/              filesystem, search, terminal, git, tests
  sandbox/            approval policy, checkpoints, terminal session
  orchestrator/       prompts + the run state machine
  server/             FastAPI + SSE
  ui/                 terminal theme and renderer
tests/                pytest suite (run: pytest -q)
```

## Not built yet

Web workspace UI, screenshot/visual verification, live web research, Docker sandbox,
Postgres/Redis persistence.

## Token limits (Groq free tier)
Every request is clamped below the model's tokens-per-minute limit (8,000 on the free tier for gpt-oss), so you no longer get "Request too large" (413) errors. Forge still reads your whole project: big files are read in pages, one after another, until the agent has everything it needs. If Groq still refuses a request, Forge shrinks it and retries on its own.
After upgrading your Groq plan, raise the limit in ~/.forge/config.toml:

    [limits]
    tpm = { "openai/gpt-oss-120b" = 250000, "openai/gpt-oss-20b" = 250000 }
