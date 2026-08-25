"""Small durable archive for user-visible conversation workspaces.

LangGraph still owns the live execution state. This store owns the material a
person expects to find after a refresh: the conversation title, messages,
layers, result panels, and the structured analytical context used for safe
follow-up questions.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    context_json TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self) -> str:
        session_id = str(uuid.uuid4())
        timestamp = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, "New analysis", timestamp, timestamp, "idle", "{}", "{}"),
            )
        return session_id

    def ids(self) -> set[str]:
        with self._lock, self._connect() as connection:
            return {row["id"] for row in connection.execute("SELECT id FROM sessions")}

    def list(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id, title, created_at, updated_at, status, snapshot_json "
                "FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        items = []
        for row in rows:
            snapshot = json.loads(row["snapshot_json"] or "{}")
            messages = snapshot.get("messages") or []
            preview = next(
                (item.get("content", "") for item in reversed(messages) if item.get("content")),
                "No messages yet",
            )
            items.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "status": row["status"],
                    "preview": preview[:140],
                    "message_count": len(messages),
                }
            )
        return items

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": row["status"],
            "snapshot": json.loads(row["snapshot_json"] or "{}"),
        }

    def context(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT context_json FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None or not row["context_json"]:
            return None
        value = json.loads(row["context_json"])
        return value or None

    def save_snapshot(self, session_id: str, snapshot: dict[str, Any]) -> bool:
        timestamp = _now()
        status = str(snapshot.get("status") or "complete")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET snapshot_json = ?, status = ?, updated_at = ? WHERE id = ?",
                (json.dumps(snapshot, ensure_ascii=False), status, timestamp, session_id),
            )
        return cursor.rowcount > 0

    def save_context(self, session_id: str, context: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET context_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(context, ensure_ascii=False), _now(), session_id),
            )

    def touch_message(self, session_id: str, text: str) -> None:
        title = " ".join(text.strip().split())[:64] or "New analysis"
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET title = CASE WHEN title = 'New analysis' THEN ? ELSE title END,
                    status = 'analyzing', updated_at = ?
                WHERE id = ?
                """,
                (title, _now(), session_id),
            )

    def rename(self, session_id: str, title: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title.strip()[:100], _now(), session_id),
            )
        return cursor.rowcount > 0

    def mark_status(self, session_id: str, status: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), session_id),
            )

    def delete(self, session_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0
