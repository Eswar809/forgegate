"""SQLite-backed session transcript store.

Shape follows OpenAI Agents SDK ``SQLiteSession``: a sessions table plus an
ordered messages table, so an agent can resume without replaying its whole
prompt. Kept intentionally small and dependency-free.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS agent_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_session
    ON agent_messages(session_id, id);
"""


def _now() -> str:
    from .core import now_iso
    return now_iso()


class SessionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        """Commit on success and always close; sqlite3's own context manager
        commits but leaves the handle open, which locks the file on Windows."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def ensure(self, session_id: str, metadata: dict[str, Any] | None = None) -> None:
        stamp = _now()
        with self._open() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO agent_sessions"
                " (session_id, created_at, updated_at, metadata)"
                " VALUES (?, ?, ?, ?)",
                (session_id, stamp, stamp, json.dumps(metadata or {})),
            )
            conn.commit()

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        self.ensure(session_id)
        stamp = _now()
        with self._open() as conn:
            cursor = conn.execute(
                "INSERT INTO agent_messages"
                " (session_id, role, content, created_at, metadata)"
                " VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, stamp, json.dumps(metadata or {})),
            )
            conn.execute(
                "UPDATE agent_sessions SET updated_at = ? WHERE session_id = ?",
                (stamp, session_id),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def messages(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT * FROM ("
                "  SELECT id, role, content, created_at, metadata"
                "  FROM agent_messages WHERE session_id = ?"
                "  ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC",
                (session_id, max(1, min(int(limit), 500))),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except json.JSONDecodeError:
                item["metadata"] = {}
            out.append(item)
        return out

    def sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_sessions ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [dict(row) for row in rows]
