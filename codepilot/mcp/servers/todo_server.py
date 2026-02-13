"""TODO MCP server for task tracking and planning.

NOTE: State is persisted to a temp file because the MCP client
creates a NEW session (subprocess) for each tool call.
"""
import json
import logging
import os
import tempfile
from typing import List, Optional

from fastmcp import FastMCP

app = FastMCP(name="todo")

# File-based persistence (shared across subprocess restarts)
_STATE_FILE = os.path.join(tempfile.gettempdir(), "codepilot_todo_state.json")


def _save() -> None:
    """Persist tasks to disk."""
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump({"tasks": _tasks, "counter": _task_counter}, f)
    except Exception:
        pass


def _load() -> None:
    """Load tasks from disk."""
    global _tasks, _task_counter
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r") as f:
                data = json.load(f)
                _tasks = data.get("tasks", [])
                _task_counter = data.get("counter", 0)
    except Exception:
        pass


# In-memory storage (reloaded from disk on each call)
_tasks: List[dict] = []
_task_counter = 0


@app.tool()
def add_task(title: str, description: str = "") -> str:
    """Add a new task to the todo list.

    Args:
        title: Task title.
        description: Task description.

    Returns:
        Success message with task ID.
    """
    global _task_counter
    _load()
    _task_counter += 1

    task = {
        "id": _task_counter,
        "title": title,
        "description": description,
        "status": "pending",
    }

    _tasks.append(task)
    _save()
    return f"Task added: #{task['id']} {title}"


@app.tool()
def complete_task(task_id: int) -> str:
    """Mark task as complete.

    Args:
        task_id: Task ID.

    Returns:
        Success message.
    """
    _load()
    for task in _tasks:
        if task["id"] == task_id:
            task["status"] = "completed"
            _save()
            return f"Task completed: #{task_id} {task['title']}"

    return f"Task not found: #{task_id}"


@app.tool()
def get_next_task() -> Optional[str]:
    """Get next pending task.

    Returns:
        Next task or None if all complete.
    """
    _load()
    for task in _tasks:
        if task["status"] == "pending":
            return f"#{task['id']}: {task['title']}\nDescription: {task['description']}"

    return None


@app.tool()
def list_tasks(status: str = "all") -> str:
    """List all tasks or tasks by status.

    Args:
        status: Filter by status ('pending', 'completed', 'all').

    Returns:
        Formatted task list.
    """
    _load()
    if not _tasks:
        return "No tasks yet."

    filtered = _tasks
    if status != "all":
        filtered = [t for t in _tasks if t["status"] == status]

    lines = []
    for task in filtered:
        status_icon = "✓" if task["status"] == "completed" else "○"
        lines.append(f"{status_icon} #{task['id']}: {task['title']}")
        if task["description"]:
            lines.append(f"  → {task['description']}")

    return "\n".join(lines)


@app.tool()
def clear_tasks() -> str:
    """Clear all tasks.

    Returns:
        Success message.
    """
    global _task_counter
    _load()
    count = len(_tasks)
    _tasks.clear()
    _task_counter = 0
    _save()
    return f"Cleared {count} tasks"


@app.tool()
def update_task(task_id: int, title: str = "", description: str = "") -> str:
    """Update task title or description.

    Args:
        task_id: Task ID.
        title: New title (optional).
        description: New description (optional).

    Returns:
        Success message.
    """
    _load()
    for task in _tasks:
        if task["id"] == task_id:
            if title:
                task["title"] = title
            if description:
                task["description"] = description
            _save()
            return f"Task updated: #{task_id}"

    return f"Task not found: #{task_id}"


@app.tool()
def get_progress_status() -> str:
    """Get overall progress status.

    Returns:
        Progress summary.
    """
    _load()
    if not _tasks:
        return "No tasks tracked yet."

    completed = len([t for t in _tasks if t["status"] == "completed"])
    total = len(_tasks)
    pending = total - completed

    percentage = (completed / total * 100) if total > 0 else 0

    status = f"""
Progress: {completed}/{total} tasks completed ({percentage:.0f}%)
Pending: {pending} tasks

Task List:
{list_tasks()}
"""

    return status

if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"

    logging.getLogger().setLevel(logging.ERROR)

    app.run(
        transport="stdio",
        show_banner=False,
        log_level="error"
    )
