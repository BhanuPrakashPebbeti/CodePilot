"""Execution MCP server — run commands, install packages, manage processes.

Structured JSON responses: {"ok": true/false, "data": ..., "error": ...}

Tools:
  run_command             — Execute any shell command (blocking). Returns stdout, stderr, exit_code.
  run_python              — Run Python code (inline string) or a Python .py file.
  pip_install             — Install Python packages via pip.
  npm_install             — Install npm packages (or install from package.json).
  npm_run                 — Run an npm script defined in package.json.
  start_background_process — Start a long-running process (server) in the background.
  stop_background_process  — Stop a background process by PID or port.
  wait_for_port            — Wait until a TCP port is accepting connections.
  get_background_output    — Read recent log output from a background process.
  check_tools_available    — Check which dev tools are installed on the system.
  get_system_info          — Get OS, Python version, and working directory info.
"""

import json
import logging
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastmcp import FastMCP

app = FastMCP(name="bash")

# Sudo password cache — set by the agent process via environment variable
# before spawning the MCP server, or injected per-call.
_sudo_password: Optional[str] = None

# Background process registry — tracks server processes started by
# start_background_process so they can be monitored / stopped later.
_background_processes: Dict[int, Dict[str, Any]] = {}


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "message": message})


def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error})


def _execute(command: str, cwd: str = ".", timeout: int = 120) -> Dict[str, Any]:
    """Internal command executor. NOT a tool — safe to call from other tools.
    
    For commands requiring sudo, uses the cached password from the agent
    process (passed via CODEPILOT_SUDO_PW env var).
    """
    try:
        cwd_path = Path(cwd).resolve()
        if not cwd_path.is_dir():
            return {"stdout": "", "stderr": f"Directory not found: {cwd}", "exit_code": -1, "success": False}

        needs_sudo = command.strip().startswith("sudo ")

        if needs_sudo:
            return _execute_with_sudo(command, cwd_path, timeout)

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


def _execute_with_sudo(command: str, cwd_path: Path, timeout: int = 120) -> Dict[str, Any]:
    """Execute a sudo command using the password from CODEPILOT_SUDO_PW env var.
    
    The agent process prompts the user for their password interactively
    and passes it to the MCP server subprocess via environment variable.
    """
    password = os.environ.get("CODEPILOT_SUDO_PW", "")
    if not password:
        return {
            "stdout": "",
            "stderr": (
                "Sudo password not available. The agent should prompt the "
                "user for their password before running sudo commands."
            ),
            "exit_code": -1,
            "success": False,
        }

    try:
        # Replace 'sudo' with 'sudo -S' so it reads password from stdin
        if "sudo -S" not in command:
            command = command.replace("sudo ", "sudo -S ", 1)

        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        stdout, stderr = proc.communicate(input=password + "\n", timeout=timeout)
        
        # Filter out the "[sudo] password for ..." line from stderr
        stderr_lines = [
            line for line in stderr.split("\n")
            if not line.strip().startswith("[sudo]")
        ]
        stderr_clean = "\n".join(stderr_lines).strip()

        return {
            "stdout": stdout,
            "stderr": stderr_clean,
            "exit_code": proc.returncode,
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return {"stdout": "", "stderr": f"Sudo command timed out after {timeout}s", "exit_code": -1, "success": False}
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
# BACKGROUND PROCESS MANAGEMENT
# ============================================================================

@app.tool()
def start_background_process(
    command: str,
    cwd: str = ".",
    log_file: str = "",
    wait_port: int = 0,
    wait_timeout: int = 15,
) -> str:
    """Start a long-running process in the background (e.g. a dev server).

    Use this instead of run_command for servers (uvicorn, npm start, etc.)
    that run indefinitely. The process keeps running after this call returns.

    Args:
        command:      Shell command to run (e.g. "uvicorn main:app --port 8000").
        cwd:          Working directory.
        log_file:     Optional file path to capture stdout+stderr.
                      If empty, a temp file is created automatically.
        wait_port:    If > 0, wait for this port to start accepting connections
                      before returning. This confirms the server is ready.
        wait_timeout: Seconds to wait for the port (default 15).

    Returns:
        JSON with pid, log_file, and port_ready status.
    """
    cwd_path = Path(cwd).resolve()
    if not cwd_path.is_dir():
        return _err(f"Directory not found: {cwd}")

    # Auto-generate log file if not provided
    if not log_file:
        import tempfile
        fd, log_file = tempfile.mkstemp(prefix="codepilot_bg_", suffix=".log")
        os.close(fd)

    log_path = Path(log_file).resolve()

    try:
        log_fh = open(log_path, "w")
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd_path),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,  # detach from parent
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        _background_processes[proc.pid] = {
            "pid": proc.pid,
            "command": command,
            "cwd": str(cwd_path),
            "log_file": str(log_path),
            "log_fh": log_fh,
            "process": proc,
        }

        result: Dict[str, Any] = {
            "pid": proc.pid,
            "log_file": str(log_path),
            "command": command,
            "started": True,
        }

        # Optionally wait for port readiness
        if wait_port > 0:
            ready = _wait_for_port(wait_port, wait_timeout)
            result["port"] = wait_port
            result["port_ready"] = ready
            if not ready:
                # Check if process died
                if proc.poll() is not None:
                    log_fh.close()
                    log_content = log_path.read_text()[-2000:]
                    result["started"] = False
                    result["exit_code"] = proc.returncode
                    result["log_tail"] = log_content
                    return _err(
                        f"Process exited with code {proc.returncode} "
                        f"before port {wait_port} became ready. "
                        f"Log tail: {log_content[-500:]}"
                    )
                return _ok(result,
                    f"Process started (PID {proc.pid}) but port {wait_port} "
                    f"not ready after {wait_timeout}s. It may still be starting.")

            return _ok(result,
                f"Server started (PID {proc.pid}), port {wait_port} is ready")

        # Give a moment and verify process is still alive
        time.sleep(0.5)
        if proc.poll() is not None:
            log_fh.close()
            log_content = log_path.read_text()[-2000:]
            result["started"] = False
            result["exit_code"] = proc.returncode
            result["log_tail"] = log_content
            return _ok(result,
                f"Process exited immediately with code {proc.returncode}")

        return _ok(result, f"Background process started (PID {proc.pid})")

    except Exception as e:
        return _err(f"Failed to start background process: {e}")


@app.tool()
def stop_background_process(pid: int = 0, port: int = 0) -> str:
    """Stop a background process started by start_background_process.

    Provide either the pid (returned by start_background_process) or
    the port number to find and kill the process listening on it.

    Args:
        pid:  Process ID to stop.
        port: Port number — finds and kills the process using this port.

    Returns:
        JSON with stopped status.
    """
    if not pid and not port:
        return _err("Provide either pid or port to stop a process.")

    pids_to_kill = []

    if pid:
        pids_to_kill.append(pid)

    if port:
        try:
            result = subprocess.run(
                f"lsof -i :{port} -t",
                shell=True, capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                for p in result.stdout.strip().split("\n"):
                    try:
                        pids_to_kill.append(int(p.strip()))
                    except ValueError:
                        pass
        except Exception:
            pass

    if not pids_to_kill:
        return _ok({"stopped": False}, f"No process found on port {port}")

    killed = []
    for p in set(pids_to_kill):
        try:
            os.killpg(os.getpgid(p), signal.SIGTERM)
            killed.append(p)
        except ProcessLookupError:
            killed.append(p)  # already dead
        except PermissionError:
            try:
                os.kill(p, signal.SIGTERM)
                killed.append(p)
            except Exception:
                pass
        except Exception:
            try:
                os.kill(p, signal.SIGTERM)
                killed.append(p)
            except Exception:
                pass

        # Clean up from registry
        entry = _background_processes.pop(p, None)
        if entry and entry.get("log_fh"):
            try:
                entry["log_fh"].close()
            except Exception:
                pass

    return _ok(
        {"stopped": True, "killed_pids": killed},
        f"Stopped process(es): {killed}"
    )


@app.tool()
def wait_for_port(port: int, timeout: int = 15) -> str:
    """Wait until a port starts accepting TCP connections.

    Use this after start_background_process to confirm a server is ready,
    or before making HTTP requests to a server.

    Args:
        port:    Port number to wait for.
        timeout: Max seconds to wait (default 15).

    Returns:
        JSON with ready status and elapsed time.
    """
    ready = _wait_for_port(port, timeout)
    if ready:
        return _ok(
            {"port": port, "ready": True},
            f"Port {port} is accepting connections"
        )
    return _ok(
        {"port": port, "ready": False},
        f"Port {port} not ready after {timeout}s"
    )


@app.tool()
def get_background_output(pid: int = 0, log_file: str = "", tail: int = 50) -> str:
    """Read recent output from a background process.

    Provide either the pid (to look up its log file) or a direct log_file path.

    Args:
        pid:      PID of the background process.
        log_file: Direct path to the log file.
        tail:     Number of lines to return from the end (default 50).

    Returns:
        JSON with log tail, process alive status, etc.
    """
    path = None
    if log_file:
        path = Path(log_file)
    elif pid and pid in _background_processes:
        path = Path(_background_processes[pid]["log_file"])
    else:
        return _err(
            f"No log file found for PID {pid}. "
            f"Provide log_file path directly, or use a PID from start_background_process."
        )

    if not path or not path.exists():
        return _err(f"Log file not found: {path}")

    try:
        lines = path.read_text().split("\n")
        tail_lines = lines[-tail:] if len(lines) > tail else lines
        content = "\n".join(tail_lines)

        alive = True
        if pid and pid in _background_processes:
            proc = _background_processes[pid]["process"]
            alive = proc.poll() is None

        return _ok({
            "log_file": str(path),
            "pid": pid or None,
            "alive": alive,
            "total_lines": len(lines),
            "tail": content,
        }, f"Last {min(tail, len(lines))} lines from {path.name}")
    except Exception as e:
        return _err(f"Failed to read log: {e}")


def _wait_for_port(port: int, timeout: int = 15) -> bool:
    """Internal helper — poll a TCP port until it accepts connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


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
