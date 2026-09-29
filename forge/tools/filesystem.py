"""Filesystem tools: read, list, write and patch files inside the project."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from forge.events import PATCH
from forge.sandbox.policies import Denied, resolve_inside
from forge.tools.context import ToolContext
from forge.tools.registry import Tool

MAX_READ_CHARS = 120_000


def _read_file(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_inside(ctx.root, str(args["path"]))
    if not path.exists():
        return {"ok": False, "error": f"file not found: {args['path']}"}
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    start = max(1, int(args.get("start_line") or 1))
    end = int(args.get("end_line") or 0) or len(lines)
    cap = min(MAX_READ_CHARS, int(getattr(ctx, "read_chunk_chars", MAX_READ_CHARS)))
    out: list[str] = []
    used = 0
    line_no = start
    while line_no <= min(end, len(lines)):
        row = f"{line_no}: {lines[line_no - 1]}"
        if out and used + len(row) + 1 > cap:
            break
        out.append(row[:cap])
        used += len(row) + 1
        line_no += 1
    result: dict[str, Any] = {
        "ok": True,
        "path": str(path.relative_to(ctx.root)),
        "lines": len(lines),
        "shown": f"{start}-{line_no - 1}" if out else "none",
        "content": "\n".join(out),
    }
    if line_no <= min(end, len(lines)):
        # Paged, not truncated: the model keeps reading until it has the whole file.
        result["truncated"] = True
        result["next_start_line"] = line_no
        result["note"] = (
            f"File continues. Call read_file with start_line={line_no} to read the next part; "
            "keep going until you have everything you need."
        )
    else:
        result["truncated"] = False
    return result


def _list_dir(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    target = resolve_inside(ctx.root, str(args.get("path") or "."))
    if not target.is_dir():
        return {"ok": False, "error": f"not a directory: {args.get('path')}"}
    entries = []
    for item in sorted(target.iterdir()):
        if item.name in {".git", "node_modules", "__pycache__", ".venv"}:
            continue
        entries.append(
            {
                "name": item.name + ("/" if item.is_dir() else ""),
                "size": item.stat().st_size if item.is_file() else None,
            }
        )
    return {"ok": True, "path": str(target.relative_to(ctx.root)), "entries": entries[:400]}


def _diff(old: str, new: str, rel: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=3,
        )
    )


def _apply_write(ctx: ToolContext, path: Path, new_text: str, label: str) -> dict[str, Any]:
    rel = str(path.relative_to(ctx.root))
    old_text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    if old_text == new_text:
        return {"ok": True, "path": rel, "note": "no change"}
    diff = _diff(old_text, new_text, rel)
    if not ctx.policy.request("write_file", f"{label} {rel}\n{diff[:4000]}"):
        return {"ok": False, "error": "user declined this change"}
    ctx.checkpoints.snapshot(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    ctx.changed_files.add(rel)
    ctx.bus.emit(PATCH, run_id=ctx.run_id, path=rel, diff=diff[:20000])
    return {"ok": True, "path": rel, "diff": diff[:6000]}


def _write_file(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_inside(ctx.root, str(args["path"]))
    return _apply_write(ctx, path, str(args["content"]), "write")


def _edit_file(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_inside(ctx.root, str(args["path"]))
    if not path.exists():
        return {"ok": False, "error": f"file not found: {args['path']}"}
    old = str(args["old_text"])
    new = str(args["new_text"])
    text = path.read_text(encoding="utf-8", errors="ignore")
    occurrences = text.count(old)
    if occurrences == 0:
        return {
            "ok": False,
            "error": "old_text not found; read the file again and copy the exact text",
        }
    if occurrences > 1 and not args.get("replace_all"):
        return {
            "ok": False,
            "error": f"old_text appears {occurrences} times; include more surrounding lines or set replace_all",
        }
    updated = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
    return _apply_write(ctx, path, updated, "edit")


def _delete_file(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_inside(ctx.root, str(args["path"]))
    if not path.exists():
        return {"ok": False, "error": "file not found"}
    rel = str(path.relative_to(ctx.root))
    if not ctx.policy.request("delete_file", f"delete {rel}"):
        return {"ok": False, "error": "user declined the deletion"}
    ctx.checkpoints.snapshot(path)
    path.unlink()
    ctx.changed_files.add(rel)
    return {"ok": True, "deleted": rel}


def _report_finding(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    import uuid

    finding = {
        "id": uuid.uuid4().hex,
        "run_id": ctx.run_id,
        "severity": str(args.get("severity", "medium")),
        "title": str(args.get("title", "")),
        "file": args.get("file"),
        "line": args.get("line"),
        "detail": str(args.get("detail", "")),
    }
    ctx.findings.append(finding)
    from forge.events import FINDING

    ctx.bus.emit(FINDING, run_id=ctx.run_id, **{k: v for k, v in finding.items() if k != "run_id"})
    return {"ok": True, "recorded": finding["id"]}


TOOLS = [
    Tool(
        name="read_file",
        description="Read a file from the project with line numbers.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path"],
        },
        run=_read_file,
    ),
    Tool(
        name="list_dir",
        description="List files and folders in a project directory.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
        run=_list_dir,
    ),
    Tool(
        name="write_file",
        description="Create or fully overwrite a file. Prefer edit_file for small changes.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        run=_write_file,
        mutates=True,
    ),
    Tool(
        name="edit_file",
        description="Replace an exact snippet in a file. old_text must match the file byte for byte.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        run=_edit_file,
        mutates=True,
    ),
    Tool(
        name="delete_file",
        description="Delete a file from the project.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        run=_delete_file,
        mutates=True,
    ),
    Tool(
        name="report_finding",
        description="Record a problem found during inspection (severity: critical|high|medium|low).",
        parameters={
            "type": "object",
            "properties": {
                "severity": {"type": "string"},
                "title": {"type": "string"},
                "file": {"type": "string"},
                "line": {"type": "integer"},
                "detail": {"type": "string"},
            },
            "required": ["severity", "title"],
        },
        run=_report_finding,
    ),
]
