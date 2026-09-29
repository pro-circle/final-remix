from forge.context.builder import build_repo_context, compress_history, fit_messages, messages_tokens
from forge.sandbox.checkpoints import CheckpointManager
from forge.tools.filesystem import _edit_file, _write_file


def test_repo_context_respects_budget(project):
    small = build_repo_context(project, "greet", token_budget=2000)
    assert "src/app.ts" in small
    assert messages_tokens([{"role": "user", "content": small}]) <= 4000


def test_history_compression_keeps_system_and_recent():
    messages = [{"role": "system", "content": "rules"}]
    for i in range(60):
        messages.append({"role": "user", "content": f"message {i} " + "x" * 2000})
    compressed = compress_history(messages, token_budget=6000)
    assert compressed[0]["role"] == "system"
    assert "compressed" in compressed[1]["content"]
    assert compressed[-1]["content"].startswith("message 59")


def test_compression_never_leaves_orphan_tool_result():
    messages = [{"role": "system", "content": "rules"}]
    for i in range(40):
        messages.append({"role": "assistant", "content": "x" * 3000})
        messages.append({"role": "tool", "tool_call_id": str(i), "content": "y" * 3000})
    compressed = compress_history(messages, token_budget=5000)
    assert compressed[2]["role"] != "tool"


def test_fit_messages_enforces_hard_limit():
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "z" * 200_000},
    ]
    fitted = fit_messages(messages, token_budget=3000)
    assert messages_tokens(fitted) <= 3000


def test_undo_restores_edited_and_removes_created(ctx, store):
    _edit_file(ctx, {"path": "src/app.ts", "old_text": "hi ${name}", "new_text": "bye ${name}"})
    _write_file(ctx, {"path": "src/new.ts", "content": "export const x = 1;\n"})
    assert (ctx.root / "src" / "new.ts").exists()

    restored = CheckpointManager.restore(store, "run1", ctx.root)

    assert "hi ${name}" in (ctx.root / "src" / "app.ts").read_text()
    assert not (ctx.root / "src" / "new.ts").exists()
    assert len(restored) == 2
