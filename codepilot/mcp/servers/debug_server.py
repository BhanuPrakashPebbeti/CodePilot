"""Debug MCP server — error analysis, log reading, process inspection.

Helps the agent diagnose problems when things go wrong: parse error
messages, read log files, analyze stack traces, and suggest fixes.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from fastmcp import FastMCP

app = FastMCP(name="debug")


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "message": message})


def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error})


# ============================================================================
# ERROR ANALYSIS
# ============================================================================

@app.tool()
def parse_error(error_text: str) -> str:
    """Parse an error message/stack trace and extract structured information.

    Works with Python tracebacks, Node.js errors, Java stack traces, etc.

    Args:
        error_text: The error text or stack trace to analyze.

    Returns:
        JSON with error type, message, file, line number, and suggestions.
    """
    result = {
        "error_type": None,
        "message": None,
        "file": None,
        "line": None,
        "suggestions": [],
    }

    # --- Python traceback ---
    py_tb = re.findall(r'File "([^"]+)", line (\d+)', error_text)
    if py_tb:
        last_file, last_line = py_tb[-1]
        result["file"] = last_file
        result["line"] = int(last_line)

        # Extract error type and message
        err_match = re.search(r"(\w+Error|\w+Exception|KeyError|TypeError|ValueError|ImportError|ModuleNotFoundError|AttributeError|NameError|IndexError|FileNotFoundError|SyntaxError|IndentationError|RuntimeError|OSError):\s*(.+)", error_text)
        if err_match:
            result["error_type"] = err_match.group(1)
            result["message"] = err_match.group(2).strip()

        # Generate suggestions
        if result["error_type"] == "ModuleNotFoundError":
            module = re.search(r"No module named '(\w+)'", error_text)
            if module:
                result["suggestions"].append(f"pip install {module.group(1)}")
        elif result["error_type"] == "ImportError":
            result["suggestions"].append("Check if the package is installed")
            result["suggestions"].append("Verify import path and spelling")
        elif result["error_type"] == "SyntaxError":
            result["suggestions"].append(f"Check syntax around line {result['line']} in {result['file']}")
        elif result["error_type"] == "IndentationError":
            result["suggestions"].append("Fix indentation (use consistent spaces)")
        elif result["error_type"] == "FileNotFoundError":
            result["suggestions"].append("Check file path and ensure file exists")
        elif result["error_type"] == "KeyError":
            result["suggestions"].append("Check dictionary key spelling")
            result["suggestions"].append("Use .get() with a default value")
        elif result["error_type"] == "TypeError":
            result["suggestions"].append("Check argument types and function signatures")
        elif result["error_type"] == "AttributeError":
            result["suggestions"].append("Check object type and available attributes")

    # --- Node.js error ---
    node_match = re.search(r"at\s+(?:\w+\s+)?\((.+?):(\d+):\d+\)", error_text)
    if not py_tb and node_match:
        result["file"] = node_match.group(1)
        result["line"] = int(node_match.group(2))

        err_match = re.search(r"(Error|TypeError|ReferenceError|SyntaxError|RangeError):\s*(.+)", error_text)
        if err_match:
            result["error_type"] = err_match.group(1)
            result["message"] = err_match.group(2).strip()

        if "Cannot find module" in error_text:
            module = re.search(r"Cannot find module '([^']+)'", error_text)
            if module:
                result["suggestions"].append(f"npm install {module.group(1)}")

    # --- Generic error extraction ---
    if not result["error_type"]:
        err_match = re.search(r"(?:error|Error|ERROR)[:\s]+(.+?)(?:\n|$)", error_text)
        if err_match:
            result["error_type"] = "Error"
            result["message"] = err_match.group(1).strip()

    return _ok(result)


@app.tool()
def read_log_tail(file_path: str, lines: int = 50) -> str:
    """Read the last N lines of a log file.

    Args:
        file_path: Path to log file.
        lines: Number of lines from the end.

    Returns:
        JSON with log content.
    """
    try:
        fp = Path(file_path).resolve()
        if not fp.exists():
            return _err(f"File not found: {file_path}")

        content = fp.read_text(encoding="utf-8", errors="replace")
        all_lines = content.splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return _ok({
            "file": file_path,
            "total_lines": len(all_lines),
            "showing": len(tail),
            "content": "\n".join(tail),
        })
    except Exception as e:
        return _err(str(e))


@app.tool()
def find_errors_in_output(output: str) -> str:
    """Scan command output for error patterns and extract them.

    Args:
        output: Command output text to scan.

    Returns:
        JSON with extracted errors (line number, severity, message).
    """
    errors = []
    lines = output.splitlines()

    error_patterns = [
        (r"(?i)^(error|fatal|critical)[:\s]+(.+)", "error"),
        (r"(?i)^(warning|warn)[:\s]+(.+)", "warning"),
        (r"(?i)failed", "error"),
        (r"Traceback \(most recent call last\)", "error"),
        (r"(\w+Error|\w+Exception):", "error"),
        (r"ERR!", "error"),
        (r"ENOENT|EACCES|ECONNREFUSED", "error"),
        (r"npm ERR!", "error"),
        (r"SyntaxError|TypeError|ReferenceError", "error"),
    ]

    for i, line in enumerate(lines, 1):
        for pattern, severity in error_patterns:
            if re.search(pattern, line):
                errors.append({
                    "line": i,
                    "severity": severity,
                    "text": line.strip()[:200],
                })
                break  # Only match first pattern per line

    return _ok({
        "errors_found": len(errors),
        "errors": errors[:50],  # Limit output
    })


@app.tool()
def check_port_in_use(port: int) -> str:
    """Check if a network port is currently in use.

    Args:
        port: Port number to check.

    Returns:
        JSON with port status.
    """
    try:
        result = subprocess.run(
            f"lsof -i :{port} -t",
            shell=True,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            return _ok({
                "port": port,
                "in_use": True,
                "pids": pids,
            }, f"Port {port} is in use by PID(s): {', '.join(pids)}")
        else:
            return _ok({
                "port": port,
                "in_use": False,
            }, f"Port {port} is available")
    except Exception as e:
        return _err(str(e))


@app.tool()
def diagnose_import_error(module_name: str, cwd: str = ".") -> str:
    """Diagnose why a Python import is failing.

    Checks if the module is installed, finds its location, and
    suggests fixes.

    Args:
        module_name: Module name that failed to import.
        cwd: Working directory.

    Returns:
        JSON with diagnosis.
    """
    diagnosis = {
        "module": module_name,
        "installed": False,
        "location": None,
        "suggestions": [],
    }

    # Check if installed
    try:
        result = subprocess.run(
            f"python -c \"import {module_name}; print({module_name}.__file__ if hasattr({module_name}, '__file__') else 'built-in')\"",
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
        )

        if result.returncode == 0:
            diagnosis["installed"] = True
            diagnosis["location"] = result.stdout.strip()
        else:
            diagnosis["suggestions"].append(f"pip install {module_name}")

            # Check if it's a common package with a different pip name
            pip_name_map = {
                "cv2": "opencv-python",
                "PIL": "Pillow",
                "sklearn": "scikit-learn",
                "yaml": "pyyaml",
                "bs4": "beautifulsoup4",
                "dotenv": "python-dotenv",
                "jwt": "pyjwt",
                "gi": "pygobject",
                "serial": "pyserial",
                "usb": "pyusb",
                "magic": "python-magic",
            }

            if module_name in pip_name_map:
                diagnosis["suggestions"] = [f"pip install {pip_name_map[module_name]}"]
                diagnosis["note"] = f"Module '{module_name}' is installed via '{pip_name_map[module_name]}'"

    except Exception as e:
        diagnosis["error"] = str(e)

    # Check pip list
    try:
        pip_result = subprocess.run(
            f"pip show {module_name}",
            shell=True, capture_output=True, text=True, cwd=cwd,
        )
        if pip_result.returncode == 0:
            diagnosis["pip_info"] = pip_result.stdout[:500]
    except Exception:
        pass

    return _ok(diagnosis)


@app.tool()
def diff_files(file1: str, file2: str) -> str:
    """Show differences between two files.

    Args:
        file1: Path to first file.
        file2: Path to second file.

    Returns:
        JSON with diff output.
    """
    try:
        p1 = Path(file1).resolve()
        p2 = Path(file2).resolve()

        if not p1.exists():
            return _err(f"File not found: {file1}")
        if not p2.exists():
            return _err(f"File not found: {file2}")

        result = subprocess.run(
            f"diff -u '{p1}' '{p2}'",
            shell=True, capture_output=True, text=True,
        )

        if result.returncode == 0:
            return _ok({"identical": True}, "Files are identical")
        else:
            return _ok({
                "identical": False,
                "diff": result.stdout[:3000],
            })
    except Exception as e:
        return _err(str(e))


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
