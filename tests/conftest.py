import os
import tempfile
from pathlib import Path

import pytest

# Keep every test off the developer's real ~/.forge.
_TMP_HOME = tempfile.mkdtemp(prefix="forge-test-home-")
os.environ["FORGE_HOME"] = _TMP_HOME

from forge.config import Config  # noqa: E402
from forge.events import EventBus  # noqa: E402
from forge.repo.detector import detect  # noqa: E402
from forge.sandbox.checkpoints import CheckpointManager  # noqa: E402
from forge.sandbox.policies import ApprovalPolicy  # noqa: E402
from forge.sandbox.terminal import TerminalSession  # noqa: E402
from forge.store import Store  # noqa: E402
from forge.tools.context import ToolContext  # noqa: E402


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(
        "export function greet(name: string) {\n  return `hi ${name}`;\n}\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        '{"name":"demo","scripts":{"test":"echo ok"},"dependencies":{"react":"19.0.0"}}',
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "forge.db")


@pytest.fixture()
def ctx(project: Path, store: Store) -> ToolContext:
    cfg = Config(api_keys=["test-key"])
    store.create_run("run1", str(project), "test run")
    return ToolContext(
        root=project,
        cfg=cfg,
        bus=EventBus(),
        run_id="run1",
        profile=detect(project),
        policy=ApprovalPolicy(auto_approve=True),
        terminal=TerminalSession(root=project),
        checkpoints=CheckpointManager(store, "run1", project),
        changed_files=set(),
        findings=[],
    )
