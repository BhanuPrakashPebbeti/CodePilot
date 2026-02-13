"""Testing MCP server — run tests, verify outputs, validate projects.

Gives the agent the ability to VERIFY its own work: run test suites,
check if servers start, validate file syntax, and assert expected outputs.
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from fastmcp import FastMCP

app = FastMCP(name="testing")


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "message": message})


def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error})


def _execute(command: str, cwd: str = ".", timeout: int = 120) -> Dict[str, Any]:
    """Run command and return result dict."""
    try:
        result = subprocess.run(
            command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timed out after {timeout}s", "exit_code": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "success": False}


# ============================================================================
# TEST RUNNERS
# ============================================================================

@app.tool()
def run_pytest(directory: str = ".", args: str = "-v --tb=short", timeout: int = 120) -> str:
    """Run pytest test suite and return results.

    Args:
        directory: Project directory containing tests.
        args: Additional pytest arguments (default: "-v --tb=short").
        timeout: Timeout in seconds.

    Returns:
        JSON with test results (passed, failed, errors, output).
    """
    result = _execute(f"python -m pytest {args}", cwd=directory, timeout=timeout)

    # Parse pytest output for summary
    summary = _parse_pytest_output(result["stdout"])

    return _ok({
        **result,
        "summary": summary,
    })


def _parse_pytest_output(output: str) -> Dict[str, Any]:
    """Parse pytest output for test counts."""
    import re
    summary = {"passed": 0, "failed": 0, "errors": 0, "warnings": 0, "skipped": 0}

    # Look for the summary line like "5 passed, 2 failed, 1 error in 1.23s"
    match = re.search(r"(\d+) passed", output)
    if match:
        summary["passed"] = int(match.group(1))

    match = re.search(r"(\d+) failed", output)
    if match:
        summary["failed"] = int(match.group(1))

    match = re.search(r"(\d+) error", output)
    if match:
        summary["errors"] = int(match.group(1))

    match = re.search(r"(\d+) warning", output)
    if match:
        summary["warnings"] = int(match.group(1))

    match = re.search(r"(\d+) skipped", output)
    if match:
        summary["skipped"] = int(match.group(1))

    return summary


@app.tool()
def run_npm_test(directory: str = ".", timeout: int = 120) -> str:
    """Run npm test suite.

    Args:
        directory: Project directory with package.json.
        timeout: Timeout in seconds.

    Returns:
        JSON with test results.
    """
    result = _execute("npm test -- --watchAll=false 2>&1 || npm test", cwd=directory, timeout=timeout)
    return _ok(result)


@app.tool()
def run_single_test(test_file: str, cwd: str = ".", framework: str = "pytest") -> str:
    """Run a single test file.

    Args:
        test_file: Path to test file.
        framework: Test framework (pytest, jest, mocha, go).
        cwd: Working directory.

    Returns:
        JSON with test result.
    """
    commands = {
        "pytest": f"python -m pytest {test_file} -v --tb=short",
        "jest": f"npx jest {test_file} --verbose",
        "mocha": f"npx mocha {test_file}",
        "go": f"go test -v -run {test_file}",
        "cargo": f"cargo test {test_file} -- --nocapture",
    }

    cmd = commands.get(framework, commands["pytest"])
    result = _execute(cmd, cwd=cwd)
    return _ok(result)


# ============================================================================
# SYNTAX VALIDATION
# ============================================================================

@app.tool()
def check_python_syntax(file_path: str) -> str:
    """Check Python file for syntax errors without executing it.

    Args:
        file_path: Path to Python file.

    Returns:
        JSON with syntax check result (valid/invalid + errors).
    """
    result = _execute(f"python -m py_compile {file_path}")

    if result["success"]:
        return _ok({"file": file_path, "valid": True}, "Syntax OK")

    return _ok({
        "file": file_path,
        "valid": False,
        "errors": result["stderr"],
    }, "Syntax errors found")


@app.tool()
def check_json_syntax(file_path: str) -> str:
    """Validate JSON file syntax.

    Args:
        file_path: Path to JSON file.

    Returns:
        JSON with validation result.
    """
    try:
        fp = Path(file_path).resolve()
        if not fp.exists():
            return _err(f"File not found: {file_path}")

        content = fp.read_text(encoding="utf-8")
        json.loads(content)
        return _ok({"file": file_path, "valid": True}, "Valid JSON")
    except json.JSONDecodeError as e:
        return _ok({
            "file": file_path,
            "valid": False,
            "error": str(e),
            "line": e.lineno,
            "column": e.colno,
        }, "Invalid JSON")
    except Exception as e:
        return _err(str(e))


@app.tool()
def lint_python(file_path: str, cwd: str = ".") -> str:
    """Run Python linting on a file (uses ruff if available, falls back to flake8/pylint).

    Args:
        file_path: Path to Python file.
        cwd: Working directory.

    Returns:
        JSON with linting results.
    """
    # Try ruff first (fastest)
    result = _execute(f"python -m ruff check {file_path} --output-format=text", cwd=cwd)
    if result["exit_code"] != 127:  # 127 = command not found
        return _ok({
            "file": file_path,
            "linter": "ruff",
            "clean": result["success"],
            "output": result["stdout"] or result["stderr"],
        })

    # Fall back to flake8
    result = _execute(f"python -m flake8 {file_path}", cwd=cwd)
    if result["exit_code"] != 127:
        return _ok({
            "file": file_path,
            "linter": "flake8",
            "clean": result["success"],
            "output": result["stdout"] or result["stderr"],
        })

    # No linter available
    return _ok({
        "file": file_path,
        "linter": None,
        "message": "No linter available (install ruff or flake8)",
    })


# ============================================================================
# VERIFICATION TOOLS
# ============================================================================

@app.tool()
def verify_server_starts(command: str, cwd: str = ".", port: int = 0, wait_seconds: int = 5) -> str:
    """Start a server process, wait briefly, then check if it's still running.

    Useful for verifying FastAPI, Express, Flask servers start without crashing.

    Args:
        command: Command to start the server (e.g., "python app.py").
        cwd: Working directory.
        port: Optional port to check (0 = skip port check).
        wait_seconds: How long to wait before checking (default: 5).

    Returns:
        JSON with verification result.
    """
    import time
    import signal

    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, preexec_fn=os.setsid,
        )

        time.sleep(wait_seconds)

        # Check if process is still running
        poll = proc.poll()
        still_running = poll is None

        stdout = ""
        stderr = ""

        if still_running:
            # Server started successfully — kill it
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                stdout, stderr = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait()

            result_data = {
                "started": True,
                "command": command,
                "stdout": stdout[:500],
                "stderr": stderr[:500],
            }

            if port > 0:
                result_data["port"] = port

            return _ok(result_data, "Server started successfully")
        else:
            stdout, stderr = proc.communicate()
            return _ok({
                "started": False,
                "command": command,
                "exit_code": poll,
                "stdout": stdout[:1000],
                "stderr": stderr[:1000],
            }, "Server failed to start")

    except Exception as e:
        return _err(f"Verification failed: {str(e)}")


@app.tool()
def assert_file_contains(file_path: str, expected: str) -> str:
    """Assert that a file contains expected text.

    Useful for verification after writing files.

    Args:
        file_path: Path to file.
        expected: Text that should be present.

    Returns:
        JSON with assertion result (pass/fail).
    """
    try:
        fp = Path(file_path).resolve()
        if not fp.exists():
            return _ok({"passed": False, "reason": f"File not found: {file_path}"})

        content = fp.read_text(encoding="utf-8", errors="replace")
        found = expected in content

        return _ok({
            "passed": found,
            "file": file_path,
            "expected_text": expected[:100],
            "reason": "Text found in file" if found else "Expected text not found in file",
        })
    except Exception as e:
        return _err(str(e))


@app.tool()
def assert_file_exists(paths: str) -> str:
    """Assert that one or more files exist.

    Args:
        paths: Comma-separated file paths to check.

    Returns:
        JSON with assertion results for each file.
    """
    file_list = [p.strip() for p in paths.split(",") if p.strip()]
    results = []
    all_pass = True

    for path in file_list:
        exists = Path(path).resolve().exists()
        results.append({"file": path, "exists": exists})
        if not exists:
            all_pass = False

    return _ok({
        "all_exist": all_pass,
        "files": results,
        "checked": len(results),
    })


@app.tool()
def assert_command_succeeds(command: str, cwd: str = ".") -> str:
    """Assert that a command runs successfully (exit code 0).

    Args:
        command: Command to run.
        cwd: Working directory.

    Returns:
        JSON with assertion result.
    """
    result = _execute(command, cwd=cwd, timeout=60)
    return _ok({
        "passed": result["success"],
        "command": command,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"][:500],
        "stderr": result["stderr"][:500],
    })


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
