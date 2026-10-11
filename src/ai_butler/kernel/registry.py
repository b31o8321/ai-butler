"""Kernel registry: sqlite event log (policy engine foundation, S6+)."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class Registry:
    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}')""")
        self._conn.commit()

    def append(self, kind: str, payload: dict | None = None) -> None:
        self._conn.execute("INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)",
                           (time.time(), kind, json.dumps(payload or {}, ensure_ascii=False)))
        self._conn.commit()

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT ts, kind, payload FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "kind": r[1], "payload": json.loads(r[2])} for r in rows]

    def close(self) -> None:
        self._conn.close()
