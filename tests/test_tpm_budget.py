"""Prompts stay under Groq's tokens-per-minute ceiling, and big files are still fully readable."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from forge.config import Config
from forge.context.builder import build_repo_context, estimate_tokens, fit_messages, messages_tokens
from forge.models.client import Completion, RequestTooLarge, Usage
from forge.models.router import ModelRouter
from forge.tools.filesystem import _read_file


def test_budget_clamped_below_free_tier_tpm():
    cfg = Config(api_keys=["x"])
    for phase in ("explore", "code", "review"):
        assert ModelRouter(cfg).context_budget(phase) < 8000


def test_tpm_override_raises_budget():
    cfg = Config(api_keys=["x"], tpm_limits={"openai/gpt-oss-120b": 250_000})
    assert ModelRouter(cfg).context_budget("code") > 100_000


def test_repo_context_respects_small_budget(tmp_path: Path):
    for i in range(30):
        (tmp_path / f"mod{i}.py").write_text("def f():\n    return 1\n" * 400)
    ctx = build_repo_context(tmp_path, "mod", 2500)
    assert estimate_tokens(ctx) < 2600


def test_fit_messages_keeps_prompt_under_budget():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "x" * 100_000}]
    assert messages_tokens(fit_messages(msgs, 5000)) <= 5000


def test_read_file_pages_through_whole_file(tmp_path: Path):
    lines = [f"line {i} " + "y" * 60 for i in range(1, 2001)]
    (tmp_path / "big.txt").write_text("\n".join(lines))
    ctx = SimpleNamespace(root=tmp_path, read_chunk_chars=5000)
    seen, start = [], 1
    while True:
        res = _read_file(ctx, {"path": "big.txt", "start_line": start})
        assert len(res["content"]) <= 5000
        seen.extend(res["content"].splitlines())
        if not res["truncated"]:
            break
        start = res["next_start_line"]
    assert len(seen) == 2000 and seen[-1].startswith("2000: line 2000")


def test_413_shrinks_and_retries(project: Path, store):
    from forge.events import EventBus
    from forge.orchestrator.engine import Orchestrator
    from forge.repo.detector import detect
    from forge.sandbox.policies import ApprovalPolicy

    class Client:
        def __init__(self):
            self.sizes, self.usage, self.cfg = [], Usage(), Config(api_keys=["s"])

        def check_budget(self):
            pass

        def chat(self, *, model, messages, tools=None, **_):
            size = messages_tokens(messages)
            self.sizes.append(size)
            if size > 2500:
                raise RequestTooLarge("too big", 3000, size)
            return Completion("ok", [], "stop", model, {})

    client = Client()
    orch = Orchestrator(root=project, cfg=Config(api_keys=["s"]), bus=EventBus(), store=store,
                        policy=ApprovalPolicy(auto_approve=True), client=client, profile=detect(project))
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "z" * 40_000}]
    _, out = orch._chat(phase="plan", model="openai/gpt-oss-120b", messages=msgs)
    assert out.content == "ok" and client.sizes[-1] <= 2500
