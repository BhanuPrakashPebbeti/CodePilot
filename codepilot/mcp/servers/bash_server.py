"""Execution MCP server — run commands and manage background processes.

Structured JSON responses: {"ok": true/false, "data": ..., "error": ...}

Auto-discovers runtimes installed via version managers (nvm, rustup, etc.)
so that all commands can find node, npm, cargo, etc. without manual PATH setup.

Tools:
  run_command              — Execute any shell command (blocking). Returns stdout, stderr, exit_code.
  run_script               — Run a script file (.py, .js, .ts, .rb, .sh, .go, etc.) with auto-detected interpreter.
  start_background_process — Start a long-running process (server) in the background.
  stop_background_process  — Stop a background process by PID or port.
  wait_for_port            — Wait until a TCP port is accepting connections.
  get_background_output    — Read recent log output from a background process.
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

from codepilot.mcp.servers._env import get_clean_env

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


def _get_env() -> Dict[str, str]:
    """Delegate to shared env isolation (see ``_env.py``)."""
    return get_clean_env()


def _resolve_cwd(cwd: str) -> Path:
    """Resolve cwd — relative paths use CODEPILOT_PROJECT_DIR as base.

    Same belt-and-suspenders approach as filesystem_server._resolve:
    even when the subprocess already has the right CWD, we explicitly
    resolve relative directories against the project root.
    """
    p = Path(cwd)
    if p.is_absolute():
        return p.resolve()
    project_dir = os.environ.get("CODEPILOT_PROJECT_DIR")
    if project_dir:
        return (Path(project_dir) / p).resolve()
    return p.resolve()


def _execute(command: str, cwd: str = ".", timeout: int = 120) -> Dict[str, Any]:
    """Internal command executor. NOT a tool — safe to call from other tools.
    
    For commands requiring sudo, uses the cached password from the agent
    process (passed via CODEPILOT_SUDO_PW env var).
    """
    try:
        cwd_path = _resolve_cwd(cwd)
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
            env=_get_env(),
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
            env=_get_env(),
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
    """Execute a shell command (blocking) and return stdout/stderr.

    Use for: installing packages, running builds, checking versions,
    listing files, and any short-lived command.
    Do NOT use for: writing source code files (use write_file instead),
    or starting long-running servers (use start_background_process).

    Args:
        command: Shell command to execute (e.g. "npm install", "make").
        cwd: Working directory (default: project root).
        timeout: Max seconds to wait (default: 120).

    Returns:
        JSON with stdout, stderr, exit_code, and success boolean.
    """
    result = _execute(command, cwd, timeout)

    if result["success"]:
        return _ok(result)

    # ── Command FAILED — return a clear error with diagnosis guidance ──
    # Truncate very long stderr to avoid token bloat, but keep enough
    # for the agent to diagnose the root cause.
    stderr = (result.get("stderr") or "").strip()
    if len(stderr) > 3000:
        stderr = stderr[:1500] + "\n\n... (truncated) ...\n\n" + stderr[-1500:]

    return _err(
        f"Command FAILED (exit code {result['exit_code']})\n"
        f"Command: {command}\n"
        f"stderr:\n{stderr}\n"
        f"stdout:\n{(result.get('stdout') or '')[:1500]}\n\n"
        "ACTION REQUIRED: Read the error above. Diagnose the root "
        "cause BEFORE retrying. If a package version does not exist, "
        "fix the version. If a dependency is missing, install it. "
        "Do NOT retry the same command blindly."
    )


@app.tool()
def run_script(file_path: str, cwd: str = ".", args: str = "") -> str:
    """Run a script file using the appropriate interpreter.

    Automatically detects the language from the file extension and uses
    the correct interpreter (python, node, ruby, bash, etc.).

    Supported extensions:
      .py  → python
      .js  → node
      .ts  → npx ts-node (or tsx)
      .rb  → ruby
      .sh  → bash
      .pl  → perl
      .lua → lua
      .go  → go run

    Args:
        file_path: Path to the script file to execute.
        cwd: Working directory.
        args: Additional command-line arguments.

    Returns:
        JSON with execution result.
    """
    file_path = file_path.strip()
    ext = Path(file_path).suffix.lower()

    interpreter_map = {
        ".py": "python",
        ".js": "node",
        ".ts": "npx ts-node",
        ".rb": "ruby",
        ".sh": "bash",
        ".pl": "perl",
        ".lua": "lua",
        ".go": "go run",
    }

    interpreter = interpreter_map.get(ext)
    if not interpreter:
        return _err(f"Unsupported file extension '{ext}'. Use run_command for custom interpreters.")

    cmd = f"{interpreter} {file_path}"
    if args:
        cmd += f" {args}"

    result = _execute(cmd, cwd)

    if result["success"]:
        return _ok(result)

    stderr = (result.get("stderr") or "").strip()
    if len(stderr) > 3000:
        stderr = stderr[:1500] + "\n\n... (truncated) ...\n\n" + stderr[-1500:]

    return _err(
        f"Script FAILED (exit code {result['exit_code']})\n"
        f"Script: {file_path}\n"
        f"stderr:\n{stderr}\n"
        f"stdout:\n{(result.get('stdout') or '')[:1500]}\n\n"
        "Read the error output above. Diagnose before retrying."
    )


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

    Use this instead of run_command for servers, watchers, daemons, or any
    process that runs indefinitely. The process keeps running after this
    function returns. Call stop_background_process to terminate it later.

    IMPORTANT: Before starting a server, kill any stale process on the same
    port using stop_background_process(port=PORT).

    Args:
        command:      Shell command to run (e.g. "npm run dev", "python app.py").
        cwd:          Working directory (default: project root).
        log_file:     Optional file path to capture stdout+stderr.
                      If empty, a temp file is created automatically.
        wait_port:    If > 0, wait for this TCP port to accept connections
                      before returning. This confirms the server is ready.
        wait_timeout: Seconds to wait for the port (default 15).

    Returns:
        JSON with pid, log_file, port_ready status. If the process dies
        immediately, returns the log tail for debugging.
    """
    cwd_path = _resolve_cwd(cwd)
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
            env=_get_env(),
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
                env=_get_env(),
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


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
