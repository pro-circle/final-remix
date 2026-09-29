"""Search tools: content search, symbol outline and the repository map."""

from __future__ import annotations

from typing import Any

from forge.repo.detector import imports_of, repo_map
from forge.repo.search import search as repo_search
from forge.repo.symbols import symbols_in
from forge.sandbox.policies import resolve_inside
from forge.tools.context import ToolContext
from forge.tools.registry import Tool


def _search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    results = repo_search(ctx.root, str(args["pattern"]), int(args.get("max_results") or 60))
    return {"ok": True, "count": len(results), "results": results}


def _outline(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve_inside(ctx.root, str(args["path"]))
    if not path.exists():
        return {"ok": False, "error": "file not found"}
    return {
        "ok": True,
        "path": str(path.relative_to(ctx.root)),
        "symbols": symbols_in(path),
        "imports": imports_of(path),
    }


def _repo_map(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "profile": ctx.profile.summary(),
        "tree": repo_map(ctx.root, int(args.get("max_entries") or 400)),
    }


TOOLS = [
    Tool(
        name="search_code",
        description="Search the project for a regular expression. Fast way to locate code.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["pattern"],
        },
        run=_search,
    ),
    Tool(
        name="outline_file",
        description="List the symbols and imports of a file without reading all of it.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        run=_outline,
    ),
    Tool(
        name="repo_map",
        description="Get the detected stack plus a file tree of the project.",
        parameters={
            "type": "object",
            "properties": {"max_entries": {"type": "integer"}},
        },
        run=_repo_map,
    ),
]
