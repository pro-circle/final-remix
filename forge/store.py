"""SQLite persistence for runs, events, findings and checkpoints."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from forge.config import DB_PATH, ensure_home
from forge.events import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  project_root TEXT NOT NULL,
  request TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at REAL NOT NULL,
  finished_at REAL,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  summary TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  file TEXT,
  line INTEGER,
  detail TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  path TEXT NOT NULL,
  existed INTEGER NOT NULL,
  backup_path TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints(run_id);
"""


class Store:
    def __init__(self, path: Path | None = None) -> None:
        ensure_home()
        self.path = path or DB_PATH
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # runs -----------------------------------------------------------------
    def create_run(self, run_id: str, project_root: str, request: str) -> None:
        self.conn.execute(
            "INSERT INTO runs (id, project_root, request, status, started_at) VALUES (?,?,?,?,?)",
            (run_id, project_root, request, "running", time.time()),
        )
        self.conn.commit()

    def finish_run(
        self,
        run_id: str,
        status: str,
        summary: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        self.conn.execute(
            "UPDATE runs SET status=?, finished_at=?, summary=?, tokens_in=?, tokens_out=? WHERE id=?",
            (status, time.time(), summary, tokens_in, tokens_out, run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def last_run_id(self, project_root: str) -> str | None:
        row = self.conn.execute(
            "SELECT id FROM runs WHERE project_root=? ORDER BY started_at DESC LIMIT 1",
            (project_root,),
        ).fetchone()
        return row["id"] if row else None

    # events ---------------------------------------------------------------
    def record_event(self, event: Event) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO events (id, run_id, ts, kind, data) VALUES (?,?,?,?,?)",
            (event.id, event.run_id, event.ts, event.kind, json.dumps(event.data, default=str)),
        )
        self.conn.commit()

    def events_for(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE run_id=? ORDER BY ts", (run_id,)
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item["data"])
            out.append(item)
        return out

    # findings -------------------------------------------------------------
    def add_finding(self, finding: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO findings (id, run_id, severity, title, file, line, detail, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                finding["id"],
                finding.get("run_id"),
                finding.get("severity", "medium"),
                finding.get("title", ""),
                finding.get("file"),
                finding.get("line"),
                finding.get("detail", ""),
                time.time(),
            ),
        )
        self.conn.commit()

    def findings_for(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM findings WHERE run_id=? ORDER BY severity", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # checkpoints ----------------------------------------------------------
    def add_checkpoint(self, entry: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO checkpoints (id, run_id, path, existed, backup_path, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                entry["id"],
                entry["run_id"],
                entry["path"],
                1 if entry["existed"] else 0,
                entry.get("backup_path"),
                time.time(),
            ),
        )
        self.conn.commit()

    def checkpoints_for(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM checkpoints WHERE run_id=? ORDER BY created_at", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
