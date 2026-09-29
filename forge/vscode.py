"""VS Code workspace integration: tasks that run Forge in the integrated terminal."""

from __future__ import annotations

import json
from pathlib import Path

TASKS = {
    "version": "2.0.0",
    "tasks": [
        {
            "label": "Forge: Session",
            "type": "shell",
            "command": "forge",
            "options": {"cwd": "${workspaceFolder}"},
            "presentation": {"reveal": "always", "panel": "dedicated", "focus": True},
            "problemMatcher": [],
        },
        {
            "label": "Forge: Ask",
            "type": "shell",
            "command": "forge ask ${input:forgeRequest}",
            "options": {"cwd": "${workspaceFolder}"},
            "presentation": {"reveal": "always", "panel": "dedicated", "focus": True},
            "problemMatcher": [],
        },
        {
            "label": "Forge: Inspect project",
            "type": "shell",
            "command": "forge inspect",
            "options": {"cwd": "${workspaceFolder}"},
            "presentation": {"reveal": "always", "panel": "dedicated"},
            "problemMatcher": [],
        },
        {
            "label": "Forge: Undo last run",
            "type": "shell",
            "command": "forge undo",
            "options": {"cwd": "${workspaceFolder}"},
            "presentation": {"reveal": "always", "panel": "dedicated"},
            "problemMatcher": [],
        },
        {
            "label": "Forge: Serve workspace API",
            "type": "shell",
            "command": "forge serve",
            "isBackground": True,
            "options": {"cwd": "${workspaceFolder}"},
            "presentation": {"reveal": "always", "panel": "dedicated"},
            "problemMatcher": [],
        },
    ],
    "inputs": [
        {
            "id": "forgeRequest",
            "type": "promptString",
            "description": "What should Forge do?",
        }
    ],
}

KEYBINDINGS_HINT = [
    {"key": "ctrl+alt+f", "command": "workbench.action.tasks.runTask", "args": "Forge: Session"},
    {"key": "ctrl+alt+a", "command": "workbench.action.tasks.runTask", "args": "Forge: Ask"},
]

SETTINGS = {
    "terminal.integrated.scrollback": 20000,
    "diffEditor.ignoreTrimWhitespace": False,
}


def write_vscode_assets(root: Path) -> list[str]:
    folder = root / ".vscode"
    folder.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    tasks_path = folder / "tasks.json"
    tasks_path.write_text(json.dumps(TASKS, indent=2) + "\n", encoding="utf-8")
    written.append(str(tasks_path))

    settings_path = folder / "settings.json"
    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(SETTINGS)
    settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    written.append(str(settings_path))

    hint_path = folder / "forge-keybindings.json"
    hint_path.write_text(json.dumps(KEYBINDINGS_HINT, indent=2) + "\n", encoding="utf-8")
    written.append(str(hint_path))
    return written
