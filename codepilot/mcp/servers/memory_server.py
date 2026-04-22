"""SQLite-backed memory MCP server for structured agent memory.

Agents call these tools explicitly to store and retrieve typed memories
that persist across sessions.  Separate from ADK's session memory —
this is the *semantic* / *structured* memory layer.

Memory types
------------
conversation  — summaries of completed development sessions
project       — project-specific notes, architecture decisions, patterns
error_fix     — (error → fix) pairs the Debug agent learns over time
preference    — user preferences observed during sessions

DB location: ~/.codepilot/memory.db

Usage by agents
---------------
- PlannerAgent: ``get_recent_conversations`` + ``get_project_context``
  before planning, to avoid re-doing completed work.
- FinalizerAgent: ``store_memory(type="conversation", ...)`` after each
  session to record what was built.
- DebugAgent: ``search_memories(type="error_fix")`` before diagnosing,
  ``store_memory(type="error_fix")`` after finding a non-obvious fix.
"""

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

mcp = FastMCP("memory")

_DB_PATH = Path.home() / ".codepilot" / "memory.db"
_VALID_TYPES = frozenset({"conversation", "project", "error_fix", "preference"})


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open() -> sqlite3.Connection:
    """Open (and lazily initialise) the memory database."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            type     TEXT    NOT NULL,
            key      TEXT    NOT NULL,
            content  TEXT    NOT NULL,
            project  TEXT,
            tags     TEXT    NOT NULL DEFAULT '[]',
            created  REAL    NOT NULL,
            updated  REAL    NOT NULL,
            UNIQUE(key)
        );
        CREATE INDEX IF NOT EXISTS idx_m_type    ON memories(type);
        CREATE INDEX IF NOT EXISTS idx_m_project ON memories(project);
        CREATE INDEX IF NOT EXISTS idx_m_updated ON memories(updated DESC);
    """)
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "key":     row["key"],
        "type":    row["type"],
        "content": row["content"],
        "project": row["project"],
        "tags":    json.loads(row["tags"] or "[]"),
        "updated": row["updated"],
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def store_memory(
    type: str,
    key: str,
    content: str,
    project: Optional[str] = None,
    tags: Optional[list] = None,
) -> dict:
    """Store a typed memory entry, overwriting any existing entry with the same key.

    Args:
        type:    Memory type — "conversation", "project", "error_fix", or "preference".
        key:     Unique identifier, e.g. "session_2024-01-15" or "flask_cors_fix".
        content: Human-readable summary or note to persist.
        project: Optional project directory path to scope the memory.
        tags:    Optional keyword tags for improved search recall.

    Returns:
        {"ok": true, "key": ..., "action": "created"|"updated"}
    """
    if type not in _VALID_TYPES:
        return {
            "ok": False,
            "error": f"Invalid type '{type}'. Must be one of: "
                     f"{', '.join(sorted(_VALID_TYPES))}",
        }
    if not key.strip():
        return {"ok": False, "error": "key must not be empty"}

    now = time.time()
    tags_json = json.dumps(tags or [])

    with _open() as conn:
        existing = conn.execute(
            "SELECT created FROM memories WHERE key = ?", (key,)
        ).fetchone()
        created = existing["created"] if existing else now
        conn.execute(
            """
            INSERT OR REPLACE INTO memories
                (type, key, content, project, tags, created, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (type, key, content, project, tags_json, created, now),
        )

    return {
        "ok": True,
        "key": key,
        "type": type,
        "action": "updated" if existing else "created",
    }


@mcp.tool()
def search_memories(
    query: str,
    type: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """Search memories by keyword, optionally filtered by type and/or project.

    Matches against key, content, and tags.  Returns results ordered by
    most recently updated.

    Args:
        query:   Keywords to search for (space-separated).
        type:    Optional filter — "conversation", "project", "error_fix",
                 or "preference".
        project: Optional project directory path to filter results.
        limit:   Maximum number of results to return (default 10).

    Returns:
        {"ok": true, "data": [...], "count": N}
    """
    words = set(re.findall(r"\w+", query.lower()))
    if not words:
        return {"ok": True, "data": [], "count": 0}

    if type and type not in _VALID_TYPES:
        return {
            "ok": False,
            "error": f"Invalid type '{type}'. Must be one of: "
                     f"{', '.join(sorted(_VALID_TYPES))}",
        }

    sql = "SELECT * FROM memories WHERE 1=1"
    params: list = []
    if type:
        sql += " AND type = ?"
        params.append(type)
    if project:
        sql += " AND (project = ? OR project IS NULL)"
        params.append(project)
    sql += " ORDER BY updated DESC LIMIT 200"

    with _open() as conn:
        rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        haystack = f"{row['key']} {row['content']} {row['tags']}".lower()
        if words & set(re.findall(r"\w+", haystack)):
            results.append(_row_to_dict(row))
        if len(results) >= limit:
            break

    return {"ok": True, "data": results, "count": len(results)}


@mcp.tool()
def get_recent_conversations(
    project: Optional[str] = None,
    limit: int = 5,
) -> dict:
    """Retrieve the most recent conversation summaries for a project.

    Call this at the start of a new session to understand what was
    previously built or discussed.  Prevents re-doing finished work.

    Args:
        project: Optional project directory path to filter.
        limit:   Number of conversations to return (default 5).

    Returns:
        {"ok": true, "data": [...], "count": N}
    """
    sql = "SELECT * FROM memories WHERE type = 'conversation'"
    params: list = []
    if project:
        sql += " AND (project = ? OR project IS NULL)"
        params.append(project)
    sql += " ORDER BY updated DESC LIMIT ?"
    params.append(limit)

    with _open() as conn:
        rows = conn.execute(sql, params).fetchall()

    return {
        "ok": True,
        "data": [_row_to_dict(r) for r in rows],
        "count": len(rows),
    }


@mcp.tool()
def get_project_context(project: str) -> dict:
    """Retrieve all memories scoped to a specific project directory.

    Returns project notes, error-fix patterns, and conversation history
    grouped by type.  Use this to quickly understand the full context
    of an existing project before planning or making changes.

    Args:
        project: Absolute path to the project directory.

    Returns:
        {"ok": true, "data": {"conversation": [...], "project": [...], ...}, "total": N}
    """
    with _open() as conn:
        rows = conn.execute(
            """
            SELECT * FROM memories
            WHERE project = ?
            ORDER BY type, updated DESC
            """,
            (project,),
        ).fetchall()

    by_type: dict[str, list] = {}
    for row in rows:
        t = row["type"]
        by_type.setdefault(t, []).append(_row_to_dict(row))

    total = sum(len(v) for v in by_type.values())
    return {
        "ok": True,
        "data": by_type,
        "total": total,
        "project": project,
    }


@mcp.tool()
def delete_memory(key: str) -> dict:
    """Delete a specific memory entry by its key.

    Args:
        key: The unique key of the memory entry to delete.

    Returns:
        {"ok": true, "deleted": key} or {"ok": false, "error": ...}
    """
    with _open() as conn:
        deleted = conn.execute(
            "DELETE FROM memories WHERE key = ?", (key,)
        ).rowcount

    if deleted == 0:
        return {"ok": False, "error": f"No memory found with key '{key}'"}
    return {"ok": True, "deleted": key}


if __name__ == "__main__":
    mcp.run()
