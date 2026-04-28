"""Local execution tools — replaces bash_server.py MCP.

Background process registry lives in the agent process (not a subprocess),
so process handles survive across tool calls without serialization overhead.
"""

import os
import shlex
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from google.adk.tools.tool_context import ToolContext

from ...utils.logger import get_logger

logger = get_logger(__name__)

# PID → process info (shared across all tool calls in the runner process)
_bg_procs: Dict[int, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_root(tool_context: Optional[ToolContext] = None) -> Path:
    if tool_context:
        d = tool_context.state.get("project_dir")
        if d:
            return Path(d).resolve()
    return Path(os.environ.get("CODEPILOT_PROJECT_DIR", ".")).resolve()


def _resolve_cwd(cwd: str, tool_context: Optional[ToolContext] = None) -> Path:
    p = Path(cwd)
    if p.is_absolute():
        return p.resolve()
    return (_project_root(tool_context) / p).resolve()


def _clean_env() -> Dict[str, str]:
    """Inherit current environment but strip CodePilot's own venv.

    This prevents the agent's venv from leaking into project subprocesses —
    which would mask missing project dependencies and corrupt installs.
    """
    env = os.environ.copy()
    # Strip codepilot venv from PATH
    codepilot_venv = os.environ.get("VIRTUAL_ENV", "")
    if codepilot_venv:
        paths = env.get("PATH", "").split(os.pathsep)
        paths = [p for p in paths if not p.startswith(codepilot_venv)]
        env["PATH"] = os.pathsep.join(paths)
        env.pop("VIRTUAL_ENV", None)
        env.pop("VIRTUAL_ENV_PROMPT", None)
    # Add version manager paths
    home = Path.home()
    extra = []
    for candidate in [
        home / ".nvm" / "versions" / "node",
        home / ".cargo" / "bin",
        home / "go" / "bin",
        Path("/usr/local/go/bin"),
    ]:
        if candidate.exists():
            if candidate.is_dir():
                # nvm: find the active version
                for v in sorted(candidate.iterdir(), reverse=True):
                    bin_dir = v / "bin"
                    if bin_dir.is_dir():
                        extra.append(str(bin_dir))
                        break
            else:
                extra.append(str(candidate))
    if extra:
        env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    return env


def _run(command: str, cwd: str, timeout: int, tool_context: Optional[ToolContext]) -> Dict[str, Any]:
    cwd_path = _resolve_cwd(cwd, tool_context)
    if not cwd_path.is_dir():
        return {"stdout": "", "stderr": f"Directory not found: {cwd}", "exit_code": -1, "ok": False}
    try:
        result = subprocess.run(
            command, shell=True, cwd=str(cwd_path),
            capture_output=True, text=True, timeout=timeout, env=_clean_env(),
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"Timed out after {timeout}s", "exit_code": -1}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "exit_code": -1}


def _wait_port(port: int, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.4)
    return False


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def run_command(
    command: str,
    tool_context: ToolContext,
    cwd: str = ".",
    timeout: int = 120,
) -> dict:
    """Execute a shell command and return stdout/stderr/exit_code.

    Use for short-lived commands: installing packages, running builds,
    checking versions. Do NOT use for servers — use start_background_process.

    Args:
        command: Shell command (e.g. "npm install", "python -m pytest").
        cwd: Working directory (default: project root).
        timeout: Max seconds to wait (default 120).

    Returns:
        dict with ok, stdout, stderr, exit_code.
    """
    result = _run(command, cwd, timeout, tool_context)
    if not result["ok"]:
        # Truncate large output to avoid token waste
        stderr = (result.get("stderr") or "").strip()
        if len(stderr) > 2000:
            stderr = stderr[:1000] + "\n...(truncated)...\n" + stderr[-1000:]
        result["stderr"] = stderr
        result["hint"] = (
            "Command failed. Read the error above carefully. "
            "Diagnose the root cause BEFORE retrying. "
            "Never retry with identical arguments."
        )
    return result


def run_script(
    file_path: str,
    tool_context: ToolContext,
    cwd: str = ".",
    args: str = "",
) -> dict:
    """Run a script file using its auto-detected interpreter.

    Supports: .py, .js, .ts, .rb, .sh, .go, .pl

    Args:
        file_path: Path to the script.
        cwd: Working directory.
        args: Additional CLI arguments.

    Returns:
        dict with ok, stdout, stderr, exit_code.
    """
    ext = Path(file_path).suffix.lower()
    interp = {".py": "python", ".js": "node", ".ts": "npx ts-node",
               ".rb": "ruby", ".sh": "bash", ".go": "go run", ".pl": "perl"}.get(ext)
    if not interp:
        return {"ok": False, "error": f"Unsupported extension '{ext}'"}
    cmd = f"{interp} {shlex.quote(file_path)}" + (f" {args}" if args else "")
    return _run(cmd, cwd, 120, tool_context)


def start_background_process(
    command: str,
    tool_context: ToolContext,
    cwd: str = ".",
    wait_port: int = 0,
    wait_timeout: int = 20,
) -> dict:
    """Start a long-running server/daemon in the background.

    The process keeps running after this call returns. Use stop_background_process
    to terminate it. Always kill stale processes on the port before starting.

    Args:
        command: Command to run (e.g. "npm run dev", "python app.py").
        cwd: Working directory.
        wait_port: If >0, wait until this port accepts connections before returning.
        wait_timeout: Seconds to wait for port readiness (default 20).

    Returns:
        dict with ok, pid, port_ready.
    """
    cwd_path = _resolve_cwd(cwd, tool_context)
    if not cwd_path.is_dir():
        return {"ok": False, "error": f"Directory not found: {cwd}"}

    fd, log_path = tempfile.mkstemp(prefix="cp_bg_", suffix=".log")
    os.close(fd)

    try:
        log_fh = open(log_path, "w")
        proc = subprocess.Popen(
            command, shell=True, cwd=str(cwd_path),
            stdout=log_fh, stderr=subprocess.STDOUT,
            text=True, start_new_session=True, env=_clean_env(),
        )
        _bg_procs[proc.pid] = {
            "pid": proc.pid, "command": command,
            "log_file": log_path, "log_fh": log_fh, "process": proc,
        }
        logger.info("Background process started: PID %d — %s", proc.pid, command)

        if wait_port:
            ready = _wait_port(wait_port, wait_timeout)
            if not ready and proc.poll() is not None:
                log_fh.close()
                tail = Path(log_path).read_text()[-1500:]
                return {"ok": False, "pid": proc.pid,
                        "error": f"Process exited (code {proc.returncode}) before port {wait_port} opened",
                        "log_tail": tail}
            return {"ok": True, "pid": proc.pid,
                    "port_ready": ready, "log_file": log_path}

        time.sleep(0.5)
        if proc.poll() is not None:
            log_fh.close()
            tail = Path(log_path).read_text()[-1500:]
            return {"ok": False, "pid": proc.pid,
                    "error": f"Process exited immediately (code {proc.returncode})",
                    "log_tail": tail}

        return {"ok": True, "pid": proc.pid, "log_file": log_path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def stop_background_process(
    tool_context: ToolContext,
    pid: int = 0,
    port: int = 0,
) -> dict:
    """Stop a background process by PID or by port number.

    Args:
        pid: Process ID to stop (from start_background_process).
        port: Kill the process listening on this port.

    Returns:
        dict with ok and killed_pids.
    """
    pids: list = []
    if pid:
        pids.append(pid)
    if port:
        try:
            r = subprocess.run(f"lsof -i :{port} -t", shell=True,
                               capture_output=True, text=True, env=_clean_env())
            pids += [int(p) for p in r.stdout.strip().split() if p.isdigit()]
        except Exception:
            pass

    if not pids:
        return {"ok": True, "killed_pids": [], "message": "No matching process found"}

    killed = []
    for p in set(pids):
        try:
            os.killpg(os.getpgid(p), signal.SIGTERM)
        except Exception:
            try:
                os.kill(p, signal.SIGTERM)
            except Exception:
                pass
        entry = _bg_procs.pop(p, None)
        if entry:
            try:
                entry["log_fh"].close()
            except Exception:
                pass
        killed.append(p)

    return {"ok": True, "killed_pids": killed}


def wait_for_port(port: int, tool_context: ToolContext, timeout: int = 15) -> dict:
    """Wait until a TCP port starts accepting connections.

    Args:
        port: Port number to wait for.
        timeout: Seconds to wait (default 15).

    Returns:
        dict with ok and ready (bool).
    """
    ready = _wait_port(port, timeout)
    return {"ok": True, "port": port, "ready": ready}


def get_background_output(
    tool_context: ToolContext,
    pid: int = 0,
    log_file: str = "",
    tail: int = 60,
) -> dict:
    """Read recent log output from a background process.

    Args:
        pid: PID of a process started by start_background_process.
        log_file: Direct path to a log file (if pid not available).
        tail: Lines to return from end (default 60).

    Returns:
        dict with ok, tail (str), alive (bool).
    """
    path = None
    if log_file:
        path = Path(log_file)
    elif pid and pid in _bg_procs:
        path = Path(_bg_procs[pid]["log_file"])
    else:
        return {"ok": False, "error": f"No log found for PID {pid}"}

    if not path or not path.exists():
        return {"ok": False, "error": f"Log file missing: {path}"}

    lines = path.read_text(errors="replace").splitlines()
    snippet = "\n".join(lines[-tail:])
    alive = _bg_procs.get(pid, {}).get("process") and _bg_procs[pid]["process"].poll() is None
    return {"ok": True, "tail": snippet, "alive": bool(alive), "total_lines": len(lines)}
