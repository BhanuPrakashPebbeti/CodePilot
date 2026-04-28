"""Local environment detection tools — replaces environment_server.py MCP."""

import shutil
import subprocess
from pathlib import Path

from google.adk.tools.tool_context import ToolContext

from .exec import _clean_env, _resolve_cwd


def _ver(cmd: str) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=5, env=_clean_env())
        return r.stdout.strip() or r.stderr.strip()
    except Exception:
        return ""


def detect_runtimes(tool_context: ToolContext) -> dict:
    """Detect all installed language runtimes and their versions.

    Returns:
        dict with ok and runtimes (dict of runtime → version string).
    """
    checks = {
        "python": "python3 --version",
        "node": "node --version",
        "npm": "npm --version",
        "npx": "npx --version",
        "go": "go version",
        "rust": "rustc --version",
        "java": "java -version",
        "ruby": "ruby --version",
        "docker": "docker --version",
    }
    runtimes = {}
    for name, cmd in checks.items():
        v = _ver(cmd)
        if v:
            runtimes[name] = v
    return {"ok": True, "runtimes": runtimes}


def check_runtime(runtime: str, tool_context: ToolContext) -> dict:
    """Check if a specific runtime is installed.

    Args:
        runtime: Runtime name (e.g. "python", "node", "go").

    Returns:
        dict with ok, installed (bool), and version.
    """
    found = shutil.which(runtime)
    if not found:
        return {"ok": True, "installed": False, "version": ""}
    v = _ver(f"{runtime} --version")
    return {"ok": True, "installed": True, "path": found, "version": v}


def create_venv(tool_context: ToolContext, cwd: str = ".", name: str = "venv") -> dict:
    """Create a Python virtual environment.

    Args:
        cwd: Directory in which to create the venv.
        name: Venv directory name (default "venv").

    Returns:
        dict with ok and pip (path to pip binary in the venv).
    """
    p = _resolve_cwd(cwd, tool_context)
    venv_dir = p / name
    try:
        r = subprocess.run(
            f"python3 -m venv {name}", shell=True, cwd=str(p),
            capture_output=True, text=True, env=_clean_env(),
        )
        pip = str(venv_dir / "bin" / "pip")
        return {"ok": r.returncode == 0, "venv": str(venv_dir),
                "pip": pip, "stderr": r.stderr.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_venv(tool_context: ToolContext, cwd: str = ".", name: str = "venv") -> dict:
    """Check if a Python virtual environment exists.

    Args:
        cwd: Project directory.
        name: Venv directory name.

    Returns:
        dict with ok, exists (bool), and pip path.
    """
    p = _resolve_cwd(cwd, tool_context) / name
    exists = p.is_dir()
    pip = str(p / "bin" / "pip") if exists else ""
    return {"ok": True, "exists": exists, "venv": str(p), "pip": pip}
