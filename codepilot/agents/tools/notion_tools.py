"""Local Notion API tools for CodePilot multi-project lifecycle tracking.

Creates and maintains structured project pages in Notion with:
  - Project info (name, workspace path, status, goal summary)
  - Task list (append-only status updates per task)
  - Execution log (append-only event stream)

Schema: page-hierarchy (not databases) for broad compatibility.
  Parent page (NOTION_PARENT_PAGE_ID)
    └── 🚀 <Project Name>
        ├── Project info (workspace, status, started, goal)
        ├── 📋 Tasks  (status updates appended inline)
        └── 📜 Execution Log  (timestamped events appended inline)

Each write is verified by inspecting the API response. On failure the call
retries once before returning an error (avoids silent data loss).

Requires: notion-client>=2.0.0  (pip install notion-client)
Env vars: NOTION_TOKEN, NOTION_PARENT_PAGE_ID
"""

import os
import time
from datetime import datetime, timezone
from typing import Optional

from ...utils.logger import get_logger

logger = get_logger(__name__)

# Retry: one automatic retry on any Notion API failure.
_MAX_RETRIES = 1
_RETRY_DELAY = 2.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _client():
    """Return a notion_client.Client or None if not configured."""
    try:
        from notion_client import Client
    except ImportError:
        logger.debug("notion-client not installed — Notion tools are no-ops")
        return None
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        return None
    return Client(auth=token)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _para(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
        },
    }


def _h3(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


_STATUS_ICON = {
    "ACTIVE": "🔄", "COMPLETED": "✅", "FAILED": "❌", "PARTIAL": "⚠️",
    "TODO": "📝", "IN_PROGRESS": "🔄", "DONE": "✅", "BLOCKED": "🚫",
    "RUN": "▶️", "ERROR": "❌", "FIX": "🔧", "TEST": "🧪",
    "PLAN": "📋", "DEPLOY": "🚀", "COMMIT": "💾",
}


def _icon(key: str) -> str:
    return _STATUS_ICON.get(key.upper(), "📌")


def _skipped(reason: str = "Notion not configured") -> dict:
    return {"ok": True, "skipped": True, "reason": reason, "project_id": ""}


def _append_with_retry(client, block_id: str, children: list) -> dict:
    """Append blocks to a Notion page with one retry on failure.

    Returns the raw API response on success, or raises the last exception.
    """
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = client.blocks.children.append(block_id=block_id, children=children)
            # Verify the write landed: response must contain results
            if not resp or not resp.get("results"):
                raise ValueError("Notion append returned empty results — write may have failed")
            return resp
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                logger.warning(
                    "Notion append failed (attempt %d/%d): %s — retrying in %.0fs",
                    attempt + 1, _MAX_RETRIES + 1, exc, _RETRY_DELAY,
                )
                time.sleep(_RETRY_DELAY)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

def notion_create_project(
    project_name: str,
    workspace_path: str,
    summary: str = "",
) -> dict:
    """Create a Notion page to track a project's full lifecycle.

    Call this at the start of each new project (PlannerAgent).
    The returned project_id must be stored via set_state so other agents
    can append tasks and logs.

    Env: NOTION_TOKEN, NOTION_PARENT_PAGE_ID (Notion page ID to nest under).

    Args:
        project_name: Human-readable name (e.g. "Todo App").
        workspace_path: Local filesystem path for the project.
        summary: Brief goal description from the user's request.

    Returns:
        {"ok": True, "project_id": str, "url": str}
        or {"ok": True, "skipped": True} if Notion is not configured.
    """
    client = _client()
    if not client:
        return _skipped()

    parent_id = os.environ.get("NOTION_PARENT_PAGE_ID", "")
    if not parent_id:
        logger.warning(
            "NOTION_PARENT_PAGE_ID is not set — Notion project tracking skipped. "
            "Run: codepilot config init → Update Notion token and paste your parent page ID."
        )
        return _skipped("NOTION_PARENT_PAGE_ID not configured")

    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            page = client.pages.create(
                parent={"type": "page_id", "page_id": parent_id},
                properties={
                    "title": {
                        "title": [{"type": "text", "text": {"content": f"🚀 {project_name}"}}]
                    }
                },
                children=[
                    _para(f"📁 Workspace: {workspace_path}"),
                    _para(f"📅 Started: {_now()}"),
                    _para("📊 Status: 🔄 ACTIVE"),
                    _para(f"📝 Goal: {summary}" if summary else "📝 Goal: (see user request)"),
                    _divider(),
                    _h3("📋 Tasks"),
                    _para("Tasks will appear below as the plan is created."),
                    _divider(),
                    _h3("📜 Execution Log"),
                    _para("Execution events will be logged below."),
                ],
            )
            page_id = page.get("id", "")
            url = page.get("url", "")
            if not page_id:
                raise ValueError("Notion page creation returned no page ID")
            logger.info("Notion project page created: %s → %s", project_name, page_id)
            return {"ok": True, "project_id": page_id, "url": url}
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                logger.warning(
                    "notion_create_project failed (attempt %d/%d): %s — retrying",
                    attempt + 1, _MAX_RETRIES + 1, exc,
                )
                time.sleep(_RETRY_DELAY)

    logger.error("notion_create_project failed after %d attempts: %s", _MAX_RETRIES + 1, last_exc)
    return {"ok": False, "error": str(last_exc), "project_id": ""}


def notion_update_project_status(
    project_id: str,
    status: str,
    summary: str = "",
) -> dict:
    """Append a project-level status update to the Notion page.

    Call this at the end of the pipeline (FinalizerAgent) to mark the
    project COMPLETED, FAILED, or PARTIAL.

    Args:
        project_id: Notion page ID (from notion_create_project or state).
        status: ACTIVE / COMPLETED / FAILED / PARTIAL.
        summary: Completion notes or context (e.g. "All tests pass. Run with: ...").

    Returns:
        {"ok": True/False}
    """
    client = _client()
    if not client:
        return {"ok": True, "skipped": True}
    if not project_id:
        return {"ok": True, "skipped": True, "reason": "No project_id in state"}

    text = f"[{_now()}] {_icon(status)} Project status → {status.upper()}"
    if summary:
        text += f"\n{summary[:500]}"

    try:
        _append_with_retry(client, project_id, [_divider(), _para(text)])
        return {"ok": True}
    except Exception as exc:
        logger.error("notion_update_project_status failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def notion_add_task(
    project_id: str,
    task_id: str,
    title: str,
    assigned_agent: str = "DeveloperAgent",
    priority: str = "MEDIUM",
) -> dict:
    """Append a task entry to the project's task list in Notion.

    Call once per task immediately after create_plan() (PlannerAgent).

    Args:
        project_id: Notion page ID (from state key notion_project_id).
        task_id: Internal ID from the planning system (e.g. "task-1").
        title: Task title/description from the plan.
        assigned_agent: Agent responsible (PlannerAgent/DeveloperAgent/etc).
        priority: HIGH / MEDIUM / LOW.

    Returns:
        {"ok": True/False}
    """
    client = _client()
    if not client:
        return {"ok": True, "skipped": True}
    if not project_id:
        return {"ok": True, "skipped": True}

    text = (
        f"📝 [{task_id}] {title}\n"
        f"   Agent: {assigned_agent} | Priority: {priority} | Status: TODO"
    )
    try:
        _append_with_retry(client, project_id, [_para(text)])
        return {"ok": True}
    except Exception as exc:
        logger.error("notion_add_task failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def notion_update_task_status(
    project_id: str,
    task_id: str,
    status: str,
    logs: str = "",
) -> dict:
    """Append a task status update to the Notion project page.

    Creates an audit trail — call when a task transitions to
    IN_PROGRESS, DONE, or BLOCKED.

    Args:
        project_id: Notion page ID (from state key notion_project_id).
        task_id: Internal task ID from the planning system.
        status: IN_PROGRESS / DONE / BLOCKED.
        logs: Optional context: error snippet, files changed, etc.

    Returns:
        {"ok": True/False}
    """
    client = _client()
    if not client:
        return {"ok": True, "skipped": True}
    if not project_id:
        return {"ok": True, "skipped": True}

    text = f"[{_now()}] {_icon(status)} Task {task_id} → {status.upper()}"
    if logs:
        text += f"\n{logs[:600]}"

    try:
        _append_with_retry(client, project_id, [_para(text)])
        return {"ok": True}
    except Exception as exc:
        logger.error("notion_update_task_status failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def notion_log_execution(
    project_id: str,
    event_type: str,
    details: str,
) -> dict:
    """Append an execution event to the project's log in Notion.

    Use for important lifecycle events: server start, test result,
    error encountered, fix applied, deployment.

    Args:
        project_id: Notion page ID (from state key notion_project_id).
        event_type: PLAN / RUN / ERROR / FIX / TEST / DEPLOY / COMMIT.
        details: Event description or truncated log output.

    Returns:
        {"ok": True/False}
    """
    client = _client()
    if not client:
        return {"ok": True, "skipped": True}
    if not project_id:
        return {"ok": True, "skipped": True}

    text = f"[{_now()}] {_icon(event_type)} [{event_type.upper()}] {details[:1200]}"

    try:
        _append_with_retry(client, project_id, [_para(text)])
        return {"ok": True}
    except Exception as exc:
        logger.error("notion_log_execution failed: %s", exc)
        return {"ok": False, "error": str(exc)}
