"""Local HTTP/SSE API over the same orchestrator and event bus as the CLI.

Bound to localhost by `forge serve`. This is the seam the web workspace will use;
nothing here is intended to be exposed to the internet.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from forge.config import load_config, redact
from forge.events import EventBus
from forge.models.client import FleetClient

from forge.orchestrator.engine import Orchestrator
from forge.repo.detector import detect, repo_map
from forge.sandbox.checkpoints import CheckpointManager
from forge.sandbox.policies import ApprovalPolicy, Denied, resolve_inside
from forge.sandbox.terminal import TerminalSession
from forge.store import Store


class RunRequest(BaseModel):
    request: str
    mode: str = "task"
    auto_approve: bool = True


class CommandRequest(BaseModel):
    command: str
    timeout: int = 300


class CommitRequest(BaseModel):
    message: str


def create_app(root: Path) -> FastAPI:
    root = root.resolve()
    app = FastAPI(title="Forge", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:8080", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    store = Store()
    terminal = TerminalSession(root=root)
    live_runs: dict[str, "queue.Queue[Any]"] = {}

    @app.get("/api/projects/current")
    def current_project() -> dict[str, Any]:
        profile = detect(root)
        return {
            "root": str(root),
            "languages": profile.languages,
            "frameworks": profile.frameworks,
            "databases": profile.databases,
            "test_commands": profile.test_commands,
            "build_commands": profile.build_commands,
            "file_count": profile.file_count,
        }

    @app.get("/api/projects/tree")
    def tree(max_entries: int = 600) -> dict[str, Any]:
        return {"tree": repo_map(root, max_entries).splitlines()}

    @app.get("/api/projects/file")
    def file(path: str) -> dict[str, Any]:
        target = resolve_inside(root, path)
        if not target.is_file():
            raise HTTPException(404, "file not found")
        return {
            "path": path,
            "content": target.read_text(encoding="utf-8", errors="ignore")[:400_000],
        }

    @app.post("/api/agent/run")
    def start_run(body: RunRequest) -> dict[str, Any]:
        cfg = load_config()
        if not cfg.all_keys:
            raise HTTPException(400, "No Groq or Gemini API keys configured")

        bus = EventBus()
        events: queue.Queue[Any] = queue.Queue()
        bus.subscribe(lambda event: events.put(event.to_dict()))
        orchestrator = Orchestrator(
            root=root,
            cfg=cfg,
            bus=bus,
            store=store,
            policy=ApprovalPolicy(auto_approve=body.auto_approve),
            client=FleetClient(cfg),
        )

        holder: dict[str, str] = {}

        def worker() -> None:
            try:
                result = orchestrator.run(body.request, mode=body.mode)
                events.put({"kind": "run.complete", "data": {"run_id": result.run_id}})
            except Exception as exc:  # tell the client instead of silently ending the stream
                events.put({"kind": "run.error", "data": {"message": redact(str(exc), cfg)}})
            finally:
                events.put(None)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        token = f"live-{id(events):x}"
        live_runs[token] = events
        holder["token"] = token
        return {"stream": f"/api/agent/runs/{token}/events", "token": token}

    @app.get("/api/agent/runs/{token}/events")
    async def stream(token: str) -> StreamingResponse:
        events = live_runs.get(token)
        if events is None:
            raise HTTPException(404, "unknown run token")

        async def generator():
            loop = asyncio.get_event_loop()
            while True:
                item = await loop.run_in_executor(None, events.get)
                if item is None:
                    yield "event: end\ndata: {}\n\n"
                    live_runs.pop(token, None)
                    return
                yield f"data: {json.dumps(item, default=str)}\n\n"

        return StreamingResponse(generator(), media_type="text/event-stream")

    @app.get("/api/agent/runs")
    def list_runs(limit: int = 20) -> dict[str, Any]:
        return {"runs": store.recent_runs(limit)}

    @app.get("/api/agent/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "unknown run")
        return {
            "run": run,
            "events": store.events_for(run_id),
            "findings": store.findings_for(run_id),
        }

    @app.post("/api/agent/runs/{run_id}/undo")
    def undo(run_id: str) -> dict[str, Any]:
        return {"restored": CheckpointManager.restore(store, run_id, root)}

    @app.post("/api/terminal/run")
    def run_command(body: CommandRequest) -> dict[str, Any]:
        policy = ApprovalPolicy(auto_approve=True)
        try:
            policy.check_command(body.command)
        except Denied as exc:
            raise HTTPException(403, str(exc))
        result = terminal.run(body.command, timeout=body.timeout)
        return {
            "exit_code": result.exit_code,
            "output": result.as_text(),
            "cwd": result.cwd,
            "timed_out": result.timed_out,
        }

    @app.get("/api/git/status")
    def git_status() -> dict[str, Any]:
        return {"output": terminal.run("git status --short --branch", timeout=30).as_text()}

    @app.get("/api/git/diff")
    def git_diff(path: str = "") -> dict[str, Any]:
        suffix = f" -- {path}" if path else ""
        return {"output": terminal.run(f"git --no-pager diff{suffix}", timeout=60).as_text()}

    @app.post("/api/git/commit")
    def git_commit(body: CommitRequest) -> dict[str, Any]:
        message = body.message.replace('"', "'")
        terminal.run("git add -A", timeout=60)
        return {"output": terminal.run(f'git commit -m "{message}"', timeout=60).as_text()}

    @app.get("/api/usage/keys")
    def key_health() -> dict[str, Any]:
        cfg = load_config()
        return {"keys": KeyManager(cfg.api_keys).health(), "models": cfg.models}

    return app
