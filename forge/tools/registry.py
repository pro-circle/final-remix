"""Tool registry: the only way the model touches the machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from forge.tools.context import ToolContext


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[ToolContext, dict[str, Any]], dict[str, Any]]
    mutates: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]


def build_registry() -> ToolRegistry:
    from forge.tools import filesystem, git, search, terminal, tests

    registry = ToolRegistry()
    for module in (filesystem, search, terminal, git, tests):
        for tool in module.TOOLS:
            registry.add(tool)
    return registry
