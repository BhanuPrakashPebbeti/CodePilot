"""Local debug tools — replaces debug_server.py MCP."""

import re
from pathlib import Path
from typing import Optional

from google.adk.tools.tool_context import ToolContext


_ERROR_PATTERNS = {
    "ModuleNotFoundError": "Missing import — add to requirements.txt/package.json and install",
    "ImportError": "Bad import path or circular import — check module structure",
    "SyntaxError": "Python syntax error — fix the highlighted line",
    "IndentationError": "Incorrect indentation — use consistent spaces/tabs",
    "TypeError": "Wrong type passed to function — check argument types",
    "AttributeError": "Accessing non-existent attribute — check variable name",
    "ConnectionRefusedError": "Server not running or wrong port — check start command",
    "ENOENT": "File or directory not found — check path",
    "EADDRINUSE": "Port already in use — kill the existing process on that port",
    "npm ERR": "npm install/run failed — check package.json and node_modules",
    "ERROR in": "TypeScript/webpack build error — check type annotations",
    "cannot find module": "Missing npm package — add to package.json and npm install",
}


def parse_error(error_text: str, tool_context: ToolContext) -> dict:
    """Extract structured information from an error message.

    Returns file, line number, error type, and suggested fix.

    Args:
        error_text: Raw error output (stack trace, stderr, etc.).

    Returns:
        dict with ok, error_type, file, line_no, message, suggestion.
    """
    result = {
        "ok": True,
        "error_type": "unknown",
        "file": None,
        "line_no": None,
        "message": error_text[:500],
        "suggestion": "Read the full error message and trace to the root cause",
    }

    # Python traceback — file and line
    py_match = re.search(r'File "([^"]+)", line (\d+)', error_text)
    if py_match:
        result["file"] = py_match.group(1)
        result["line_no"] = int(py_match.group(2))

    # Node/TypeScript — file:line:col
    node_match = re.search(r'at .+ \((.+):(\d+):\d+\)', error_text)
    if node_match and not result["file"]:
        result["file"] = node_match.group(1)
        result["line_no"] = int(node_match.group(2))

    # Detect error type
    for pattern, suggestion in _ERROR_PATTERNS.items():
        if pattern in error_text:
            result["error_type"] = pattern
            result["suggestion"] = suggestion
            break

    # Extract the first "Error: ..." line
    for line in error_text.splitlines():
        if re.match(r"(Error|Exception|Warning|FAILED):", line.strip()):
            result["message"] = line.strip()
            break

    return result


def read_log_tail(path: str, tool_context: ToolContext, lines: int = 100) -> dict:
    """Read the last N lines of a log file.

    Args:
        path: Log file path.
        lines: Number of lines from the end (default 100).

    Returns:
        dict with ok and content.
    """
    try:
        from .exec import _resolve_cwd
        p = _resolve_cwd(path, tool_context)
        all_lines = Path(p).read_text(errors="replace").splitlines()
        tail = "\n".join(all_lines[-lines:])
        return {"ok": True, "content": tail, "total_lines": len(all_lines)}
    except FileNotFoundError:
        return {"ok": False, "error": f"Log file not found: {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def find_errors_in_output(output: str, tool_context: ToolContext) -> dict:
    """Scan command output for error patterns and return structured findings.

    Args:
        output: Raw stdout/stderr text to scan.

    Returns:
        dict with ok and errors (list of {line, pattern, suggestion}).
    """
    errors = []
    for line in output.splitlines():
        for pattern, suggestion in _ERROR_PATTERNS.items():
            if pattern.lower() in line.lower():
                errors.append({"line": line.strip(), "pattern": pattern, "suggestion": suggestion})
                break
    return {"ok": True, "errors": errors[:20], "count": len(errors)}
