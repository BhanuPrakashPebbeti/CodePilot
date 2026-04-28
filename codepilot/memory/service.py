"""SQLite-backed ADK memory service for persistent, project-scoped session context.

Implements ADK's ``BaseMemoryService`` so the ADK Runner can automatically
persist session events to disk.

Session isolation:  The ``user_id`` passed to ADK is the project name
(e.g. "kanban-board"), NOT a shared "user" constant.  This ensures that
``search_memory`` only returns events from the same project — no cross-project
leakage through the ADK memory layer.

DB location: ``~/.codepilot/session_memory.db``
"""

import asyncio
import re
import sqlite3
from pathlib import Path
from typing import Optional

from google.adk.memory import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import Session
from google.genai import types as genai_types

from ..utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_DB = Path.home() / ".codepilot" / "session_memory.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name    TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    session_id  TEXT    NOT NULL,
    event_id    TEXT    NOT NULL,
    author      TEXT,
    content_json TEXT   NOT NULL,
    timestamp   REAL    NOT NULL,
    UNIQUE(event_id)
);
CREATE INDEX IF NOT EXISTS idx_se_user
    ON session_events(app_name, user_id);
CREATE INDEX IF NOT EXISTS idx_se_session
    ON session_events(app_name, user_id, session_id);
CREATE INDEX IF NOT EXISTS idx_se_time
    ON session_events(timestamp DESC);
"""


class SqliteMemoryService(BaseMemoryService):
    """ADK-compatible memory service backed by SQLite.

    Stores session events so they survive process restarts.  Implements
    keyword-based search (same approach as ADK's ``InMemoryMemoryService``
    but with persistence).

    Usage with ADK Runner::

        memory = SqliteMemoryService()
        runner = Runner(
            agent=...,
            session_service=session_service,
            memory_service=memory,   # ← pass here
        )

    After each completed run the Runner calls
    ``add_session_to_memory(session)`` automatically.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        path = Path(db_path) if db_path else _DEFAULT_DB
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(path)
        self._init_schema()
        logger.debug("SqliteMemoryService initialised at %s", self._db_path)

    # ── Schema ────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── BaseMemoryService interface ───────────────────────────────────────

    async def add_session_to_memory(self, session: Session) -> None:
        """Persist all content-bearing events from *session* to SQLite."""
        events = [
            e for e in (session.events or [])
            if e.content and e.content.parts
        ]
        if not events:
            return

        rows: list[tuple] = []
        for event in events:
            try:
                content_json = event.content.model_dump_json()
            except Exception:
                continue
            rows.append((
                session.app_name,
                session.user_id,
                session.id,
                event.id,
                event.author,
                content_json,
                event.timestamp,
            ))

        def _write() -> None:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO session_events
                        (app_name, user_id, session_id, event_id,
                         author, content_json, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

        await asyncio.to_thread(_write)
        logger.debug(
            "Stored %d events from session %s into memory", len(rows), session.id
        )

    async def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
    ) -> SearchMemoryResponse:
        """Keyword search across stored session events.

        Returns events whose text content shares at least one word with
        *query*, ordered by recency (most recent first).
        """
        query_words = set(re.findall(r"\w+", query.lower()))
        response = SearchMemoryResponse()
        if not query_words:
            return response

        def _read() -> list:
            with self._connect() as conn:
                return conn.execute(
                    """
                    SELECT author, content_json, timestamp
                    FROM session_events
                    WHERE app_name = ? AND user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 500
                    """,
                    (app_name, user_id),
                ).fetchall()

        rows = await asyncio.to_thread(_read)

        for row in rows:
            try:
                content = genai_types.Content.model_validate_json(
                    row["content_json"]
                )
            except Exception:
                continue

            text = " ".join(
                p.text for p in content.parts if p.text
            ).lower()
            if not text:
                continue

            event_words = set(re.findall(r"\w+", text))
            if query_words & event_words:
                response.memories.append(
                    MemoryEntry(
                        content=content,
                        author=row["author"] or "unknown",
                        timestamp=str(row["timestamp"]),
                    )
                )

        return response
