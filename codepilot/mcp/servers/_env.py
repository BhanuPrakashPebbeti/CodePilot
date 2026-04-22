"""Shared environment isolation for MCP server subprocesses.

Every MCP server that runs commands via subprocess MUST use ``get_clean_env()``
so that:

1. CodePilot's own virtual‑environment is stripped from PATH / VIRTUAL_ENV.
   This prevents ``pip install`` from accidentally polluting CodePilot's deps.
2. Version‑manager paths (nvm, cargo, go) are added so the agent can find
   runtimes even when they are not on the default system PATH.

Usage in any server:
    from codepilot.mcp.servers._env import get_clean_env
    subprocess.run(cmd, env=get_clean_env(), ...)
"""

import os
from pathlib import Path
from typing import Dict, Optional

# Captured ONCE at import time — the venv CodePilot itself is running in.
_CODEPILOT_VENV: Optional[str] = os.environ.get("VIRTUAL_ENV")


def get_clean_env() -> Dict[str, str]:
    """Return an environment dict safe for subprocess execution.

    • Strips CodePilot's own venv from ``PATH``.
    • Clears ``VIRTUAL_ENV`` so pip can't target CodePilot.
    • Adds nvm / cargo / go bin dirs to ``PATH``.
    • Sets ``PYTHONDONTWRITEBYTECODE=1``.
    """
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    # ── Strip CodePilot's own venv from PATH ────────────────────────
    if _CODEPILOT_VENV:
        venv_bin = os.path.join(_CODEPILOT_VENV, "bin")
        path_dirs = env.get("PATH", "").split(":")
        path_dirs = [
            d for d in path_dirs
            if os.path.normpath(d) != os.path.normpath(venv_bin)
        ]
        env["PATH"] = ":".join(path_dirs)
        env.pop("VIRTUAL_ENV", None)

    # ── Add nvm‑managed Node.js ─────────────────────────────────────
    home = str(Path.home())
    nvm_dir = env.get("NVM_DIR", os.path.join(home, ".nvm"))
    nvm_versions = os.path.join(nvm_dir, "versions", "node")
    if os.path.isdir(nvm_versions):
        try:
            versions = sorted(os.listdir(nvm_versions), reverse=True)
            if versions:
                node_bin = os.path.join(nvm_versions, versions[0], "bin")
                if os.path.isdir(node_bin):
                    env["PATH"] = node_bin + ":" + env.get("PATH", "")
        except OSError:
            pass

    # ── Add cargo (Rust) ────────────────────────────────────────────
    cargo_bin = os.path.join(home, ".cargo", "bin")
    if os.path.isdir(cargo_bin):
        env["PATH"] = cargo_bin + ":" + env.get("PATH", "")

    # ── Add Go binaries ─────────────────────────────────────────────
    gopath_bin = os.path.join(
        env.get("GOPATH", os.path.join(home, "go")), "bin"
    )
    if os.path.isdir(gopath_bin):
        env["PATH"] = gopath_bin + ":" + env.get("PATH", "")

    return env
