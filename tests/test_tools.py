from pathlib import Path

import pytest

from forge.sandbox.policies import ApprovalPolicy, Denied, resolve_inside
from forge.tools.filesystem import _edit_file, _read_file, _write_file
from forge.tools.registry import build_registry
from forge.tools.terminal import _run_command


def test_registry_exposes_core_tools():
    names = build_registry().names()
    for expected in ("read_file", "edit_file", "run_command", "run_tests", "git_diff"):
        assert expected in names


def test_read_file_numbers_lines(ctx):
    out = _read_file(ctx, {"path": "src/app.ts"})
    assert out["ok"] and out["content"].startswith("1: export function greet")


def test_edit_file_requires_exact_match(ctx):
    out = _edit_file(ctx, {"path": "src/app.ts", "old_text": "nope", "new_text": "x"})
    assert not out["ok"] and "not found" in out["error"]


def test_edit_file_applies_and_records_change(ctx):
    out = _edit_file(
        ctx, {"path": "src/app.ts", "old_text": "hi ${name}", "new_text": "hello ${name}"}
    )
    assert out["ok"]
    assert "src/app.ts" in ctx.changed_files
    assert "hello" in (ctx.root / "src" / "app.ts").read_text()


def test_ambiguous_edit_is_refused(ctx):
    target = ctx.root / "src" / "dupe.ts"
    target.write_text("const a = 1;\nconst a = 1;\n", encoding="utf-8")
    out = _edit_file(ctx, {"path": "src/dupe.ts", "old_text": "const a = 1;", "new_text": "const b = 2;"})
    assert not out["ok"] and "appears 2 times" in out["error"]


def test_declined_write_changes_nothing(ctx):
    ctx.policy = ApprovalPolicy(auto_approve=False, asker=lambda kind, detail: False)
    out = _write_file(ctx, {"path": "src/app.ts", "content": "wiped"})
    assert not out["ok"]
    assert "wiped" not in (ctx.root / "src" / "app.ts").read_text()


def test_path_escape_is_blocked(ctx):
    with pytest.raises(Denied):
        resolve_inside(ctx.root, "../../etc/passwd")


def test_env_file_is_protected(ctx):
    with pytest.raises(Denied):
        resolve_inside(ctx.root, ".env")


def test_blocked_command_is_refused(ctx):
    out = _run_command(ctx, {"command": "rm -rf / "})
    assert not out["ok"] and "safety policy" in out["error"]


def test_command_runs_and_keeps_cwd(ctx):
    _run_command(ctx, {"command": "cd src"})
    out = _run_command(ctx, {"command": "pwd"})
    assert out["ok"] and out["cwd"].endswith("src")
