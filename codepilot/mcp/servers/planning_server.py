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
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

app = FastMCP(name="planning")


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "message": message})

def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error})


# ============================================================================
# IN-MEMORY PLAN STATE
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
        }


# Global plan
_plan = Plan()


# ============================================================================
# TOOLS
# ============================================================================

@app.tool()
def create_plan(goal: str, tasks: str) -> str:
    """Create a development plan with ordered tasks.

    Args:
        goal: High-level objective (e.g. "Build a React todo app with FastAPI backend")
        tasks: Comma-separated list of ordered tasks
               (e.g. "Set up project structure,Create FastAPI backend,Create React frontend,Connect frontend to backend,Test and verify")
    
    Returns structured plan with task IDs for tracking.
    """
    global _plan
    _plan = Plan(goal=goal)

    task_list = [t.strip() for t in tasks.split(",") if t.strip()]
    if not task_list:
        return _err("No tasks provided")

    for title in task_list:
        _plan.add(title)

    return _ok(
        _plan.to_dict(),
        f"Plan created: {len(task_list)} tasks for '{goal}'"
    )


@app.tool()
def get_current_task() -> str:
    """Get the current task to work on.
    
    Returns the in-progress task if one exists, otherwise the next pending task.
    If all tasks are done, returns completion status.
    """
    # Check for in-progress first
    current = _plan.current_in_progress()
    if current:
        return _ok(
            {"task": current.to_dict(), **_plan.to_dict()["progress"]},
            f"In progress: #{current.id} {current.title}"
        )

    # Next pending
    pending = _plan.next_pending()
    if pending:
        return _ok(
            {"task": pending.to_dict(), **_plan.to_dict()["progress"]},
            f"Next: #{pending.id} {pending.title}"
        )

    # All done
    if _plan.total == 0:
        return _err("No plan created yet. Use create_plan() first.")

    if _plan.failed_count > 0:
        failed = [t for t in _plan.tasks if t.status == "failed"]
        return _ok(
            {"task": None, "all_done": False, "has_failures": True,
             "failed_tasks": [t.to_dict() for t in failed],
             **_plan.to_dict()["progress"]},
            f"All tasks attempted. {_plan.failed_count} failed — consider replan()"
        )

    return _ok(
        {"task": None, "all_done": True, **_plan.to_dict()["progress"]},
        f"🎉 All {_plan.total} tasks completed!"
    )


@app.tool()
def start_task(task_id: int) -> str:
    """Mark a task as in-progress. Call this BEFORE working on a task."""
    task = _plan.get(task_id)
    if not task:
        return _err(f"Task #{task_id} not found")
    if task.status not in ("pending", "failed"):
        return _err(f"Task #{task_id} is {task.status}, cannot start")

    task.status = "in_progress"
    task.error = None
    return _ok(
        {"task": task.to_dict()},
        f"Started: #{task_id} {task.title}"
    )


@app.tool()
def complete_task(task_id: int) -> str:
    """Mark a task as done. Call this AFTER verifying the task works."""
    task = _plan.get(task_id)
    if not task:
        return _err(f"Task #{task_id} not found")

    task.status = "done"
    return _ok(
        {**_plan.to_dict()["progress"], "completed_task": task.to_dict()},
        f"✓ Done: #{task_id} {task.title} ({_plan.percent:.0f}% complete)"
    )


@app.tool()
def fail_task(task_id: int, error: str = "") -> str:
    """Mark a task as failed. Include the error message for diagnosis.
    
    After failing, consider:
    - Fixing the issue and retrying (start_task again)
    - Using replan() to adjust the plan
    - Using skip_task() if it's non-critical
    """
    task = _plan.get(task_id)
    if not task:
        return _err(f"Task #{task_id} not found")

    task.status = "failed"
    task.error = error
    task.retry_count += 1

    return _ok(
        {"task": task.to_dict(), **_plan.to_dict()["progress"]},
        f"✗ Failed: #{task_id} {task.title} (attempt {task.retry_count})"
    )


@app.tool()
def skip_task(task_id: int, reason: str = "") -> str:
    """Skip a task (mark as not needed)."""
    task = _plan.get(task_id)
    if not task:
        return _err(f"Task #{task_id} not found")

    task.status = "skipped"
    task.error = reason or "Skipped"
    return _ok(
        {"task": task.to_dict()},
        f"⊘ Skipped: #{task_id} {task.title}"
    )


@app.tool()
def add_task(title: str, after_task_id: int = 0) -> str:
    """Add a new task to the plan (at the end, or after a specific task).
    
    Use this when you discover additional work needed during execution.
    """
    task = _plan.add(title)

    # Reorder if after_task_id specified
    if after_task_id > 0:
        idx = next((i for i, t in enumerate(_plan.tasks) if t.id == after_task_id), None)
        if idx is not None:
            _plan.tasks.remove(task)
            _plan.tasks.insert(idx + 1, task)

    return _ok(
        {"task": task.to_dict(), "total": _plan.total},
        f"Added: #{task.id} {title}"
    )


@app.tool()
def replan(reason: str, new_tasks: str = "") -> str:
    """Adjust the plan after encountering issues.
    
    Resets failed/pending tasks and optionally adds new ones.
    
    Args:
        reason: Why replanning is needed
        new_tasks: Comma-separated new tasks to add (optional)
    """
    # Reset failed tasks to pending
    for task in _plan.tasks:
        if task.status == "failed":
            task.status = "pending"
            task.error = None

    # Add new tasks
    added = []
    if new_tasks:
        for title in [t.strip() for t in new_tasks.split(",") if t.strip()]:
            t = _plan.add(title)
            added.append(t.to_dict())

    return _ok(
        {
            "reason": reason,
            "added_tasks": added,
            **_plan.to_dict()["progress"],
        },
        f"Replanned: {reason}. {len(added)} new tasks added."
    )


@app.tool()
def get_plan_status() -> str:
    """Get full plan status with all tasks and progress."""
    if _plan.total == 0:
        return _err("No plan created yet. Use create_plan() first.")

    return _ok(
        _plan.to_dict(),
        f"Plan: {_plan.done_count}/{_plan.total} done ({_plan.percent:.0f}%)"
    )


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
