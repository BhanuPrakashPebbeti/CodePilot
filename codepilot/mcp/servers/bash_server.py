"""Execution MCP server — run commands, install packages, manage processes.

Structured JSON responses: {"ok": true/false, "data": ..., "error": ...}
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from fastmcp import FastMCP

app = FastMCP(name="bash")


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "message": message})


def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error})


def _execute(command: str, cwd: str = ".", timeout: int = 120) -> Dict[str, Any]:
    """Internal command executor. NOT a tool — safe to call from other tools."""
    try:
        cwd_path = Path(cwd).resolve()
        if not cwd_path.is_dir():
            return {"stdout": "", "stderr": f"Directory not found: {cwd}", "exit_code": -1, "success": False}

        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Command timed out after {timeout}s", "exit_code": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "success": False}


# ============================================================================
# COMMAND EXECUTION
# ============================================================================

@app.tool()
def run_command(command: str, cwd: str = ".", timeout: int = 120) -> str:
    """Run a shell command and return structured output.

    Args:
        command: Shell command to execute.
        cwd: Working directory (default: current directory).
        timeout: Timeout in seconds (default: 120).

    Returns:
        JSON with stdout, stderr, exit_code, success.
    """
    result = _execute(command, cwd, timeout)
    return _ok(result) if result["success"] else _ok(result, f"Command failed with exit code {result['exit_code']}")


@app.tool()
def run_python(code_or_file: str, cwd: str = ".", args: str = "") -> str:
    """Run Python code (inline string) or a Python file.

    If code_or_file ends with .py, runs it as a file.
    Otherwise, executes it as inline code via `python -c`.

    Args:
        code_or_file: Python file path or inline code string.
        cwd: Working directory.
        args: Additional command-line arguments.

    Returns:
        JSON with execution result.
    """
    if code_or_file.strip().endswith(".py"):
        cmd = f"python {code_or_file}"
    else:
        # Inline code — use -c flag
        escaped = code_or_file.replace("'", "'\"'\"'")
        cmd = f"python -c '{escaped}'"

    if args:
        cmd += f" {args}"

    result = _execute(cmd, cwd)
    return _ok(result)


# ============================================================================
# PACKAGE MANAGEMENT
# ============================================================================

@app.tool()
def pip_install(packages: str, cwd: str = ".") -> str:
    """Install Python packages via pip.

    Args:
        packages: Space-separated package names (e.g., "flask sqlalchemy pytest").
        cwd: Working directory.

    Returns:
        JSON with installation result.
    """
    result = _execute(f"pip install {packages}", cwd)
    return _ok(result, f"pip install {packages}")


@app.tool()
def npm_install(packages: str = "", cwd: str = ".", dev: bool = False) -> str:
    """Install npm packages. If packages is empty, runs `npm install`.

    Args:
        packages: Space-separated package names (empty = install from package.json).
        cwd: Working directory.
        dev: Install as devDependencies.

    Returns:
        JSON with installation result.
    """
    if packages.strip():
        flag = "--save-dev" if dev else ""
        cmd = f"npm install {flag} {packages}".strip()
    else:
        cmd = "npm install"

    result = _execute(cmd, cwd, timeout=180)
    return _ok(result, cmd)


@app.tool()
def npm_run(script: str, cwd: str = ".") -> str:
    """Run an npm script defined in package.json.

    Args:
        script: Script name (e.g., "build", "test", "dev").
        cwd: Working directory.

    Returns:
        JSON with execution result.
    """
    result = _execute(f"npm run {script}", cwd, timeout=180)
    return _ok(result, f"npm run {script}")


# ============================================================================
# ENVIRONMENT CHECKS
# ============================================================================

@app.tool()
def check_tools_available() -> str:
    """Check which common development tools are available on the system.

    Returns:
        JSON with availability of python, node, npm, git, docker, cargo, go.
    """
    tools = ["python", "python3", "node", "npm", "npx", "git", "docker", "cargo", "go", "java", "make"]
    available = {}

    for tool in tools:
        result = subprocess.run(
            f"which {tool}",
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # Get version
            version_result = subprocess.run(
                f"{tool} --version",
                shell=True,
                capture_output=True,
                text=True,
            )
            version = version_result.stdout.strip().split("\n")[0] if version_result.returncode == 0 else "installed"
            available[tool] = version
        else:
            available[tool] = None

    return _ok(available)


@app.tool()
def get_system_info() -> str:
    """Get system information (OS, Python version, working directory).

    Returns:
        JSON with system info.
    """
    import platform

    info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "cwd": os.getcwd(),
        "home": str(Path.home()),
    }

    return _ok(info)


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
