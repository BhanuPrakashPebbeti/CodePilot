"""Local filesystem tools — replaces filesystem_server.py MCP.

Running these as ADK FunctionTools (direct Python calls in the agent process)
eliminates the per-call MCP subprocess spawn overhead and gives agents
synchronous, low-latency file access.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from ...utils.logger import get_logger

logger = get_logger(__name__)


def _project_root(tool_context: Optional[ToolContext] = None) -> Path:
    if tool_context:
        d = tool_context.state.get("project_dir")
        if d:
            return Path(d).resolve()
    return Path(os.environ.get("CODEPILOT_PROJECT_DIR", ".")).resolve()


def _resolve(path: str, tool_context: Optional[ToolContext] = None) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    return (_project_root(tool_context) / p).resolve()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_file(path: str, tool_context: ToolContext) -> dict:
    """Read the full contents of a file.

    Args:
        path: File path (absolute or relative to project root).

    Returns:
        dict with ok, content, and size_bytes keys.
    """
    try:
        p = _resolve(path, tool_context)
        content = p.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "content": content, "size_bytes": len(content)}
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def read_lines(path: str, start: int, end: int, tool_context: ToolContext) -> dict:
    """Read a range of lines from a file (1-indexed, inclusive).

    Args:
        path: File path.
        start: First line to read (1-indexed).
        end: Last line to read (inclusive).

    Returns:
        dict with ok, lines (list), and line_count keys.
    """
    try:
        p = _resolve(path, tool_context)
        all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = all_lines[start - 1 : end]
        return {"ok": True, "lines": selected, "line_count": len(all_lines)}
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Write / Edit
# ---------------------------------------------------------------------------

def write_file(path: str, content: str, tool_context: ToolContext) -> dict:
    """Write (overwrite) a file with the given content.

    Creates parent directories automatically. Use this for new files
    or full rewrites. For targeted edits use replace_in_file.

    Args:
        path: Destination file path.
        content: Full file content to write.

    Returns:
        dict with ok and bytes_written keys.
    """
    try:
        p = _resolve(path, tool_context)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        logger.debug("write_file: %s (%d bytes)", p, len(content))
        return {"ok": True, "path": str(p), "bytes_written": len(content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def append_file(path: str, content: str, tool_context: ToolContext) -> dict:
    """Append content to an existing file (creates it if missing).

    Args:
        path: File path.
        content: Text to append.

    Returns:
        dict with ok key.
    """
    try:
        p = _resolve(path, tool_context)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def replace_in_file(
    path: str,
    old_string: str,
    new_string: str,
    tool_context: ToolContext,
) -> dict:
    """Replace the first occurrence of old_string with new_string in a file.

    Use this for surgical edits to existing files instead of rewriting the
    whole file. The old_string must match exactly (including whitespace).

    Args:
        path: File path.
        old_string: Exact text to find and replace.
        new_string: Replacement text.

    Returns:
        dict with ok and replaced (bool) keys.
    """
    try:
        p = _resolve(path, tool_context)
        original = p.read_text(encoding="utf-8")
        if old_string not in original:
            return {
                "ok": False,
                "error": f"String not found in {path}. Check exact whitespace/indentation.",
                "replaced": False,
            }
        updated = original.replace(old_string, new_string, 1)
        p.write_text(updated, encoding="utf-8")
        return {"ok": True, "replaced": True}
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {path}", "replaced": False}
    except Exception as e:
        return {"ok": False, "error": str(e), "replaced": False}


def edit_lines(
    path: str,
    start: int,
    end: int,
    new_content: str,
    tool_context: ToolContext,
) -> dict:
    """Replace a range of lines in a file.

    Use for block replacements (5+ lines). For smaller changes use
    replace_in_file instead.

    Args:
        path: File path.
        start: First line to replace (1-indexed).
        end: Last line to replace (inclusive).
        new_content: Replacement text (may span multiple lines).

    Returns:
        dict with ok key.
    """
    try:
        p = _resolve(path, tool_context)
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        replacement_lines = new_content.splitlines(keepends=True)
        if not replacement_lines or not replacement_lines[-1].endswith("\n"):
            replacement_lines = [l + "\n" for l in new_content.splitlines()]
        new_lines = lines[: start - 1] + replacement_lines + lines[end:]
        p.write_text("".join(new_lines), encoding="utf-8")
        return {"ok": True}
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Directory / File operations
# ---------------------------------------------------------------------------

def create_directory(path: str, tool_context: ToolContext) -> dict:
    """Create a directory (and any missing parents).

    Args:
        path: Directory path to create.

    Returns:
        dict with ok key.
    """
    try:
        _resolve(path, tool_context).mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_directory(path: str, tool_context: ToolContext) -> dict:
    """List files and directories inside a directory.

    Args:
        path: Directory to list (relative to project root or absolute).

    Returns:
        dict with ok, entries (list of {name, type, size}).
    """
    try:
        p = _resolve(path, tool_context)
        entries = []
        for item in sorted(p.iterdir()):
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"ok": True, "path": str(p), "entries": entries}
    except FileNotFoundError:
        return {"ok": False, "error": f"Directory not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_file(path: str, tool_context: ToolContext) -> dict:
    """Delete a file or empty directory.

    Args:
        path: Path to delete.

    Returns:
        dict with ok key.
    """
    try:
        p = _resolve(path, tool_context)
        if p.is_dir():
            p.rmdir()
        else:
            p.unlink()
        return {"ok": True}
    except FileNotFoundError:
        return {"ok": False, "error": f"Not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def move_file(src: str, dst: str, tool_context: ToolContext) -> dict:
    """Move or rename a file.

    Args:
        src: Source path.
        dst: Destination path.

    Returns:
        dict with ok key.
    """
    try:
        s = _resolve(src, tool_context)
        d = _resolve(dst, tool_context)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def copy_file(src: str, dst: str, tool_context: ToolContext) -> dict:
    """Copy a file.

    Args:
        src: Source path.
        dst: Destination path.

    Returns:
        dict with ok key.
    """
    try:
        s = _resolve(src, tool_context)
        d = _resolve(dst, tool_context)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(s), str(d))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def file_exists(path: str, tool_context: ToolContext) -> dict:
    """Check whether a file or directory exists.

    Args:
        path: Path to check.

    Returns:
        dict with ok and exists (bool) keys.
    """
    p = _resolve(path, tool_context)
    return {"ok": True, "exists": p.exists(), "is_file": p.is_file(), "is_dir": p.is_dir()}


def search_in_file(path: str, pattern: str, tool_context: ToolContext) -> dict:
    """Search for a regex pattern in a file. Returns matching lines with numbers.

    Args:
        path: File to search.
        pattern: Python regex pattern.

    Returns:
        dict with ok and matches (list of {line_no, line}) keys.
    """
    try:
        p = _resolve(path, tool_context)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        regex = re.compile(pattern)
        matches = [
            {"line_no": i + 1, "line": line}
            for i, line in enumerate(lines)
            if regex.search(line)
        ]
        return {"ok": True, "matches": matches, "count": len(matches)}
    except re.error as e:
        return {"ok": False, "error": f"Invalid regex: {e}"}
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
