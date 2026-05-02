"""Local planning tools — task management stored in ADK session state.

Replaces planning_server.py MCP.  Tasks are stored directly in session
state (tool_context.state["_plan"]) so they persist across agent turns
within a session without temp files or subprocesses.
"""

import json
import time
from collections import Counter
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from ...utils.logger import get_logger

logger = get_logger(__name__)

_PLAN_KEY = "_plan"


def _get_plan(ctx: ToolContext) -> dict:
    raw = ctx.state.get(_PLAN_KEY)
    if not raw:
        return {"goal": "", "tasks": [], "created_at": 0}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {"goal": "", "tasks": [], "created_at": 0}
    return raw


def _save_plan(ctx: ToolContext, plan: dict) -> None:
    ctx.state[_PLAN_KEY] = json.dumps(plan)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def create_plan(goal: str, tasks: str, tool_context: ToolContext) -> dict:
    """Create a development plan with an ordered list of tasks.

    Call this once at the start of a session to define the work breakdown.
    Tasks are pipe-delimited (|) to avoid conflicts with commas in text.

    Args:
        goal: High-level description of what we are building.
        tasks: Pipe-separated task descriptions (e.g. "Setup project | Write API | Write tests").

    Returns:
        dict with ok, task_count, and task_ids (list).
    """
    task_list = [t.strip() for t in tasks.split("|") if t.strip()]
    if not task_list:
        return {"ok": False, "error": "No tasks provided"}

    plan = {
        "goal": goal,
        "created_at": int(time.time()),
        "tasks": [
            {
                "id": f"task_{i+1}",
                "description": desc,
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "notes": "",
            }
            for i, desc in enumerate(task_list)
        ],
    }
    _save_plan(tool_context, plan)
    logger.info("Plan created with %d tasks for goal: %s", len(task_list), goal)
    return {"ok": True, "goal": goal, "task_count": len(task_list),
            "task_ids": [t["id"] for t in plan["tasks"]]}


def get_current_task(tool_context: ToolContext) -> dict:
    """Return the next pending task in the plan.

    Returns:
        dict with ok, task (dict with id and description), or done=True when all tasks complete.
    """
    plan = _get_plan(tool_context)
    for task in plan.get("tasks", []):
        if task["status"] == "pending":
            return {"ok": True, "done": False, "task": task}
        if task["status"] == "in_progress":
            return {"ok": True, "done": False, "task": task,
                    "note": "Task already in progress — complete or fail it first"}
    return {"ok": True, "done": True, "task": None,
            "message": "All tasks complete"}


def start_task(task_id: str, tool_context: ToolContext) -> dict:
    """Mark a task as in-progress.

    Args:
        task_id: Task ID from get_current_task (e.g. "task_1").

    Returns:
        dict with ok.
    """
    plan = _get_plan(tool_context)
    for task in plan.get("tasks", []):
        if task["id"] == task_id:
            task["status"] = "in_progress"
            task["started_at"] = int(time.time())
            _save_plan(tool_context, plan)
            return {"ok": True, "task_id": task_id}
    return {"ok": False, "error": f"Task not found: {task_id}"}


def complete_task(task_id: str, tool_context: ToolContext, notes: str = "") -> dict:
    """Mark a task as complete.

    Args:
        task_id: Task ID to complete.
        notes: Optional notes about what was done.

    Returns:
        dict with ok and remaining (count of pending tasks).
    """
    plan = _get_plan(tool_context)
    for task in plan.get("tasks", []):
        if task["id"] == task_id:
            task["status"] = "done"
            task["completed_at"] = int(time.time())
            task["notes"] = notes
            _save_plan(tool_context, plan)
            remaining = sum(1 for t in plan["tasks"] if t["status"] == "pending")
            return {"ok": True, "task_id": task_id, "remaining": remaining}
    return {"ok": False, "error": f"Task not found: {task_id}"}


def fail_task(task_id: str, reason: str, tool_context: ToolContext) -> dict:
    """Mark a task as failed (will be retried next loop iteration).

    Args:
        task_id: Task ID.
        reason: Why it failed.

    Returns:
        dict with ok.
    """
    plan = _get_plan(tool_context)
    for task in plan.get("tasks", []):
        if task["id"] == task_id:
            task["status"] = "failed"
            task["notes"] = reason
            _save_plan(tool_context, plan)
            return {"ok": True, "task_id": task_id}
    return {"ok": False, "error": f"Task not found: {task_id}"}


def skip_task(task_id: str, reason: str, tool_context: ToolContext) -> dict:
    """Skip a task (mark as done without doing the work).

    Use when a task is no longer relevant or was already handled.

    Args:
        task_id: Task ID.
        reason: Why it is being skipped.

    Returns:
        dict with ok.
    """
    plan = _get_plan(tool_context)
    for task in plan.get("tasks", []):
        if task["id"] == task_id:
            task["status"] = "skipped"
            task["notes"] = reason
            _save_plan(tool_context, plan)
            return {"ok": True}
    return {"ok": False, "error": f"Task not found: {task_id}"}


def get_plan_status(tool_context: ToolContext) -> dict:
    """Return a full summary of the current plan and task statuses.

    Returns:
        dict with ok, goal, tasks (list), and counts by status.
    """
    plan = _get_plan(tool_context)
    tasks = plan.get("tasks", [])
    counts = dict(Counter(t.get("status", "pending") for t in tasks))
    # Ensure all expected keys are present even if count is zero
    for k in ("pending", "in_progress", "done", "failed", "skipped"):
        counts.setdefault(k, 0)
    return {"ok": True, "goal": plan.get("goal", ""), "tasks": tasks, "counts": counts}
