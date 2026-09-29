"""The orchestrator: the run loop that makes Forge an agent rather than a chat box.

State machine:
    explore -> plan -> code -> verify -> (debug -> verify)* -> review -> report
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.config import Config, redact
from forge.context.builder import (
    build_repo_context,
    fit_messages,
    messages_tokens,
)
from forge.events import (
    APPROVAL,
    EventBus,
    NOTICE,
    PHASE,
    PLAN,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    THOUGHT,
    TOOL_CALL,
    TOOL_RESULT,
    USAGE,
)
from forge.models.client import (
    BudgetExceeded,
    Completion,
    FleetClient,
    GroqClient,
    ModelError,
    ModelUnavailable,
    RequestTooLarge,
)

from forge.models.router import ModelRouter
from forge.orchestrator.prompts import ROLE_PROMPTS, project_briefing
from forge.repo.detector import ProjectProfile, detect, repo_map
from forge.sandbox.checkpoints import CheckpointManager
from forge.sandbox.policies import ApprovalPolicy, Denied
from forge.sandbox.terminal import TerminalSession
from forge.store import Store
from forge.tools.context import ToolContext
from forge.tools.registry import ToolRegistry, build_registry


@dataclass
class RunResult:
    run_id: str
    status: str
    summary: str
    plan: str = ""
    changed_files: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    tests_passed: bool | None = None
    risk: str = ""
    tokens: dict[str, int] = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self,
        root: Path,
        cfg: Config,
        bus: EventBus,
        store: Store,
        policy: ApprovalPolicy,
        client: GroqClient | FleetClient | None = None,
        profile: ProjectProfile | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.root = root.resolve()
        self.cfg = cfg
        self.bus = bus
        self.store = store
        self.policy = policy
        self.client = client or FleetClient(cfg)
        self.router = ModelRouter(cfg, self.client)
        self.profile = profile or detect(self.root)
        self.registry = registry or build_registry()
        self.terminal = TerminalSession(root=self.root)


    # ------------------------------------------------------------------ public
    def run(self, request: str, mode: str = "task") -> RunResult:
        run_id = uuid.uuid4().hex[:12]
        self.store.create_run(run_id, str(self.root), request)
        unsubscribe = self.bus.subscribe(self.store.record_event)
        self.bus.emit(
            RUN_STARTED,
            run_id=run_id,
            request=request,
            mode=mode,
            project=str(self.root),
            profile=self.profile.summary(),
        )

        ctx = ToolContext(
            root=self.root,
            cfg=self.cfg,
            bus=self.bus,
            run_id=run_id,
            profile=self.profile,
            policy=self.policy,
            terminal=self.terminal,
            checkpoints=CheckpointManager(self.store, run_id, self.root),
            changed_files=set(),
            findings=[],
        )

        result = RunResult(run_id=run_id, status="running", summary="")
        try:
            if mode == "inspect":
                result = self._inspect(ctx, request, result)
            else:
                result = self._task(ctx, request, result)
            result.status = result.status if result.status != "running" else "done"
        except (BudgetExceeded, ModelError, Denied) as exc:
            result.status = "failed"
            result.summary = redact(str(exc), self.cfg)
            self.bus.emit(RUN_FAILED, run_id=run_id, error=result.summary)
        except KeyboardInterrupt:
            result.status = "stopped"
            result.summary = "Stopped by user."
            self.bus.emit(RUN_FAILED, run_id=run_id, error=result.summary)
        finally:
            result.changed_files = sorted(ctx.changed_files)
            result.findings = ctx.findings
            for finding in ctx.findings:
                self.store.add_finding(finding)
            result.tokens = {
                "in": self.client.usage.tokens_in,
                "out": self.client.usage.tokens_out,
                "requests": self.client.usage.requests,
            }
            self.bus.emit(USAGE, run_id=run_id, **result.tokens)
            if result.status == "running":
                result.status = "done"
            self.store.finish_run(
                run_id,
                result.status,
                result.summary,
                self.client.usage.tokens_in,
                self.client.usage.tokens_out,
            )
            self.bus.emit(
                RUN_FINISHED,
                run_id=run_id,
                status=result.status,
                summary=result.summary,
                changed_files=result.changed_files,
                findings=len(result.findings),
                tests_passed=result.tests_passed,
                risk=result.risk,
            )
            unsubscribe()
        return result

    # ----------------------------------------------------------------- phases
    def _task(self, ctx: ToolContext, request: str, result: RunResult) -> RunResult:
        briefing = self._briefing(request, "code")

        exploration = self._phase_loop(
            ctx,
            phase="explore",
            briefing=briefing,
            instruction=f"Request: {request}\n\nFind and report the code that matters.",
            max_steps=8,
        )

        plan = self._single_shot(
            phase="plan",
            briefing=briefing,
            instruction=(
                f"Request: {request}\n\nExplorer report:\n{exploration}\n\n"
                "Write the execution plan."
            ),
        )
        result.plan = plan
        self.bus.emit(PLAN, run_id=ctx.run_id, plan=plan)

        outcome = self._phase_loop(
            ctx,
            phase="code",
            briefing=briefing,
            instruction=(
                f"Request: {request}\n\nContext from exploration:\n{exploration}\n\n"
                f"Plan to execute:\n{plan}\n\nExecute it now."
            ),
            max_steps=self.cfg.budget.max_tool_calls,
        )

        verified, verify_note = self._verify(ctx)
        result.tests_passed = verified
        attempts = 0
        while verified is False and attempts < self.cfg.budget.max_repair_attempts:
            attempts += 1
            self.bus.emit(
                NOTICE, run_id=ctx.run_id, message=f"Verification failed; repair attempt {attempts}"
            )
            outcome = self._phase_loop(
                ctx,
                phase="debug",
                briefing=briefing,
                instruction=(
                    f"Request: {request}\n\nPlan:\n{plan}\n\n"
                    f"Verification output:\n{verify_note}\n\nRepair it."
                ),
                max_steps=30,
            )
            verified, verify_note = self._verify(ctx)
            result.tests_passed = verified

        if ctx.changed_files:
            review = self._phase_loop(
                ctx,
                phase="review",
                briefing=self._briefing(request, "review"),
                instruction=(
                    f"Request: {request}\n\nFiles changed in this run: "
                    f"{', '.join(sorted(ctx.changed_files))}\n\nReview the change."
                ),
                max_steps=16,
            )
            result.risk = _extract_risk(review)
            result.summary = _clean(outcome) + ("\n\nReview: " + _clean(review) if review else "")
        else:
            result.summary = _clean(outcome)

        if verified is False:
            result.status = "needs_attention"
        return result

    def _inspect(self, ctx: ToolContext, request: str, result: RunResult) -> RunResult:
        briefing = self._briefing(request or "full project audit", "inspect")
        report = self._phase_loop(
            ctx,
            phase="inspect",
            briefing=briefing,
            instruction=(
                (request or "Audit the whole project.")
                + "\n\nRecord every real defect with report_finding."
            ),
            max_steps=40,
        )
        result.summary = _clean(report)
        return result

    # ------------------------------------------------------------------ model
    def _briefing(self, request: str, phase: str) -> str:
        budget = self.router.context_budget_for(self._pick(phase))

        # Small windows (free-tier TPM) get a short tree and a lean preload; the agent
        # then reads everything else itself, page by page, through read_file.
        tree_entries = 300 if budget > 40_000 else max(40, budget // 60)
        context = build_repo_context(self.root, request, int(budget * 0.3))
        return project_briefing(self.profile.summary(), repo_map(self.root, tree_entries), context)

    def _pick(self, phase: str, messages: list[dict[str, Any]] | None = None) -> str:
        """Model for this phase, chosen from the phase's chain by live key capacity."""
        need = messages_tokens(messages) + 1_000 if messages else 0
        return self.router.choose(phase, need=need, client=self.client)

    def _chat(
        self,
        *,
        phase: str,
        messages: list[dict[str, Any]],
        tools=None,
        model: str | None = None,
    ):
        """Pick a model with free capacity, fit the prompt to it; on a 413 shrink and retry."""
        chosen = model or self._pick(phase, messages)
        budget = self.router.context_budget_for(chosen)
        for _ in range(6):
            fitted = fit_messages(messages, budget)
            try:
                if tools is None:
                    return fitted, self.client.chat(model=chosen, messages=fitted)
                return fitted, self.client.chat(model=chosen, messages=fitted, tools=tools)
            except ModelUnavailable as exc:
                # No key can call this model any more (learned live): move down the chain.
                if model is not None:
                    raise ModelError(str(exc)) from exc
                previous, chosen = chosen, self._pick(phase, messages)
                if chosen == previous:
                    raise ModelError(str(exc)) from exc
                budget = self.router.context_budget_for(chosen)
                self.bus.emit(NOTICE, text=f"{previous} unavailable on every key; switching to {chosen}.")
            except RequestTooLarge as exc:
                limit = exc.limit or budget
                # Our estimate was low for this text: learn the real ceiling and shrink.
                self.cfg.tpm_limits[chosen] = min(self.cfg.tpm_limit(chosen), limit)
                budget = max(1_500, int(min(budget, limit - 1_800) * 0.7))
                self.bus.emit(NOTICE, text=f"Prompt too large for {chosen}; shrinking to ~{budget} tokens and retrying.")
        raise ModelError(
            f"{chosen} rejects even a {budget}-token prompt. Raise [limits] in ~/.forge/config.toml "
            "or switch [models] to one with a higher tokens-per-minute limit."
        )

    def _single_shot(self, *, phase: str, briefing: str, instruction: str) -> str:
        messages = [
            {"role": "system", "content": ROLE_PROMPTS[phase] + "\n\n" + briefing},
            {"role": "user", "content": instruction},
        ]
        model = self._pick(phase, messages)
        self.bus.emit(PHASE, phase=phase, model=model)
        _, completion = self._chat(phase=phase, model=model, messages=messages)
        return completion.content.strip()

    def _phase_loop(
        self,
        ctx: ToolContext,
        *,
        phase: str,
        briefing: str,
        instruction: str,
        max_steps: int,
    ) -> str:
        model = self._pick(phase)
        budget = self.router.context_budget_for(model)
        self.bus.emit(PHASE, phase=phase, model=model, run_id=ctx.run_id)

        # One file page may use ~35% of a request; long files are read in several calls.
        ctx.read_chunk_chars = max(3_000, int(budget * 0.35 * 3.6))

        tools = self.registry.schemas()
        if phase in ("explore", "inspect", "review"):
            read_only = {
                "read_file",
                "list_dir",
                "search_code",
                "outline_file",
                "repo_map",
                "git_status",
                "git_diff",
                "git_log",
                "report_finding",
            }
            tools = [t for t in tools if t["function"]["name"] in read_only]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": ROLE_PROMPTS[phase] + "\n\n" + briefing},
            {"role": "user", "content": instruction},
        ]
        final_text = ""
        seen: set[str] = set()  # identical read-only calls since the last change

        for step in range(max_steps):
            # Re-pick every step: a saturated model hands the next turn to one with headroom.
            step_model = self._pick(phase, messages)
            if step_model != model:
                model = step_model
                self.bus.emit(PHASE, phase=phase, model=model, run_id=ctx.run_id)
                ctx.read_chunk_chars = max(
                    3_000, int(self.router.context_budget_for(model) * 0.35 * 3.6)
                )
            messages, completion = self._chat(
                phase=phase, model=model, messages=messages, tools=tools
            )

            if completion.content.strip():
                self.bus.emit(
                    THOUGHT, run_id=ctx.run_id, phase=phase, text=completion.content.strip()[:4000]
                )
                final_text = completion.content.strip()

            if not completion.tool_calls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": completion.content or "",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["arguments"]),
                            },
                        }
                        for call in completion.tool_calls
                    ],
                }
            )

            for call in completion.tool_calls:
                tool = self.registry.get(call["name"])
                signature = call["name"] + json.dumps(call["arguments"], sort_keys=True, default=str)
                if tool is not None and not tool.mutates and call["name"] not in ("run_tests", "run_command") and signature in seen:
                    # Saves tokens (and rate limit) when a model re-reads what it already has.
                    payload = {"ok": True, "note": "Same call already made; its result is above. Move on."}
                else:
                    payload = self._execute_tool(ctx, phase, call)
                    if tool is None or tool.mutates or call["name"] in ("run_tests", "run_command"):
                        seen.clear()
                    else:
                        seen.add(signature)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(payload, default=str)[:60_000],
                    }
                )

            if final_text.startswith(("DONE:", "BLOCKED:")):
                break

        return final_text

    def _execute_tool(
        self, ctx: ToolContext, phase: str, call: dict[str, Any]
    ) -> dict[str, Any]:
        name = call["name"]
        args = call["arguments"] if isinstance(call["arguments"], dict) else {}
        self.bus.emit(TOOL_CALL, run_id=ctx.run_id, phase=phase, tool=name, args=args)
        tool = self.registry.get(name)
        if tool is None:
            payload = {"ok": False, "error": f"unknown tool: {name}"}
        else:
            try:
                payload = tool.run(ctx, args)
            except Denied as exc:
                payload = {"ok": False, "error": str(exc)}
            except Exception as exc:  # surface the failure to the model, don't crash the run
                payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.bus.emit(
            TOOL_RESULT,
            run_id=ctx.run_id,
            phase=phase,
            tool=name,
            ok=bool(payload.get("ok")),
            detail=redact(str(payload.get("error") or payload.get("summary") or "")[:500], self.cfg),
        )
        return payload

    def _verify(self, ctx: ToolContext) -> tuple[bool | None, str]:
        """Run the project's own verification commands after a change."""
        if not ctx.changed_files:
            return None, "no files changed"
        commands = (self.profile.test_commands or [])[:1] + (self.profile.build_commands or [])[:1]
        if not commands:
            self.bus.emit(
                NOTICE,
                run_id=ctx.run_id,
                message="No test or build command detected; skipping automated verification.",
            )
            return None, "no verification command available"
        notes: list[str] = []
        ok = True
        from forge.tools.tests import _run_tests

        for command in commands:
            payload = _run_tests(ctx, {"command": command})
            notes.append(str(payload.get("output") or payload.get("error") or ""))
            if payload.get("error") == "user declined to run tests":
                return None, "verification skipped by user"
            ok = ok and bool(payload.get("ok"))
        return ok, "\n\n".join(notes)[:12000]


def _clean(text: str) -> str:
    for prefix in ("DONE:", "BLOCKED:"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text.strip()


def _extract_risk(review: str) -> str:
    lowered = review.lower()
    for level in ("high", "medium", "low"):
        if f"risk: {level}" in lowered or f"risk {level}" in lowered:
            return level.capitalize()
    return ""
