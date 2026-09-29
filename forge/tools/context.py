"""Everything a tool is allowed to reach."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.config import Config
from forge.events import EventBus
from forge.repo.detector import ProjectProfile
from forge.sandbox.checkpoints import CheckpointManager
from forge.sandbox.policies import ApprovalPolicy
from forge.sandbox.terminal import TerminalSession


@dataclass
class ToolContext:
    root: Path
    cfg: Config
    bus: EventBus
    run_id: str
    profile: ProjectProfile
    policy: ApprovalPolicy
    terminal: TerminalSession
    checkpoints: CheckpointManager
    changed_files: set[str]
    findings: list[dict[str, Any]]
    # Max characters one read_file call returns; longer files are paged, never dropped.
    read_chunk_chars: int = 120_000
