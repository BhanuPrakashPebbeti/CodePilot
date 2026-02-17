"""Filesystem MCP server — production-grade file operations.

Every tool returns a JSON string with a consistent schema:
  {"ok": true/false, "data": ..., "error": ...}

This lets the agent reliably parse results and chain operations.
"""

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

app = FastMCP(name="filesystem")


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    """Return a success JSON response."""
    return json.dumps({"ok": True, "data": data, "message": message})


def _err(error: str) -> str:
    """Return an error JSON response."""
    return json.dumps({"ok": False, "error": error})


def _resolve(path: str) -> Path:
    """Resolve path (supports relative and absolute)."""
    return Path(path).resolve()


# ============================================================================
# READ OPERATIONS
# ============================================================================

@app.tool()
def read_file(path: str) -> str:
    """Read entire file content.

    Args:
        path: Path to file (relative or absolute).

    Returns:
        JSON with file content, line count, and size.
    """
    try:
        fp = _resolve(path)
        if not fp.exists():
            return _err(f"File not found: {path}")
        if fp.is_dir():
            return _err(f"Path is a directory: {path}")

        content = fp.read_text(encoding="utf-8", errors="replace")
        return _ok({
            "path": str(fp),
            "content": content,
            "lines": len(content.splitlines()),
            "size": fp.stat().st_size,
        })
    except Exception as e:
        return _err(str(e))


@app.tool()
def read_lines(path: str, start: int, end: int) -> str:
    """Read specific line range from file (1-indexed, inclusive).

    Args:
        path: Path to file.
        start: Start line number (1-indexed).
        end: End line number (1-indexed, inclusive).

    Returns:
        JSON with the requested lines.
    """
    try:
        fp = _resolve(path)
        if not fp.exists():
            return _err(f"File not found: {path}")

        all_lines = fp.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        start_idx = max(0, start - 1)
        end_idx = min(len(all_lines), end)
        selected = all_lines[start_idx:end_idx]

        return _ok({
            "path": str(fp),
            "start": start,
            "end": min(end, len(all_lines)),
            "total_lines": len(all_lines),
            "content": "".join(selected),
        })
    except Exception as e:
        return _err(str(e))


# ============================================================================
# WRITE OPERATIONS
# ============================================================================

@app.tool()
def write_file(path: str, content: str) -> str:
    """Write content to file (creates parent dirs, overwrites if exists).

    Args:
        path: Path to file.
        content: Full file content.

    Returns:
        JSON with result.
    """
    try:
        fp = _resolve(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return _ok({
            "path": str(fp),
            "lines": len(content.splitlines()),
            "size": fp.stat().st_size,
        }, f"File written: {path}")
    except Exception as e:
        return _err(str(e))


@app.tool()
def append_file(path: str, content: str) -> str:
    """Append content to end of file.

    Args:
        path: Path to file.
        content: Content to append.

    Returns:
        JSON with result.
    """
    try:
        fp = _resolve(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "a", encoding="utf-8") as f:
            f.write(content)
        return _ok({"path": str(fp)}, f"Appended to: {path}")
    except Exception as e:
        return _err(str(e))


# ============================================================================
# EDIT OPERATIONS
# ============================================================================

@app.tool()
def replace_in_file(path: str, search: str, replace: str) -> str:
    """Find and replace text in file. Returns count of replacements.

    Args:
        path: Path to file.
        search: Exact text to find.
        replace: Text to replace with.

    Returns:
        JSON with replacement count.
    """
    try:
        fp = _resolve(path)
        if not fp.exists():
            return _err(f"File not found: {path}")

        content = fp.read_text(encoding="utf-8")
        count = content.count(search)

        if count == 0:
            return _err(f"Search text not found in {path}")

        new_content = content.replace(search, replace)
        fp.write_text(new_content, encoding="utf-8")

        return _ok({
            "path": str(fp),
            "replacements": count,
        }, f"Replaced {count} occurrence(s)")
    except Exception as e:
        return _err(str(e))


@app.tool()
def edit_lines(path: str, start_line: int, end_line: int, new_content: str) -> str:
    """Replace a range of lines with new content.

    Args:
        path: Path to file.
        start_line: First line to replace (1-indexed).
        end_line: Last line to replace (1-indexed, inclusive).
        new_content: New content to insert in place of the range.

    Returns:
        JSON with result.
    """
    try:
        fp = _resolve(path)
        if not fp.exists():
            return _err(f"File not found: {path}")

        lines = fp.read_text(encoding="utf-8").splitlines(keepends=True)
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)

        new_lines = new_content.split("\n")
        new_lines = [line + "\n" for line in new_lines[:-1]] + [new_lines[-1]]

        result_lines = lines[:start_idx] + new_lines + lines[end_idx:]
        fp.write_text("".join(result_lines), encoding="utf-8")

        return _ok({
            "path": str(fp),
            "lines_replaced": end_idx - start_idx,
            "new_line_count": len(new_lines),
            "total_lines": len(result_lines),
        }, f"Lines {start_line}-{end_line} replaced")
    except Exception as e:
        return _err(str(e))


# ============================================================================
# DIRECTORY OPERATIONS
# ============================================================================

@app.tool()
def create_directory(path: str) -> str:
    """Create directory (and all parents).

    Args:
        path: Directory path.

    Returns:
        JSON with result.
    """
    try:
        fp = _resolve(path)
        fp.mkdir(parents=True, exist_ok=True)
        return _ok({"path": str(fp)}, f"Directory created: {path}")
    except Exception as e:
        return _err(str(e))


@app.tool()
def list_directory(path: str = ".") -> str:
    """List directory contents with type info.

    Args:
        path: Directory path.

    Returns:
        JSON with list of entries (name, type, size).
    """
    try:
        dp = _resolve(path)
        if not dp.is_dir():
            return _err(f"Not a directory: {path}")

        entries = []
        for item in sorted(dp.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            entry = {"name": item.name, "type": "dir" if item.is_dir() else "file"}
            if item.is_file():
                entry["size"] = item.stat().st_size
                entry["extension"] = item.suffix
            entries.append(entry)

        return _ok({"path": str(dp), "count": len(entries), "entries": entries})
    except Exception as e:
        return _err(str(e))


# ============================================================================
# FILE MANAGEMENT
# ============================================================================

@app.tool()
def delete_file(path: str) -> str:
    """Delete a file or directory.

    Automatically detects whether the path is a file or directory
    and removes it accordingly. Directories are removed recursively.

    Args:
        path: Path to file or directory.

    Returns:
        JSON with result.
    """
    try:
        fp = _resolve(path)
        if not fp.exists():
            return _err(f"Path not found: {path}")
        if fp.is_dir():
            shutil.rmtree(fp)
            return _ok({"path": str(fp)}, f"Directory deleted: {path}")
        fp.unlink()
        return _ok({"path": str(fp)}, f"File deleted: {path}")
    except Exception as e:
        return _err(str(e))


@app.tool()
def move_file(source: str, destination: str) -> str:
    """Move or rename a file.

    Args:
        source: Source path.
        destination: Destination path.

    Returns:
        JSON with result.
    """
    try:
        src = _resolve(source)
        dst = _resolve(destination)
        if not src.exists():
            return _err(f"Source not found: {source}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return _ok({"source": str(src), "destination": str(dst)})
    except Exception as e:
        return _err(str(e))


@app.tool()
def copy_file(source: str, destination: str) -> str:
    """Copy a file.

    Args:
        source: Source path.
        destination: Destination path.

    Returns:
        JSON with result.
    """
    try:
        src = _resolve(source)
        dst = _resolve(destination)
        if not src.exists():
            return _err(f"Source not found: {source}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return _ok({"source": str(src), "destination": str(dst)})
    except Exception as e:
        return _err(str(e))


@app.tool()
def file_exists(path: str) -> str:
    """Check if a path exists and whether it is a file or directory.

    Args:
        path: Path to check.

    Returns:
        JSON with existence info.
    """
    fp = _resolve(path)
    return _ok({"path": str(fp), "exists": fp.exists(), "is_file": fp.is_file(), "is_dir": fp.is_dir()})


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
