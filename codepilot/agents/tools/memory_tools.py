"""Local memory tools — direct SQLite access, replaces memory_server.py MCP.

Agents call these to store and retrieve structured memories across sessions.
Direct SQLite access (no subprocess) is faster and more reliable than MCP.

Memory types
------------
conversation  — session summaries, what was built
project       — project-specific notes, decisions, constraints
error_fix     — error pattern → fix mappings (most valuable for Debug agent)
preference    — user preferences
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from ...utils.constants import CONFIG_DIR
from ...utils.logger import get_logger

logger = get_logger(__name__)

_DB_PATH = CONFIG_DIR / "memory.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    type     TEXT NOT NULL,
    key      TEXT NOT NULL UNIQUE,
    content  TEXT NOT NULL,
    project  TEXT,
    tags     TEXT DEFAULT '[]',
    created  INTEGER NOT NULL,
    updated  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_type    ON memories(type);
CREATE INDEX IF NOT EXISTS idx_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_updated ON memories(updated);
"""

_VALID_TYPES = {"conversation", "project", "error_fix", "preference"}


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def store_memory(
    type: str,
    key: str,
    content: str,
    tool_context: ToolContext,
    project: str = "",
    tags: str = "",
) -> dict:
    """Store a structured memory for future sessions.

    Use this to persist important context: session summaries, error→fix
    patterns, project decisions, and user preferences.

    Args:
        type: Memory type — "conversation", "project", "error_fix", or "preference".
        key: Short unique identifier (e.g. "fix_import_error_flask").
        content: The memory text (be concise, 1-3 sentences).
        project: Project directory path (for scoping this memory to a project).
        tags: Comma-separated tags for retrieval (e.g. "python,flask,error").

    Returns:
        dict with ok and memory_id.
    """
    if type not in _VALID_TYPES:
        return {"ok": False, "error": f"Invalid type '{type}'. Use: {_VALID_TYPES}"}
    tag_list = json.dumps([t.strip() for t in tags.split(",") if t.strip()])
    now = int(time.time())
    try:
        with _conn() as conn:
            cursor = conn.execute(
                """INSERT INTO memories (type, key, content, project, tags, created, updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                     content=excluded.content, type=excluded.type,
                     project=excluded.project, tags=excluded.tags, updated=excluded.updated""",
                (type, key, content, project or None, tag_list, now, now),
            )
            return {"ok": True, "memory_id": cursor.lastrowid, "key": key}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def search_memories(
    query: str,
    tool_context: ToolContext,
    type: str = "",
    project: str = "",
    limit: int = 5,
) -> dict:
    """Search memories by keyword. Call this before debugging to find known fixes.

    Args:
        query: Keywords to search in content and key fields.
        type: Optional filter — "conversation", "project", "error_fix", "preference".
        project: Optional project path to restrict search.
        limit: Max results (default 5).

    Returns:
        dict with ok and results (list of memory dicts).
    """
    clauses = ["(content LIKE ? OR key LIKE ?)"]
    params: list = [f"%{query}%", f"%{query}%"]
    if type and type in _VALID_TYPES:
        clauses.append("type = ?")
        params.append(type)
    if project:
        clauses.append("(project = ? OR project IS NULL)")
        params.append(project)
    params.append(limit)
    sql = f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY updated DESC LIMIT ?"
    try:
        with _conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return {"ok": True, "results": rows, "count": len(rows)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_recent_conversations(
    tool_context: ToolContext,
    project: str = "",
    limit: int = 5,
) -> dict:
    """Return recent conversation summaries for a project.

    Call this at session start to understand what was previously built.

    Args:
        project: Project directory to filter by (empty = all projects).
        limit: Max conversations to return (default 5).

    Returns:
        dict with ok and conversations (list of {key, content, updated}).
    """
    clauses = ["type = 'conversation'"]
    params: list = []
    if project:
        clauses.append("(project = ? OR project IS NULL)")
        params.append(project)
    params.append(limit)
    sql = f"SELECT key, content, project, updated FROM memories WHERE {' AND '.join(clauses)} ORDER BY updated DESC LIMIT ?"
    try:
        with _conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return {"ok": True, "conversations": rows, "count": len(rows)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_project_context(project: str, tool_context: ToolContext) -> dict:
    """Return all stored memories for a specific project.

    Args:
        project: Project directory path.

    Returns:
        dict with ok and memories grouped by type.
    """
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE project = ? ORDER BY type, updated DESC",
                (project,),
            ).fetchall()
        grouped: dict = {}
        for row in rows:
            t = row["type"]
            grouped.setdefault(t, []).append(dict(row))
        return {"ok": True, "project": project, "memories": grouped}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_memory(key: str, tool_context: ToolContext) -> dict:
    """Delete a memory by its key.

    Args:
        key: Memory key to delete.

    Returns:
        dict with ok and deleted (bool).
    """
    try:
        with _conn() as conn:
            r = conn.execute("DELETE FROM memories WHERE key = ?", (key,))
            return {"ok": True, "deleted": r.rowcount > 0}
    except Exception as e:
        return {"ok": False, "error": str(e)}
