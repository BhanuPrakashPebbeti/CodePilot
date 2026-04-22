"""Planning MCP server — todo-driven autonomous development.

The agent MUST work through a structured plan:
  1. create_plan(goal, tasks) — define the work
  2. get_current_task()       — get next pending task
  3. start_task(task_id)      — mark in-progress
  4. complete_task(task_id)   — mark done
  5. fail_task(task_id, err)  — mark failed + trigger replan
  6. replan(reason)           — adjust plan based on failures
  7. get_plan_status()        — progress summary

Task states: pending → in_progress → done | failed | skipped

NOTE: State is persisted to a temp file because the MCP client
creates a NEW session (subprocess) for each tool call. In-memory
state does NOT survive between calls.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

app = FastMCP(name="planning")


# ============================================================================
# FILE-BASED PERSISTENCE
# ============================================================================

# Use a fixed temp file path so ALL sessions share the same state.
# This survives across the per-call subprocess restarts.
_STATE_FILE = os.path.join(tempfile.gettempdir(), "codepilot_plan_state.json")


def _save_state(plan_dict: dict) -> None:
    """Persist plan state to disk."""
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump(plan_dict, f)
    except Exception:
        pass  # Best-effort persistence


def _load_state() -> Optional[dict]:
    """Load plan state from disk."""
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return None


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "message": message})

def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error})


# ============================================================================
# PLAN DATA MODEL
# ============================================================================

@dataclass
class Task:
    id: int
    title: str
    status: str = "pending"  # pending, in_progress, done, failed, skipped
    error: Optional[str] = None
    retry_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Task":
        return Task(
            id=d["id"],
            title=d["title"],
            status=d.get("status", "pending"),
            error=d.get("error"),
            retry_count=d.get("retry_count", 0),
        )


@dataclass
class Plan:
    goal: str = ""
    tasks: List[Task] = field(default_factory=list)
    _counter: int = 0

    def add(self, title: str) -> Task:
        self._counter += 1
        task = Task(id=self._counter, title=title)
        self.tasks.append(task)
        return task

    def get(self, task_id: int) -> Optional[Task]:
        return next((t for t in self.tasks if t.id == task_id), None)

    def next_pending(self) -> Optional[Task]:
        return next((t for t in self.tasks if t.status == "pending"), None)

    def current_in_progress(self) -> Optional[Task]:
        return next((t for t in self.tasks if t.status == "in_progress"), None)

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def done_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "done")

    @property
    def failed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "failed")

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "pending")

    @property
    def in_progress_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "in_progress")

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.done_count / self.total) * 100

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "tasks": [t.to_dict() for t in self.tasks],
            "progress": {
                "total": self.total,
                "done": self.done_count,
                "failed": self.failed_count,
                "pending": self.pending_count,
                "in_progress": self.in_progress_count,
                "percent": self.percent,
            },
            "_counter": self._counter,
        }

    @staticmethod
    def from_dict(d: dict) -> "Plan":
        plan = Plan(goal=d.get("goal", ""))
        plan._counter = d.get("_counter", 0)
        for t in d.get("tasks", []):
            plan.tasks.append(Task.from_dict(t))
        if plan._counter == 0 and plan.tasks:
            plan._counter = max(t.id for t in plan.tasks)
        return plan

    def save(self) -> None:
        """Persist current state to disk."""
        _save_state(self.to_dict())

    @staticmethod
    def load() -> "Plan":
        """Load plan from disk, or return empty plan."""
        data = _load_state()
        if data:
            return Plan.from_dict(data)
        return Plan()


# ============================================================================
# TOOLS
# ============================================================================

@app.tool()
def create_plan(goal: str, tasks: str) -> str:
    """Create a development plan with ordered tasks.

    Args:
        goal: High-level objective (e.g. "Build a todo app with a REST API and database")
        tasks: Pipe-separated list of ordered tasks. Use | as the delimiter.
               (e.g. "Set up project structure | Create backend API | Create frontend UI | Connect frontend to backend | Test and verify")
               IMPORTANT: Do NOT use commas as delimiters. Use | (pipe) characters.
    
    Returns structured plan with task IDs for tracking.
    """
    # Always start fresh — delete any stale state from previous sessions.
    # This prevents the "tasks already done" problem where a previous
    # run's completed plan blocks the current run.
    if os.path.exists(_STATE_FILE):
        try:
            os.remove(_STATE_FILE)
        except OSError:
            pass

    plan = Plan(goal=goal)

    # Split on pipe character; fall back to newlines if no pipes found
    if "|" in tasks:
        task_list = [t.strip() for t in tasks.split("|") if t.strip()]
    elif "\n" in tasks:
        task_list = [t.strip().lstrip("0123456789.-) ") for t in tasks.split("\n") if t.strip()]
    else:
        # Last resort: treat entire string as a single task
        task_list = [tasks.strip()] if tasks.strip() else []

    if not task_list:
        return _err("No tasks provided")

    for title in task_list:
        plan.add(title)

    plan.save()

    return _ok(
        plan.to_dict(),
        f"Plan created: {len(task_list)} tasks for '{goal}'"
    )


@app.tool()
def get_current_task() -> str:
    """Get the current task to work on.
    
    Returns the in-progress task if one exists, otherwise the next pending task.
    If all tasks are done, returns completion status.
    """
    plan = Plan.load()

    # Check for in-progress first
    current = plan.current_in_progress()
    if current:
        return _ok(
            {"task": current.to_dict(), **plan.to_dict()["progress"]},
            f"In progress: #{current.id} {current.title}"
        )

    # Next pending
    pending = plan.next_pending()
    if pending:
        return _ok(
            {"task": pending.to_dict(), **plan.to_dict()["progress"]},
            f"Next: #{pending.id} {pending.title}"
        )

    # All done
    if plan.total == 0:
        return _err("No plan created yet. Use create_plan() first.")

    if plan.failed_count > 0:
        failed = [t for t in plan.tasks if t.status == "failed"]
        return _ok(
            {"task": None, "all_done": False, "has_failures": True,
             "failed_tasks": [t.to_dict() for t in failed],
             **plan.to_dict()["progress"]},
            f"All tasks attempted. {plan.failed_count} failed — consider replan()"
        )

    return _ok(
        {"task": None, "all_done": True, **plan.to_dict()["progress"]},
        f"🎉 All {plan.total} tasks completed!"
    )


@app.tool()
def start_task(task_id: int) -> str:
    """Mark a task as in-progress. Call this BEFORE working on a task.

    Args:
        task_id: Integer ID of the task (from create_plan or get_current_task).

    Returns:
        JSON with the updated task and progress info.
    """
    plan = Plan.load()
    task = plan.get(task_id)
    if not task:
        return _err(f"Task #{task_id} not found")
    if task.status not in ("pending", "failed"):
        return _err(f"Task #{task_id} is {task.status}, cannot start")

    task.status = "in_progress"
    task.error = None
    plan.save()
    return _ok(
        {"task": task.to_dict()},
        f"Started: #{task_id} {task.title}"
    )


@app.tool()
def complete_task(task_id: int) -> str:
    """Mark a task as done. Call this AFTER verifying the task works.

    Args:
        task_id: Integer ID of the task to complete.

    Returns:
        JSON with progress info and the completed task.
    """
    plan = Plan.load()
    task = plan.get(task_id)
    if not task:
        return _err(f"Task #{task_id} not found")

    task.status = "done"
    plan.save()
    return _ok(
        {**plan.to_dict()["progress"], "completed_task": task.to_dict()},
        f"✓ Done: #{task_id} {task.title} ({plan.percent:.0f}% complete)"
    )


@app.tool()
def fail_task(task_id: int, error: str = "") -> str:
    """Mark a task as failed. Include the error message for diagnosis.
    
    After failing, consider:
    - Fixing the issue and retrying (start_task again)
    - Using replan() to adjust the plan
    - Using skip_task() if it's non-critical
    """
    plan = Plan.load()
    task = plan.get(task_id)
    if not task:
        return _err(f"Task #{task_id} not found")

    task.status = "failed"
    task.error = error
    task.retry_count += 1
    plan.save()

    return _ok(
        {"task": task.to_dict(), **plan.to_dict()["progress"]},
        f"✗ Failed: #{task_id} {task.title} (attempt {task.retry_count})"
    )


@app.tool()
def skip_task(task_id: int, reason: str = "") -> str:
    """Skip a task that is no longer needed or not applicable.

    Args:
        task_id: Integer ID of the task to skip.
        reason: Why this task is being skipped (optional but recommended).

    Returns:
        JSON with the updated task.
    """
    plan = Plan.load()
    task = plan.get(task_id)
    if not task:
        return _err(f"Task #{task_id} not found")

    task.status = "skipped"
    task.error = reason or "Skipped"
    plan.save()
    return _ok(
        {"task": task.to_dict()},
        f"⊘ Skipped: #{task_id} {task.title}"
    )


@app.tool()
def add_task(title: str, after_task_id: int = 0) -> str:
    """Add a new task to the plan (at the end, or after a specific task).
    
    Use this when you discover additional work needed during execution.
    """
    plan = Plan.load()
    task = plan.add(title)

    # Reorder if after_task_id specified
    if after_task_id > 0:
        idx = next((i for i, t in enumerate(plan.tasks) if t.id == after_task_id), None)
        if idx is not None:
            plan.tasks.remove(task)
            plan.tasks.insert(idx + 1, task)

    plan.save()
    return _ok(
        {"task": task.to_dict(), "total": plan.total},
        f"Added: #{task.id} {title}"
    )


@app.tool()
def replan(reason: str, new_tasks: str = "") -> str:
    """Adjust the plan after encountering issues.
    
    Resets failed/pending tasks and optionally adds new ones.
    
    Args:
        reason: Why replanning is needed
        new_tasks: Pipe-separated new tasks to add (optional).
                   Use | as the delimiter, NOT commas.
                   Example: "Fix auth module | Add error handling | Retest API"
    """
    plan = Plan.load()

    # Reset failed tasks to pending
    for task in plan.tasks:
        if task.status == "failed":
            task.status = "pending"
            task.error = None

    # Add new tasks (pipe-separated, consistent with create_plan)
    added = []
    if new_tasks:
        if "|" in new_tasks:
            parts = [t.strip() for t in new_tasks.split("|") if t.strip()]
        else:
            # Fallback: treat as single task if no pipe
            parts = [new_tasks.strip()] if new_tasks.strip() else []
        for title in parts:
            t = plan.add(title)
            added.append(t.to_dict())

    plan.save()
    return _ok(
        {
            "reason": reason,
            "added_tasks": added,
            **plan.to_dict()["progress"],
        },
        f"Replanned: {reason}. {len(added)} new tasks added."
    )


@app.tool()
def get_plan_status() -> str:
    """Get full plan status with all tasks and progress."""
    plan = Plan.load()

    if plan.total == 0:
        return _err("No plan created yet. Use create_plan() first.")

    return _ok(
        plan.to_dict(),
        f"Plan: {plan.done_count}/{plan.total} done ({plan.percent:.0f}%)"
    )


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
