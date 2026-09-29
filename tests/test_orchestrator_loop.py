"""The debug-repair loop, driven by a stubbed model instead of Groq."""

from __future__ import annotations

import json
from pathlib import Path

from forge.config import Config
from forge.events import EventBus
from forge.models.client import Completion, Usage
from forge.orchestrator.engine import Orchestrator
from forge.repo.detector import detect
from forge.sandbox.policies import ApprovalPolicy
from forge.store import Store


class StubClient:
    """Scripted model: explore -> plan -> edit -> done, then a repair turn."""

    def __init__(self, script: list[Completion]) -> None:
        self.script = script
        self.calls: list[dict] = []
        self.usage = Usage()
        self.cfg = Config(api_keys=["stub"])

    def check_budget(self) -> None:
        return None

    def chat(self, *, model, messages, tools=None, temperature=0.2, max_attempts=6):
        self.calls.append({"model": model, "messages": messages})
        self.usage.requests += 1
        self.usage.tokens_in += 100
        self.usage.tokens_out += 20
        if self.script:
            return self.script.pop(0)
        return Completion(content="DONE: nothing left", tool_calls=[], finish_reason="stop", model=model, raw={})


def text(body: str) -> Completion:
    return Completion(content=body, tool_calls=[], finish_reason="stop", model="stub", raw={})


def call(name: str, args: dict) -> Completion:
    return Completion(
        content="",
        tool_calls=[{"id": "c1", "name": name, "arguments": args}],
        finish_reason="tool_calls",
        model="stub",
        raw={},
    )


def _orchestrator(project: Path, store: Store, client: StubClient) -> Orchestrator:
    return Orchestrator(
        root=project,
        cfg=Config(api_keys=["stub"]),
        bus=EventBus(),
        store=store,
        policy=ApprovalPolicy(auto_approve=True),
        client=client,  # type: ignore[arg-type]
        profile=detect(project),
    )


def test_task_run_edits_verifies_and_reports(project: Path, store: Store):
    script = [
        text("src/app.ts holds greet()"),          # explore
        text("1. change the greeting\n2. run tests"),  # plan
        call("edit_file", {"path": "src/app.ts", "old_text": "hi ${name}", "new_text": "hey ${name}"}),
        text("DONE: greeting updated"),            # coder final
        text("Risk: Low - one string changed"),    # reviewer
    ]
    client = StubClient(script)
    result = _orchestrator(project, store, client).run("change the greeting")

    assert result.status == "done"
    assert result.changed_files == ["src/app.ts"]
    assert "hey ${name}" in (project / "src" / "app.ts").read_text()
    assert result.tests_passed is True  # package.json test script is `echo ok`
    assert result.risk == "Low"
    assert result.plan.startswith("1.")


def test_failing_verification_triggers_debug_phase(project: Path, store: Store):
    # Make the project's test command fail the first time.
    (project / "package.json").write_text(
        json.dumps({"name": "demo", "scripts": {"test": "node -e \"process.exit(1)\""}}),
        encoding="utf-8",
    )
    script = [
        text("explored"),
        text("1. edit\n2. verify"),
        call("edit_file", {"path": "src/app.ts", "old_text": "hi ${name}", "new_text": "yo ${name}"}),
        text("DONE: edited"),
        text("DONE: repaired"),  # debugger turn
        text("DONE: repaired"),
        text("DONE: repaired"),
        text("Risk: Medium - tests still failing"),
    ]
    client = StubClient(script)
    result = _orchestrator(project, store, client).run("change the greeting")

    phases = [c["messages"][0]["content"] for c in client.calls]
    assert any("role is DEBUGGER" in p for p in phases)
    assert result.tests_passed is False
    assert result.status == "needs_attention"


def test_findings_are_recorded_in_inspect_mode(project: Path, store: Store):
    script = [
        call(
            "report_finding",
            {
                "severity": "high",
                "title": "Unvalidated input",
                "file": "src/app.ts",
                "line": 1,
                "detail": "greet() trusts its argument",
            },
        ),
        text("1 high"),
    ]
    result = _orchestrator(project, store, StubClient(script)).run("audit", mode="inspect")

    assert len(result.findings) == 1
    assert store.findings_for(result.run_id)[0]["severity"] == "high"
    assert result.changed_files == []
