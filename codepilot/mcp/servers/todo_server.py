"""TODO MCP server for task tracking and planning."""
import logging
import os
from typing import List, Optional

from fastmcp import FastMCP

app = FastMCP(name="todo")

# In-memory storage for tasks during session
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
    _task_counter += 1

    task = {
        "id": _task_counter,
        "title": title,
        "description": description,
        "status": "pending",
    }

    _tasks.append(task)
    return f"Task added: #{task['id']} {title}"


@app.tool()
def complete_task(task_id: int) -> str:
    """Mark task as complete.

    Args:
        task_id: Task ID.

    Returns:
        Success message.
    """
    for task in _tasks:
        if task["id"] == task_id:
            task["status"] = "completed"
            return f"Task completed: #{task_id} {task['title']}"

    return f"Task not found: #{task_id}"


@app.tool()
def get_next_task() -> Optional[str]:
    """Get next pending task.

    Returns:
        Next task or None if all complete.
    """
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
    count = len(_tasks)
    _tasks.clear()
    _task_counter = 0
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
    for task in _tasks:
        if task["id"] == task_id:
            if title:
                task["title"] = title
            if description:
                task["description"] = description
            return f"Task updated: #{task_id}"

    return f"Task not found: #{task_id}"


@app.tool()
def get_progress_status() -> str:
    """Get overall progress status.

    Returns:
        Progress summary.
    """
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
