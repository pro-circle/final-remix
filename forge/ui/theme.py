"""Forge terminal theme: near-black, bone white, amber accent."""

from __future__ import annotations

from rich.theme import Theme

FORGE_THEME = Theme(
    {
        "forge.brand": "bold #ff9d2e",
        "forge.accent": "#ff9d2e",
        "forge.dim": "#8a8478",
        "forge.text": "#ece6db",
        "forge.ok": "#8fbf6f",
        "forge.fail": "#e5533d",
        "forge.warn": "#e8b04b",
        "forge.agent": "bold #ff9d2e",
        "forge.tool": "#c9a227",
        "forge.rule": "#4a443c",
    }
)

SEVERITY_STYLE = {
    "critical": "forge.fail",
    "high": "forge.fail",
    "medium": "forge.warn",
    "low": "forge.dim",
}
