"""Notion tools for CodePilot — structured project management (Jira-like).

Architecture (per project, created fresh each session)
-------------------------------------------------------
  Project Page  ← notion_project_id in state
  ├── 📋 Tasks Database       ← notion_tasks_db_id in state
  │     Columns: Name(title), Status, Priority, Task ID,
  │              Assigned Agent, Created Time, Updated Time
  ├── 📜 Activity Log DB      ← notion_logs_db_id in state
  │     Columns: Message(title), Event Type, Task ID, Agent, Timestamp
  └── 🧪 Test Artifacts DB    ← notion_artifacts_db_id in state
        Columns: Name(title), Type, Path, Task ID, Result, Timestamp

STRICT SCHEMA — these property names are canonical and must never change.
All database IDs are stored in ADK session state by PlannerAgent after
notion_setup_project(). Downstream tools read them from state via
tool_context.state automatically.

Tools
-----
  notion_setup_project()     — create project page + 3 child databases (validates schema)
  notion_create_task()       — add task to Tasks DB
  notion_update_task()       — update status + Updated Time, append comment
  notion_query_tasks()       — query tasks by status (use before acting)
  notion_add_comment()       — structured comment on a task page
  notion_log_event()         — add row to Activity Log DB
  notion_add_artifact()      — store screenshot/result in Test Artifacts DB
  notion_create_qa_page()    — create QA sub-page for test report
  notion_log_qa_step()       — log a browser/API test step
  notion_finalize_qa()       — finalize QA page with summary
  notion_finalize_project()  — create Summary sub-page, update project status

Failure contract
----------------
ALL tools return {"ok": True, "skipped": True, "reason": ...} on ANY failure.
They NEVER return {"ok": False}. The pipeline always continues without Notion.

Env vars
--------
  NOTION_TOKEN          — integration token (required)
  NOTION_PARENT_PAGE_ID — page to nest project root pages under (required)
"""

import os
import re
from datetime import datetime, timezone

from google.adk.tools.tool_context import ToolContext

from ...utils.logger import get_logger

logger = get_logger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
    re.IGNORECASE,
)

_STATUS_ICON = {
    "TODO": "📝", "IN_PROGRESS": "🔄", "DONE": "✅", "BLOCKED": "🚫",
    "ACTIVE": "🔄", "COMPLETED": "✅", "FAILED": "❌", "PARTIAL": "⚠️",
    "PLAN": "📋", "RUN": "▶️", "ERROR": "❌", "FIX": "🔧",
    "TEST": "🧪", "DEPLOY": "🚀", "COMMIT": "💾",
    "DEBUG": "🐛", "DECISION": "💡", "NOTE": "📝", "INFO": "ℹ️",
    "SCREENSHOT": "📸", "LOG": "📄", "TEST_RESULT": "🧪",
}

# Canonical property names — never reference Notion property names as literals elsewhere
_TASKS_REQUIRED_PROPS = frozenset({
    "Name", "Status", "Priority", "Task ID", "Assigned Agent",
    "Created Time", "Updated Time",
})
_LOGS_REQUIRED_PROPS = frozenset({
    "Message", "Event Type", "Task ID", "Agent", "Timestamp",
})
_ARTIFACTS_REQUIRED_PROPS = frozenset({
    "Name", "Type", "Path", "Task ID", "Result", "Timestamp",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_notion_client_cache: dict = {}


def _client():
    """Return a cached notion_client.Client, or None if not configured."""
    try:
        from notion_client import Client
    except ImportError:
        logger.debug("notion-client not installed — Notion tools disabled")
        return None
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        return None
    if token not in _notion_client_cache:
        _notion_client_cache[token] = Client(auth=token)
    return _notion_client_cache[token]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _icon(key: str) -> str:
    return _STATUS_ICON.get(key.upper(), "📌")


def _valid_id(pid: str) -> bool:
    return bool(pid and _UUID_RE.match(str(pid).strip()))


def _skipped(reason: str = "Notion not configured", **extra) -> dict:
    return {"ok": True, "skipped": True, "reason": reason, **extra}


def _state_id(tool_context: ToolContext, key: str) -> str:
    """Read a Notion ID from ADK state. Returns "" if not set or invalid."""
    if tool_context is None:
        return ""
    val = str(tool_context.state.get(key, "") or "")
    return val if _valid_id(val) else ""


def _find_task_page(client, tasks_db_id: str, task_id: str) -> str:
    """Return the Notion page ID for a task row, or "" if not found."""
    if not _valid_id(tasks_db_id):
        return ""
    try:
        result = client.databases.query(
            database_id=tasks_db_id,
            filter={"property": "Task ID", "rich_text": {"equals": task_id}},
            page_size=1,
        )
        pages = result.get("results", [])
        return pages[0]["id"] if pages else ""
    except Exception as exc:
        logger.debug("_find_task_page failed for task_id=%s: %s", task_id, exc)
        return ""


# Notion block builder helpers
def _para(text: str) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}


def _h2(text: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _h3(text: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _callout(text: str, emoji: str = "📋") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": text[:2000]}}],
                "icon": {"type": "emoji", "emoji": emoji},
            }}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
            }}


def _safe_text(rich_text_list: list) -> str:
    """Safely extract plain text from a Notion rich_text or title list."""
    try:
        if rich_text_list and isinstance(rich_text_list, list):
            first = rich_text_list[0]
            if isinstance(first, dict):
                return (first.get("text") or {}).get("content", "")
    except (IndexError, TypeError, KeyError):
        pass
    return ""


def _safe_append(client, block_id: str, children: list, tool_name: str) -> dict:
    try:
        client.blocks.children.append(block_id=block_id, children=children)
        return {"ok": True}
    except Exception as exc:
        logger.warning("%s: append failed — %s (skipping)", tool_name, exc)
        return _skipped(str(exc))


def _log_access_hint(exc, resource_id: str) -> None:
    msg = str(exc)
    if "shared with your integration" in msg or "Could not find" in msg:
        logger.warning(
            "Notion access denied for '%s…': %s\n"
            "  Fix: Open page in Notion → ··· → Connections → add your integration.",
            resource_id[:12], exc,
        )
    else:
        logger.warning("Notion error: %s", exc)


def _validate_db_schema(db_response: dict, required_props: frozenset, db_name: str) -> bool:
    """Check that all required properties exist in the newly created DB response.

    Notion sometimes silently drops properties if the definition is malformed.
    Logs a clear error if any required property is missing.
    Returns True only when ALL required properties are present.
    """
    props = db_response.get("properties") or {}
    missing = sorted(required_props - set(props.keys()))
    if missing:
        logger.error(
            "%s DB schema is incomplete — missing properties: %s. "
            "Notion may have rejected some property definitions.",
            db_name, missing,
        )
        return False
    logger.debug("%s DB schema OK — all %d properties present", db_name, len(required_props))
    return True


# ---------------------------------------------------------------------------
# 1. Setup — call ONCE per project
# ---------------------------------------------------------------------------

def notion_setup_project(
    project_name: str,
    workspace_path: str,
    goal: str,
    tool_context: ToolContext,
) -> dict:
    """Create the full Notion project structure for a session.

    Creates one root page + three child databases (Tasks, Activity Log,
    Test Artifacts) all under the NOTION_PARENT_PAGE_ID page.

    Called ONCE by PlannerAgent at the start of every project.
    The agent MUST store the returned IDs in state via set_state().

    Returns:
        {"ok": True, "project_id": "...", "tasks_db_id": "...",
         "logs_db_id": "...", "artifacts_db_id": "...", "url": "..."}
        or {"ok": True, "skipped": True, ...} on failure.
    """
    client = _client()
    if not client:
        return _skipped("NOTION_TOKEN not set", project_id="", tasks_db_id="",
                        logs_db_id="", artifacts_db_id="")

    parent_id = os.environ.get("NOTION_PARENT_PAGE_ID", "")
    if not _valid_id(parent_id):
        logger.warning(
            "NOTION_PARENT_PAGE_ID is not a valid UUID — run `codepilot config init`."
        )
        return _skipped("NOTION_PARENT_PAGE_ID not configured",
                        project_id="", tasks_db_id="", logs_db_id="", artifacts_db_id="")

    try:
        # --- Root project page ---
        page = client.pages.create(
            parent={"type": "page_id", "page_id": parent_id},
            properties={
                "title": {"title": [{"type": "text",
                                     "text": {"content": f"🚀 {project_name}"}}]}
            },
            children=[
                _callout(
                    f"Project: {project_name}\n"
                    f"Workspace: {workspace_path}\n"
                    f"Goal: {goal or '(see task)'}\n"
                    f"Started: {_now_label()}\n"
                    f"Status: 🔄 ACTIVE",
                    emoji="🗂️",
                ),
                _divider(),
                _para("Databases below are auto-created by CodePilot."),
            ],
        )
        project_id = page.get("id", "")
        if not project_id:
            return _skipped("Notion returned empty page ID",
                            project_id="", tasks_db_id="", logs_db_id="", artifacts_db_id="")

        # --- Tasks database (child of project page) — STRICT schema ---
        tasks_db = client.databases.create(
            parent={"type": "page_id", "page_id": project_id},
            title=[{"type": "text", "text": {"content": "📋 Tasks"}}],
            properties={
                "Name":           {"title": {}},
                "Status":         {"select": {"options": [
                    {"name": "TODO",        "color": "gray"},
                    {"name": "IN_PROGRESS", "color": "blue"},
                    {"name": "DONE",        "color": "green"},
                    {"name": "BLOCKED",     "color": "red"},
                ]}},
                "Priority":       {"select": {"options": [
                    {"name": "HIGH",   "color": "red"},
                    {"name": "MEDIUM", "color": "yellow"},
                    {"name": "LOW",    "color": "gray"},
                ]}},
                "Task ID":        {"rich_text": {}},
                "Assigned Agent": {"rich_text": {}},
                "Created Time":   {"date": {}},
                "Updated Time":   {"date": {}},
            },
        )
        tasks_db_id = tasks_db.get("id", "")
        if tasks_db_id and not _validate_db_schema(tasks_db, _TASKS_REQUIRED_PROPS, "Tasks"):
            logger.error("Tasks DB schema invalid — Notion project setup aborted")
            return _skipped("Tasks DB schema validation failed",
                            project_id=project_id, tasks_db_id="",
                            logs_db_id="", artifacts_db_id="")

        # --- Activity Log database ---
        logs_db = client.databases.create(
            parent={"type": "page_id", "page_id": project_id},
            title=[{"type": "text", "text": {"content": "📜 Activity Log"}}],
            properties={
                "Message":    {"title": {}},
                "Event Type": {"select": {"options": [
                    {"name": "PLAN",   "color": "purple"},
                    {"name": "RUN",    "color": "blue"},
                    {"name": "ERROR",  "color": "red"},
                    {"name": "FIX",    "color": "orange"},
                    {"name": "TEST",   "color": "green"},
                    {"name": "DEPLOY", "color": "pink"},
                    {"name": "COMMIT", "color": "gray"},
                    {"name": "DEBUG",  "color": "brown"},
                    {"name": "NOTE",   "color": "default"},
                ]}},
                "Task ID":  {"rich_text": {}},
                "Agent":    {"rich_text": {}},
                "Timestamp":{"date": {}},
            },
        )
        logs_db_id = logs_db.get("id", "")
        if logs_db_id and not _validate_db_schema(logs_db, _LOGS_REQUIRED_PROPS, "Activity Log"):
            logger.warning("Activity Log DB schema invalid — continuing with empty logs_db_id")
            logs_db_id = ""

        # --- Test Artifacts database ---
        artifacts_db = client.databases.create(
            parent={"type": "page_id", "page_id": project_id},
            title=[{"type": "text", "text": {"content": "🧪 Test Artifacts"}}],
            properties={
                "Name":      {"title": {}},
                "Type":      {"select": {"options": [
                    {"name": "SCREENSHOT",   "color": "blue"},
                    {"name": "LOG",          "color": "gray"},
                    {"name": "TEST_RESULT",  "color": "green"},
                    {"name": "QA_REPORT",    "color": "purple"},
                ]}},
                "Path":      {"rich_text": {}},
                "Task ID":   {"rich_text": {}},
                "Result":    {"select": {"options": [
                    {"name": "PASS",    "color": "green"},
                    {"name": "FAIL",    "color": "red"},
                    {"name": "PENDING", "color": "gray"},
                ]}},
                "Timestamp": {"date": {}},
            },
        )
        artifacts_db_id = artifacts_db.get("id", "")
        if artifacts_db_id and not _validate_db_schema(
            artifacts_db, _ARTIFACTS_REQUIRED_PROPS, "Test Artifacts"
        ):
            logger.warning("Test Artifacts DB schema invalid — continuing with empty artifacts_db_id")
            artifacts_db_id = ""

        logger.info(
            "Notion project setup complete: %s → page=%s tasks=%s logs=%s artifacts=%s",
            project_name, project_id[:8], tasks_db_id[:8],
            logs_db_id[:8], artifacts_db_id[:8],
        )
        return {
            "ok": True,
            "project_id":     project_id,
            "tasks_db_id":    tasks_db_id,
            "logs_db_id":     logs_db_id,
            "artifacts_db_id": artifacts_db_id,
            "url": page.get("url", ""),
        }

    except Exception as exc:
        _log_access_hint(exc, parent_id)
        return _skipped(f"Notion error: {exc}",
                        project_id="", tasks_db_id="", logs_db_id="", artifacts_db_id="")


# ---------------------------------------------------------------------------
# 2. Task management
# ---------------------------------------------------------------------------

def notion_create_task(
    task_id: str,
    title: str,
    description: str = "",
    priority: str = "MEDIUM",
    assigned_agent: str = "DeveloperAgent",
    tool_context: ToolContext = None,
) -> dict:
    """Create a task row in the Tasks database.

    The Tasks DB ID is read from state (notion_tasks_db_id) set by PlannerAgent.

    Args:
        task_id:        Internal plan ID (e.g. "task_1").
        title:          Task title.
        description:    What needs to be done.
        priority:       HIGH | MEDIUM | LOW.
        assigned_agent: Which agent owns this task.

    Returns:
        {"ok": True, "task_page_id": "<uuid>"} or {"ok": True, "skipped": True, ...}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured", task_page_id="")
    tasks_db_id = _state_id(tool_context, "notion_tasks_db_id")
    if not tasks_db_id:
        return _skipped("notion_tasks_db_id not set in state — call notion_setup_project first",
                        task_page_id="")

    pri = priority.upper() if priority.upper() in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"
    now = _now_iso()
    try:
        page = client.pages.create(
            parent={"type": "database_id", "database_id": tasks_db_id},
            properties={
                "Name":           {"title": [{"type": "text", "text": {"content": title[:200]}}]},
                "Status":         {"select": {"name": "TODO"}},
                "Priority":       {"select": {"name": pri}},
                "Task ID":        {"rich_text": [{"type": "text", "text": {"content": task_id}}]},
                "Assigned Agent": {"rich_text": [{"type": "text", "text": {"content": assigned_agent}}]},
                "Created Time":   {"date": {"start": now}},
                "Updated Time":   {"date": {"start": now}},
            },
        )
        task_page_id = page.get("id", "")
        # Store description as a page block (keeps DB schema strict)
        if description and task_page_id:
            _safe_append(
                client, task_page_id,
                [_callout(f"Description: {description[:1000]}", emoji="📋")],
                "notion_create_task",
            )
        logger.debug("Task created: [%s] %s → %s", task_id, title, task_page_id)
        return {"ok": True, "task_page_id": task_page_id}
    except Exception as exc:
        logger.warning("notion_create_task failed: %s", exc)
        return _skipped(str(exc), task_page_id="")


def notion_update_task(
    task_id: str,
    status: str,
    notes: str = "",
    github_ref: str = "",
    commit_ref: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Update a task's Status property and append a structured comment.

    Args:
        task_id:    Internal plan task ID (e.g. "task_1").
        status:     TODO | IN_PROGRESS | DONE | BLOCKED.
        notes:      Progress notes, error description, or fix summary.
        github_ref: Optional GitHub PR or commit URL.
        commit_ref: Optional git commit hash.

    Returns:
        {"ok": True} or {"ok": True, "skipped": True, ...}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured")
    tasks_db_id = _state_id(tool_context, "notion_tasks_db_id")
    if not tasks_db_id:
        return _skipped("notion_tasks_db_id not in state")

    status_upper = status.upper()
    valid = {"TODO", "IN_PROGRESS", "DONE", "BLOCKED"}
    prop_status = status_upper if status_upper in valid else "IN_PROGRESS"

    task_page_id = _find_task_page(client, tasks_db_id, task_id)
    if not task_page_id:
        return _skipped(f"Task page not found for task_id={task_id!r}")

    try:
        # Update Status and Updated Time (the only writable DB properties)
        client.pages.update(
            page_id=task_page_id,
            properties={
                "Status":       {"select": {"name": prop_status}},
                "Updated Time": {"date": {"start": _now_iso()}},
            },
        )
    except Exception as exc:
        logger.warning("notion_update_task (property update) failed: %s", exc)

    # Append structured comment block with all context
    comment_parts = [f"[{_now_label()}] {_icon(status)} Status → {status_upper}"]
    if notes:
        comment_parts.append(f"Notes: {notes[:400]}")
    if github_ref:
        comment_parts.append(f"GitHub: {github_ref}")
    if commit_ref:
        comment_parts.append(f"Commit: {commit_ref}")

    return _safe_append(
        client, task_page_id,
        [_callout("\n".join(comment_parts), emoji=_icon(status))],
        "notion_update_task",
    )


def notion_query_tasks(
    status_filter: str = "all",
    tool_context: ToolContext = None,
) -> dict:
    """Query the Tasks database and return matching tasks.

    Use this BEFORE working on tasks to identify what is pending, blocked, etc.

    Args:
        status_filter: "TODO" | "IN_PROGRESS" | "DONE" | "BLOCKED" | "all"

    Returns:
        {"ok": True, "tasks": [{"task_id", "title", "status", "priority",
                                 "assigned_agent", "task_page_id"}]}
        or {"ok": True, "skipped": True, "tasks": []}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured", tasks=[])
    tasks_db_id = _state_id(tool_context, "notion_tasks_db_id")
    if not tasks_db_id:
        return _skipped("notion_tasks_db_id not in state", tasks=[])

    valid = {"TODO", "IN_PROGRESS", "DONE", "BLOCKED"}
    query_kwargs: dict = {"database_id": tasks_db_id, "page_size": 50}
    sf = status_filter.upper()
    if sf in valid:
        query_kwargs["filter"] = {"property": "Status", "select": {"equals": sf}}
    query_kwargs["sorts"] = [
        {"property": "Priority",    "direction": "descending"},
        {"property": "Created Time", "direction": "ascending"},
    ]

    try:
        result = client.databases.query(**query_kwargs)
        tasks = []
        for page in result.get("results") or []:
            props = page.get("properties") or {}
            task_id_parts = (props.get("Task ID") or {}).get("rich_text") or []
            name_parts    = (props.get("Name") or {}).get("title") or []
            status_val    = (props.get("Status") or {}).get("select") or {}
            pri_val       = (props.get("Priority") or {}).get("select") or {}
            agent_parts   = (props.get("Assigned Agent") or {}).get("rich_text") or []
            tasks.append({
                "task_id":        _safe_text(task_id_parts),
                "title":          _safe_text(name_parts),
                "status":         status_val.get("name", "TODO"),
                "priority":       pri_val.get("name", "MEDIUM"),
                "assigned_agent": _safe_text(agent_parts),
                "task_page_id":   page.get("id", ""),
            })
        return {"ok": True, "tasks": tasks, "count": len(tasks),
                "filter": status_filter}
    except Exception as exc:
        logger.warning("notion_query_tasks failed: %s", exc)
        return _skipped(str(exc), tasks=[])


def notion_add_comment(
    task_id: str,
    agent_name: str,
    comment_type: str,
    content: str,
    tool_context: ToolContext = None,
) -> dict:
    """Append a structured agent comment to a task page.

    Use this to leave reasoning, debugging notes, and decisions so the
    project history is traceable in Notion.

    Args:
        task_id:      Internal plan task ID.
        agent_name:   Which agent is leaving the comment (e.g. "DebugAgent").
        comment_type: DEBUG | DECISION | FIX | NOTE | ANALYSIS
        content:      Comment text (root cause, decision rationale, etc.).

    Returns:
        {"ok": True} or {"ok": True, "skipped": True, ...}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured")
    tasks_db_id = _state_id(tool_context, "notion_tasks_db_id")
    if not tasks_db_id:
        return _skipped("notion_tasks_db_id not in state")

    task_page_id = _find_task_page(client, tasks_db_id, task_id)
    if not task_page_id:
        return _skipped(f"Task not found: {task_id}")

    icon = _icon(comment_type)
    header = f"{icon} [{comment_type.upper()}] {agent_name} @ {_now_label()}"
    return _safe_append(client, task_page_id,
                        [_callout(f"{header}\n\n{content[:1800]}", emoji=icon)],
                        "notion_add_comment")


# ---------------------------------------------------------------------------
# 3. Activity logging
# ---------------------------------------------------------------------------

def notion_log_event(
    event_type: str,
    message: str,
    task_id: str = "",
    agent: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Add a structured row to the Activity Log database.

    Use this instead of appending raw text blocks. Log entries are
    queryable, filterable, and displayed cleanly in Notion views.

    Args:
        event_type: PLAN | RUN | ERROR | FIX | TEST | DEPLOY | COMMIT
        message:    Human-readable event description (keep under 200 chars).
        task_id:    Optional related task ID (e.g. "task_1").
        agent:      Which agent is logging (e.g. "DeveloperAgent").

    Returns:
        {"ok": True} or {"ok": True, "skipped": True, ...}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured")
    logs_db_id = _state_id(tool_context, "notion_logs_db_id")
    if not logs_db_id:
        return _skipped("notion_logs_db_id not in state")

    et = event_type.upper()
    valid_types = {"PLAN", "RUN", "ERROR", "FIX", "TEST", "DEPLOY", "COMMIT", "DEBUG", "NOTE"}
    event_select = et if et in valid_types else "RUN"
    try:
        client.pages.create(
            parent={"type": "database_id", "database_id": logs_db_id},
            properties={
                "Message":    {"title": [{"type": "text",
                                          "text": {"content": message[:200]}}]},
                "Event Type": {"select": {"name": event_select}},
                "Task ID":    {"rich_text": [{"type": "text",
                                              "text": {"content": task_id[:50]}}]},
                "Agent":      {"rich_text": [{"type": "text",
                                              "text": {"content": agent[:100]}}]},
                "Timestamp":  {"date": {"start": _now_iso()}},
            },
        )
        return {"ok": True}
    except Exception as exc:
        logger.warning("notion_log_event failed: %s", exc)
        return _skipped(str(exc))


# ---------------------------------------------------------------------------
# 4. Test artifacts
# ---------------------------------------------------------------------------

def notion_add_artifact(
    task_id: str,
    artifact_type: str,
    path: str,
    result: str = "PENDING",
    notes: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Store a test artifact (screenshot, log, test result) in the Artifacts DB.

    Args:
        task_id:       Internal plan task ID this artifact belongs to.
        artifact_type: SCREENSHOT | LOG | TEST_RESULT | QA_REPORT
        path:          File system path or URL to the artifact.
        result:        PASS | FAIL | PENDING (test outcome if applicable).
        notes:         Optional context about this artifact.

    Returns:
        {"ok": True, "artifact_page_id": "<uuid>"} or {"ok": True, "skipped": True, ...}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured", artifact_page_id="")
    artifacts_db_id = _state_id(tool_context, "notion_artifacts_db_id")
    if not artifacts_db_id:
        return _skipped("notion_artifacts_db_id not in state", artifact_page_id="")

    at = artifact_type.upper()
    valid_types = {"SCREENSHOT", "LOG", "TEST_RESULT", "QA_REPORT"}
    type_select = at if at in valid_types else "LOG"

    res = result.upper() if result.upper() in ("PASS", "FAIL", "PENDING") else "PENDING"
    import os as _os
    name = _os.path.basename(path) if path else f"{type_select}_{_now_label()}"
    try:
        page = client.pages.create(
            parent={"type": "database_id", "database_id": artifacts_db_id},
            properties={
                "Name":      {"title": [{"type": "text",
                                         "text": {"content": name[:200]}}]},
                "Type":      {"select": {"name": type_select}},
                "Path":      {"rich_text": [{"type": "text",
                                              "text": {"content": path[:500]}}]},
                "Task ID":   {"rich_text": [{"type": "text",
                                              "text": {"content": task_id[:50]}}]},
                "Result":    {"select": {"name": res}},
                "Timestamp": {"date": {"start": _now_iso()}},
            },
        )
        art_id = page.get("id", "")
        if notes and art_id:
            _safe_append(client, art_id, [_para(notes[:500])], "notion_add_artifact")
        return {"ok": True, "artifact_page_id": art_id}
    except Exception as exc:
        logger.warning("notion_add_artifact failed: %s", exc)
        return _skipped(str(exc), artifact_page_id="")


# ---------------------------------------------------------------------------
# 5. QA sub-page (TestAgent)
# ---------------------------------------------------------------------------

def notion_create_qa_page(
    app_url: str = "",
    app_type: str = "web",
    tool_context: ToolContext = None,
) -> dict:
    """Create a QA Testing sub-page inside the project page.

    TestAgent creates this at the start of testing, then calls
    notion_log_qa_step() for every browser action, and notion_finalize_qa()
    when done.

    Returns:
        {"ok": True, "qa_page_id": "<uuid>", "url": "..."}
        or {"ok": True, "skipped": True, "qa_page_id": ""}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured", qa_page_id="")
    project_id = _state_id(tool_context, "notion_project_id")
    if not project_id:
        return _skipped("notion_project_id not in state", qa_page_id="")

    try:
        page = client.pages.create(
            parent={"type": "page_id", "page_id": project_id},
            properties={
                "title": {"title": [{"type": "text",
                                     "text": {"content": "🧪 QA Test Report"}}]}
            },
            children=[
                _callout(
                    f"URL: {app_url or 'N/A'}\n"
                    f"Type: {app_type.upper()}\n"
                    f"Date: {_now_label()}\n"
                    f"Tester: TestAgent (Automated)",
                    emoji="📋",
                ),
                _divider(),
                _h2("🔍 Test Steps"),
                _para("Steps are logged below as testing progresses."),
                _divider(),
                _h2("📊 Summary"),
                _para("Summary will appear here after testing completes."),
            ],
        )
        qa_page_id = page.get("id", "")
        logger.info("Notion QA page created → %s", qa_page_id)
        return {"ok": True, "qa_page_id": qa_page_id, "url": page.get("url", "")}
    except Exception as exc:
        logger.warning("notion_create_qa_page failed: %s", exc)
        return _skipped(str(exc), qa_page_id="")


def notion_log_qa_step(
    step_num: int,
    action: str,
    result: str,
    screenshot_path: str = "",
    notes: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Append a single test step to the QA report page.

    Also creates an artifact row in the Test Artifacts database when a
    screenshot path is provided.

    Args:
        step_num:        Step number (1, 2, 3…).
        action:          What was done ("Click Add Task button").
        result:          "PASS" or "FAIL: <reason>".
        screenshot_path: Path to the screenshot file (optional).
        notes:           Extra context or error details.

    Returns:
        {"ok": True} or {"ok": True, "skipped": True, ...}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured")
    qa_page_id = _state_id(tool_context, "notion_qa_page_id")
    if not qa_page_id:
        return _skipped("notion_qa_page_id not in state")

    is_pass = result.upper().startswith("PASS")
    icon = "✅" if is_pass else "❌"

    # Step header callout
    header_text = f"Step {step_num}: {icon} {action}\nResult: {result}"
    if notes:
        header_text += f"\nNotes: {notes[:300]}"
    blocks: list = [_callout(header_text, emoji=icon)]

    # Dedicated screenshot block — prominent, shows filename clearly
    if screenshot_path:
        import os as _os
        fname = _os.path.basename(screenshot_path)
        blocks.append(
            _callout(
                f"📸 Screenshot saved\n"
                f"File:  {fname}\n"
                f"Path:  {screenshot_path}\n"
                f"Saved to: <project>/tests/screenshots/{fname}",
                emoji="📸",
            )
        )

    result_dict = _safe_append(client, qa_page_id, blocks, "notion_log_qa_step")

    # Mirror to Test Artifacts DB (path stored as searchable record)
    if screenshot_path:
        notion_add_artifact(
            task_id="browser-test",
            artifact_type="SCREENSHOT",
            path=screenshot_path,
            result="PASS" if is_pass else "FAIL",
            notes=f"Step {step_num}: {action}",
            tool_context=tool_context,
        )

    return result_dict


def notion_add_screenshot(
    screenshot_path: str,
    step_num: int,
    action: str,
    result: str = "PASS",
    tool_context: ToolContext = None,
) -> dict:
    """Record a Playwright screenshot in Notion (QA page + Test Artifacts DB).

    Call this immediately after every browser_take_screenshot() call.
    The screenshot is stored locally at <project>/tests/screenshots/<filename>.
    This tool records the filename and path in Notion so the QA report is
    always in sync with local artifacts.

    Note: The Notion public API does not support binary file upload from
    integration tokens. This tool records the file path prominently in
    the QA page and the Test Artifacts database. The actual PNG file is
    saved locally in <project>/tests/screenshots/.

    Args:
        screenshot_path: Absolute path returned by browser_take_screenshot().
        step_num:        Test step number this screenshot belongs to.
        action:          Description of what was tested ("Click Add Task button").
        result:          "PASS" or "FAIL: <reason>".

    Returns:
        {"ok": True, "filename": str, "artifact_page_id": str}
        or {"ok": True, "skipped": True, ...} if Notion is not configured.
    """
    import os as _os

    client = _client()
    if not client:
        return _skipped("Notion not configured", filename="", artifact_page_id="")

    fname = _os.path.basename(screenshot_path) if screenshot_path else ""
    is_pass = result.upper().startswith("PASS")
    icon = "✅" if is_pass else "❌"

    # 1. Append a prominent block to the QA page
    qa_page_id = _state_id(tool_context, "notion_qa_page_id")
    if qa_page_id:
        block_text = (
            f"📸 Screenshot captured  {icon}\n"
            f"Step:   {step_num} — {action}\n"
            f"Result: {result}\n"
            f"File:   {fname}\n"
            f"Path:   {screenshot_path}"
        )
        _safe_append(client, qa_page_id,
                     [_callout(block_text, emoji="📸")],
                     "notion_add_screenshot")

    # 2. Create a row in the Test Artifacts DB
    art = notion_add_artifact(
        task_id="browser-test",
        artifact_type="SCREENSHOT",
        path=screenshot_path,
        result="PASS" if is_pass else "FAIL",
        notes=f"Step {step_num}: {action}",
        tool_context=tool_context,
    )
    art_id = art.get("artifact_page_id", "")

    logger.debug("notion_add_screenshot: step=%d file=%s art_id=%s", step_num, fname, art_id)
    return {"ok": True, "filename": fname, "artifact_page_id": art_id}


def notion_finalize_qa(
    overall_result: str,
    total_steps: int,
    passed: int,
    failed: int,
    summary: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Append the summary section to the QA report page.

    Also creates a QA_REPORT artifact row in the Test Artifacts database.

    Args:
        overall_result: "PASS" or "FAIL".
        total_steps:    Total test steps executed.
        passed:         Steps that passed.
        failed:         Steps that failed.
        summary:        Free-text summary of the test session.

    Returns:
        {"ok": True} or {"ok": True, "skipped": True, ...}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured")
    qa_page_id = _state_id(tool_context, "notion_qa_page_id")
    if not qa_page_id:
        return _skipped("notion_qa_page_id not in state")

    is_pass = overall_result.upper().startswith("PASS")
    verdict_icon = "✅" if is_pass else "❌"
    verdict = "PASS" if is_pass else "FAIL"
    summary_text = (
        f"Overall: {verdict_icon} {verdict}\n"
        f"Total: {total_steps} | Passed: {passed} | Failed: {failed}\n"
        f"Tested: {_now_label()}"
    )
    if summary:
        summary_text += f"\n\n{summary[:600]}"

    blocks = [_divider(), _callout(summary_text, emoji=verdict_icon)]
    result_dict = _safe_append(client, qa_page_id, blocks, "notion_finalize_qa")

    # Record overall test result as an artifact
    notion_add_artifact(
        task_id="browser-test",
        artifact_type="QA_REPORT",
        path="",
        result=verdict,
        notes=summary_text,
        tool_context=tool_context,
    )
    return result_dict


# ---------------------------------------------------------------------------
# 6. Finalization (FinalizerAgent)
# ---------------------------------------------------------------------------

def notion_finalize_project(
    final_status: str,
    summary: str,
    github_url: str = "",
    tool_context: ToolContext = None,
) -> dict:
    """Create a Summary sub-page and update the project page status.

    Called once by FinalizerAgent at the end of the pipeline.

    Args:
        final_status: "SUCCESS" | "PARTIAL: ..." | "FAILED: ..."
        summary:      What was built, how to run it, known issues.
        github_url:   GitHub PR or repo URL if available.

    Returns:
        {"ok": True} or {"ok": True, "skipped": True, ...}
    """
    client = _client()
    if not client:
        return _skipped("Notion not configured")
    project_id = _state_id(tool_context, "notion_project_id")
    if not project_id:
        return _skipped("notion_project_id not in state")

    is_success = "SUCCESS" in final_status.upper()
    icon = "✅" if is_success else ("⚠️" if "PARTIAL" in final_status.upper() else "❌")

    # Update project page header to reflect final status
    status_block: list = [
        _divider(),
        _callout(
            f"{icon} Final Status: {final_status}\n"
            f"Completed: {_now_label()}\n"
            + (f"GitHub: {github_url}\n" if github_url else ""),
            emoji=icon,
        ),
    ]
    _safe_append(client, project_id, status_block, "notion_finalize_project_header")

    # Create Summary sub-page
    try:
        client.pages.create(
            parent={"type": "page_id", "page_id": project_id},
            properties={
                "title": {"title": [{"type": "text",
                                     "text": {"content": "📄 Project Summary"}}]}
            },
            children=[
                _callout(
                    f"Status: {icon} {final_status}\n"
                    f"Completed: {_now_label()}"
                    + (f"\nGitHub: {github_url}" if github_url else ""),
                    emoji=icon,
                ),
                _divider(),
                _h2("📋 Summary"),
                _para(summary[:2000] if summary else "No summary provided."),
                _divider(),
                _h2("🔗 Links"),
                *([_bullet(f"GitHub: {github_url}")] if github_url else [_bullet("GitHub: not configured")]),
            ],
        )
    except Exception as exc:
        logger.warning("notion_finalize_project (summary page) failed: %s", exc)

    return {"ok": True}
