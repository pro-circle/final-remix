"""Per-run file checkpoints so any change can be undone."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from forge.config import CHECKPOINT_DIR, ensure_home
from forge.store import Store


class CheckpointManager:
    def __init__(self, store: Store, run_id: str, root: Path) -> None:
        ensure_home()
        self.store = store
        self.run_id = run_id
        self.root = root
        self.dir = CHECKPOINT_DIR / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()

    def snapshot(self, path: Path) -> None:
        rel = str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)
        if rel in self._seen:
            return
        self._seen.add(rel)
        entry_id = uuid.uuid4().hex
        backup_path: str | None = None
        if path.exists():
            target = self.dir / f"{entry_id}__{path.name}"
            shutil.copy2(path, target)
            backup_path = str(target)
        self.store.add_checkpoint(
            {
                "id": entry_id,
                "run_id": self.run_id,
                "path": rel,
                "existed": path.exists(),
                "backup_path": backup_path,
            }
        )

    @staticmethod
    def restore(store: Store, run_id: str, root: Path) -> list[str]:
        restored: list[str] = []
        for entry in reversed(store.checkpoints_for(run_id)):
            target = root / entry["path"]
            if entry["existed"] and entry["backup_path"]:
                backup = Path(entry["backup_path"])
                if backup.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
                    restored.append(entry["path"])
            elif not entry["existed"] and target.exists():
                target.unlink()
                restored.append(f"{entry['path']} (removed)")
        return restored
