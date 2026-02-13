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
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
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
def get_install_command(runtime: str) -> str:
    """Get the recommended install command for a runtime.
    
    Returns the appropriate package manager command based on the OS.
    NOTE: This only returns the command — it must be approved via permissions.
    """
    suggestions = _get_install_suggestions(runtime)

    return _ok(
        {"runtime": runtime, "suggestions": suggestions},
        f"Install suggestions for {runtime}"
    )


@app.tool()
def check_venv(directory: str = ".") -> str:
    """Check if a Python virtual environment exists in the directory."""
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
    """Create a Python virtual environment."""
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


@app.tool()
def check_node_project(directory: str = ".") -> str:
    """Check if a Node.js project is properly set up."""
    root = Path(directory).resolve()
    status = {
        "has_package_json": (root / "package.json").exists(),
        "has_node_modules": (root / "node_modules").is_dir(),
        "has_lock_file": any(
            (root / f).exists()
            for f in ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"]
        ),
    }

    if status["has_package_json"]:
        try:
            pkg = json.loads((root / "package.json").read_text())
            status["name"] = pkg.get("name", "")
            status["scripts"] = list(pkg.get("scripts", {}).keys())
            status["dependencies_count"] = len(pkg.get("dependencies", {}))
            status["dev_dependencies_count"] = len(pkg.get("devDependencies", {}))
        except Exception:
            pass

    all_ok = status["has_package_json"] and status["has_node_modules"]
    msg = "Node.js project OK" if all_ok else "Node.js project needs setup"
    if not status["has_package_json"]:
        msg = "No package.json found — run 'npm init -y'"
    elif not status["has_node_modules"]:
        msg = "Dependencies not installed — run 'npm install'"

    return _ok(status, msg)


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
