"""Environment MCP server — runtime detection, version management, dependency resolution.

Provides tools for:
  - Detecting installed runtimes (Python, Node, Go, Rust, Java, etc.)
  - Checking specific version requirements
  - Suggesting installation methods
  - Managing virtual environments
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from codepilot.mcp.servers._env import get_clean_env

app = FastMCP(name="environment")


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "message": message})

def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error})

def _run(cmd: str, timeout: int = 15) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
            env=get_clean_env(),
        )
        return {"stdout": result.stdout.strip(), "stderr": result.stderr.strip(),
                "exit_code": result.returncode, "success": result.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Timed out", "exit_code": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "success": False}


# ============================================================================
# RUNTIME DETECTION
# ============================================================================

@app.tool()
def detect_runtimes() -> str:
    """Detect all installed development runtimes and their versions.
    
    Returns a comprehensive environment report including:
    - Languages (Python, Node, Go, Rust, Java, Ruby, PHP, etc.)
    - Package managers (pip, npm, yarn, cargo, etc.)
    - Build tools (make, cmake, gcc, etc.)
    - Utilities (git, docker, curl, etc.)
    """
    runtimes = {}

    checks = [
        # Languages
        ("python3", "python3 --version"),
        ("python", "python --version"),
        ("node", "node --version"),
        ("deno", "deno --version"),
        ("bun", "bun --version"),
        ("go", "go version"),
        ("rustc", "rustc --version"),
        ("java", "java --version"),
        ("ruby", "ruby --version"),
        ("php", "php --version"),
        ("perl", "perl --version"),
        ("lua", "lua -v"),
        ("elixir", "elixir --version"),
        ("swift", "swift --version"),
        ("kotlin", "kotlin -version"),
        ("scala", "scala -version"),
        ("dotnet", "dotnet --version"),

        # Package managers
        ("pip", "pip --version"),
        ("pip3", "pip3 --version"),
        ("npm", "npm --version"),
        ("npx", "npx --version"),
        ("yarn", "yarn --version"),
        ("pnpm", "pnpm --version"),
        ("cargo", "cargo --version"),
        ("composer", "composer --version"),
        ("gem", "gem --version"),
        ("maven", "mvn --version"),
        ("gradle", "gradle --version"),
        ("poetry", "poetry --version"),
        ("pipenv", "pipenv --version"),

        # Build tools
        ("make", "make --version"),
        ("cmake", "cmake --version"),
        ("gcc", "gcc --version"),
        ("g++", "g++ --version"),
        ("clang", "clang --version"),

        # Utilities
        ("git", "git --version"),
        ("docker", "docker --version"),
        ("docker-compose", "docker compose version"),
        ("curl", "curl --version"),
        ("wget", "wget --version"),
    ]

    for name, cmd in checks:
        result = _run(cmd)
        if result["success"]:
            # Extract version string (first line, cleaned)
            version_line = (result["stdout"] or result["stderr"]).split("\n")[0].strip()
            runtimes[name] = {"installed": True, "version": version_line}
        else:
            runtimes[name] = {"installed": False, "version": None}

    # Categorize
    installed = {k: v for k, v in runtimes.items() if v["installed"]}
    missing = [k for k, v in runtimes.items() if not v["installed"]]

    return _ok(
        {"runtimes": runtimes, "installed_count": len(installed),
         "missing": missing[:20]},  # only show first 20 missing
        f"{len(installed)} runtimes detected"
    )


@app.tool()
def check_runtime(name: str, min_version: str = "") -> str:
    """Check if a specific runtime is installed and meets version requirements.
    
    Args:
        name: Runtime name (e.g. "node", "python3", "go", "rustc")
        min_version: Minimum required version (e.g. "18.0.0", "3.10", "1.70")
    
    Returns installation status and suggestions if missing.
    """
    version_cmds = {
        "python": "python --version",
        "python3": "python3 --version",
        "node": "node --version",
        "npm": "npm --version",
        "go": "go version",
        "rustc": "rustc --version",
        "cargo": "cargo --version",
        "java": "java --version",
        "ruby": "ruby --version",
        "php": "php --version",
        "docker": "docker --version",
        "git": "git --version",
        "make": "make --version",
        "gcc": "gcc --version",
    }

    cmd = version_cmds.get(name.lower(), f"{name} --version")
    result = _run(cmd)

    if not result["success"]:
        suggestions = _get_install_suggestions(name)
        return _ok(
            {"name": name, "installed": False, "suggestions": suggestions},
            f"{name} is not installed"
        )

    version_line = (result["stdout"] or result["stderr"]).split("\n")[0].strip()

    data = {"name": name, "installed": True, "version": version_line}

    if min_version:
        import re
        # Extract version numbers
        found = re.findall(r"(\d+\.\d+(?:\.\d+)?)", version_line)
        if found:
            installed_ver = found[0]
            data["installed_version"] = installed_ver
            data["min_version"] = min_version
            data["meets_requirement"] = _compare_versions(installed_ver, min_version)
            if not data["meets_requirement"]:
                data["suggestions"] = _get_install_suggestions(name)
                return _ok(data, f"{name} {installed_ver} found, but {min_version}+ required")

    return _ok(data, f"{name} installed: {version_line}")


@app.tool()
def install_runtime(runtime: str = "", method: str = "") -> str:
    """Attempt to install a runtime. Tries non-sudo methods first.

    IMPORTANT: You MUST pass the 'runtime' argument.

    Args:
        runtime: REQUIRED — The name of the runtime to install.
                 Examples: "node", "go", "rustc"
        method: Specific install method (optional).
                For node: "nvm" (default), "apt"

    For Node.js: tries nvm (no sudo needed).
    For Rust: tries rustup (no sudo needed).
    If non-sudo fails, returns the exact sudo command to run via
    the run_command tool (which supports interactive sudo password).
    """
    runtime = runtime.strip().lower()
    if not runtime:
        return _err(
            "Missing 'runtime' argument. You must specify what to install. "
            "Example: install_runtime(runtime='node'). "
            "Or skip this tool and use run_command directly: "
            "run_command(command='sudo apt-get install -y nodejs npm')"
        )

    # ── Node.js via nvm (no sudo) ──────────────────────────────────
    if runtime in ("node", "nodejs", "npm"):
        # Check if nvm already available
        nvm_check = _run('bash -c "source $HOME/.nvm/nvm.sh 2>/dev/null && nvm --version"')
        if nvm_check["success"]:
            result = _run(
                'bash -c "source $HOME/.nvm/nvm.sh && nvm install --lts && nvm use --lts"',
                timeout=120
            )
            if result["success"]:
                node_ver = _run('bash -c "source $HOME/.nvm/nvm.sh && node --version"')
                return _ok(
                    {"runtime": "node", "installed": True,
                     "version": node_ver["stdout"], "method": "nvm",
                     "activate": "source $HOME/.nvm/nvm.sh",
                     "note": "Run 'source ~/.nvm/nvm.sh' or restart terminal to use."},
                    f"Node.js installed via nvm: {node_ver['stdout']}"
                )

        # nvm not available — try installing nvm first, then node
        if method != "apt":
            result = _run(
                'bash -c "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"',
                timeout=120
            )
            if result["success"]:
                result2 = _run(
                    'bash -c "source $HOME/.nvm/nvm.sh && nvm install --lts"',
                    timeout=120
                )
                if result2["success"]:
                    node_ver = _run('bash -c "source $HOME/.nvm/nvm.sh && node --version"')
                    return _ok(
                        {"runtime": "node", "installed": True,
                         "version": node_ver["stdout"], "method": "nvm",
                         "activate": "source $HOME/.nvm/nvm.sh",
                         "note": "Installed via nvm. Run 'source ~/.nvm/nvm.sh' or restart terminal."},
                        f"Node.js installed via nvm: {node_ver['stdout']}. "
                        f"Run 'source ~/.nvm/nvm.sh' to activate."
                    )

        # Fallback — tell the LLM to use run_command with sudo
        return _ok(
            {"runtime": "node", "installed": False,
             "requires_sudo": True,
             "use_run_command": "sudo apt-get install -y nodejs npm",
             "instructions": (
                 "Non-sudo install failed. Use the run_command tool to run: "
                 "sudo apt-get install -y nodejs npm  — "
                 "The user will be prompted for their password."
             )},
            "Non-sudo install failed. Use run_command tool with: "
            "sudo apt-get install -y nodejs npm"
        )

    # ── Rust via rustup (no sudo) ──────────────────────────────────
    elif runtime in ("rustc", "rust", "cargo"):
        result = _run(
            "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
            timeout=120
        )
        if result["success"]:
            return _ok(
                {"runtime": "rust", "installed": True, "method": "rustup",
                 "activate": "source $HOME/.cargo/env",
                 "note": "Run 'source $HOME/.cargo/env' to activate."},
                "Rust installed via rustup"
            )

    # ── Generic fallback ───────────────────────────────────────────
    suggestions = _get_install_suggestions(runtime)
    # Build a direct sudo command if possible
    sudo_cmd = suggestions[0] if suggestions else f"sudo apt-get install -y {runtime}"
    return _ok(
        {"runtime": runtime, "installed": False,
         "requires_sudo": True,
         "use_run_command": sudo_cmd,
         "instructions": (
             f"Cannot install {runtime} without sudo. "
             f"Use the run_command tool to run: {sudo_cmd}"
         )},
        f"Cannot auto-install {runtime}. Use run_command tool with: {sudo_cmd}"
    )


@app.tool()
def check_venv(directory: str = ".") -> str:
    """Check if a Python virtual environment exists in or near a directory.

    Looks for common venv directories: venv, .venv, env, .env.
    Returns the venv path, Python executable, and version if found.

    Args:
        directory: Directory to search in (default: current directory).

    Returns:
        JSON with exists, path, python executable path, and version.
    """
    root = Path(directory).resolve()

    venv_dirs = ["venv", ".venv", "env", ".env"]
    for vd in venv_dirs:
        venv_path = root / vd
        if venv_path.is_dir():
            python_path = venv_path / "bin" / "python"
            if not python_path.exists():
                python_path = venv_path / "Scripts" / "python.exe"

            if python_path.exists():
                result = _run(f"{python_path} --version")
                version = result["stdout"].strip() if result["success"] else "unknown"
                return _ok(
                    {"exists": True, "path": str(venv_path), "python": str(python_path),
                     "version": version},
                    f"Virtual environment found: {venv_path}"
                )

    return _ok(
        {"exists": False, "directory": str(root),
         "suggestion": f"python3 -m venv {root / 'venv'}"},
        "No virtual environment found"
    )


@app.tool()
def create_venv(directory: str = ".", name: str = "venv") -> str:
    """Create a new Python virtual environment using python3 -m venv.

    Args:
        directory: Parent directory where the venv will be created (default: current directory).
        name: Name of the venv directory (default: "venv").

    Returns:
        JSON with the venv path and the activation command.
    """
    root = Path(directory).resolve()
    venv_path = root / name

    if venv_path.exists():
        return _err(f"Directory already exists: {venv_path}")

    result = _run(f"python3 -m venv {venv_path}", timeout=30)
    if result["success"]:
        return _ok(
            {"path": str(venv_path), "activate": f"source {venv_path / 'bin' / 'activate'}"},
            f"Virtual environment created: {venv_path}"
        )
    return _err(f"Failed to create venv: {result['stderr']}")


# ============================================================================
# INTERNAL HELPERS
# ============================================================================

def _get_install_suggestions(runtime: str) -> List[str]:
    """Get OS-appropriate install suggestions for a runtime."""
    name = runtime.lower()

    # Detect OS
    import platform
    system = platform.system().lower()

    suggestions = []

    install_map = {
        "node": {
            "linux": [
                "curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs",
                "sudo snap install node --classic",
                "Use nvm: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash && nvm install --lts",
            ],
            "darwin": ["brew install node", "Use nvm: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash && nvm install --lts"],
        },
        "python3": {
            "linux": ["sudo apt install python3 python3-pip python3-venv", "sudo dnf install python3 python3-pip"],
            "darwin": ["brew install python3"],
        },
        "go": {
            "linux": ["sudo apt install golang-go", "sudo snap install go --classic"],
            "darwin": ["brew install go"],
        },
        "rustc": {
            "linux": ["curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"],
            "darwin": ["curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh", "brew install rust"],
        },
        "java": {
            "linux": ["sudo apt install default-jdk", "sudo snap install --classic openjdk"],
            "darwin": ["brew install openjdk"],
        },
        "docker": {
            "linux": ["sudo apt install docker.io docker-compose", "curl -fsSL https://get.docker.com | sh"],
            "darwin": ["brew install --cask docker"],
        },
        "ruby": {
            "linux": ["sudo apt install ruby-full"],
            "darwin": ["brew install ruby"],
        },
        "php": {
            "linux": ["sudo apt install php php-cli php-common"],
            "darwin": ["brew install php"],
        },
    }

    if name in install_map:
        suggestions = install_map[name].get(system, install_map[name].get("linux", []))

    if not suggestions:
        suggestions = [f"Search your package manager for '{runtime}'"]

    return suggestions


def _compare_versions(installed: str, required: str) -> bool:
    """Compare semantic version strings."""
    def to_tuple(v):
        parts = v.split(".")
        return tuple(int(p) for p in parts if p.isdigit())

    try:
        return to_tuple(installed) >= to_tuple(required)
    except (ValueError, IndexError):
        return False


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
