"""Kernel task store: generic queued→running→done|failed state machine.

Domain-free: it knows task ids, statuses and JSON results — nothing else.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

VALID_TRANSITIONS = {
    "queued": {"running", "failed"},
    "running": {"done", "failed"},
}


class TaskStore:
    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            result TEXT,
            error TEXT,
            created REAL NOT NULL,
            updated REAL NOT NULL)""")
        self._conn.commit()

    def create(self, task_id: str, kind: str, payload: dict | None = None) -> None:
        now = time.time()
        self._conn.execute(
            "INSERT INTO tasks (id, kind, status, payload, created, updated) VALUES (?,?,?,?,?,?)",
            (task_id, kind, "queued", json.dumps(payload or {}, ensure_ascii=False), now, now))
        self._conn.commit()

    def set_status(self, task_id: str, status: str, *,
                   result: dict | None = None, error: str | None = None) -> None:
        row = self._conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown task: {task_id}")
        current = row[0]
        if status not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid transition {current} -> {status}")
        self._conn.execute(
            "UPDATE tasks SET status = ?, result = ?, error = ?, updated = ? WHERE id = ?",
            (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
             error, time.time(), task_id))
        self._conn.commit()

    def get(self, task_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, kind, status, payload, result, error, created, updated "
            "FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return {"id": row[0], "kind": row[1], "status": row[2],
                "payload": json.loads(row[3]),
                "result": json.loads(row[4]) if row[4] else None,
                "error": row[5], "created": row[6], "updated": row[7]}

    def recent_tasks(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kind, status, created FROM tasks ORDER BY created DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"id": r[0], "kind": r[1], "status": r[2], "created": r[3]} for r in rows]
