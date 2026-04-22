"""Git MCP server — local git operations with structured responses.

Returns JSON: {"ok": true/false, "data": ..., "error": ...}
"""

import json
import logging
import os
import subprocess
from typing import Any, Dict

from fastmcp import FastMCP

from codepilot.mcp.servers._env import get_clean_env

app = FastMCP(name="git")


# ============================================================================
# HELPERS
# ============================================================================

def _ok(data: Any = None, message: str = "") -> str:
    return json.dumps({"ok": True, "data": data, "message": message})


def _err(error: str) -> str:
    return json.dumps({"ok": False, "error": error})


def _git(command: str, cwd: str = ".") -> Dict[str, Any]:
    """Run git command and return result dict."""
    try:
        result = subprocess.run(
            f"git {command}",
            shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=30,
            env=get_clean_env(),
        )
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Command timed out", "exit_code": -1, "success": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "success": False}


# ============================================================================
# REPOSITORY MANAGEMENT
# ============================================================================

@app.tool()
def git_init(path: str = ".") -> str:
    """Initialize a new git repository.

    Args:
        path: Repository path.

    Returns:
        JSON with result.
    """
    result = _git("init", path)
    if result["success"]:
        return _ok({"path": path}, "Repository initialized")
    return _err(result["stderr"])


@app.tool()
def git_status(cwd: str = ".") -> str:
    """Get git status (modified, staged, untracked files).

    Args:
        cwd: Repository directory.

    Returns:
        JSON with parsed status.
    """
    result = _git("status --porcelain", cwd)
    if not result["success"]:
        return _err(result["stderr"])

    files = {"modified": [], "staged": [], "untracked": [], "deleted": []}

    for line in result["stdout"].splitlines():
        if len(line) < 3:
            continue
        index_status = line[0]
        work_status = line[1]
        filename = line[3:]

        if index_status in ("M", "A", "D", "R"):
            files["staged"].append(filename)
        if work_status == "M":
            files["modified"].append(filename)
        elif work_status == "D":
            files["deleted"].append(filename)
        elif index_status == "?" and work_status == "?":
            files["untracked"].append(filename)

    clean = not any(files.values())
    return _ok({
        "clean": clean,
        "files": files,
        "raw": result["stdout"] if not clean else "Working tree clean",
    })


@app.tool()
def git_add(files: str = ".", cwd: str = ".") -> str:
    """Stage files for commit.

    Args:
        files: Files to stage ("." for all, or specific paths).
        cwd: Repository directory.

    Returns:
        JSON with result.
    """
    result = _git(f"add {files}", cwd)
    if result["success"]:
        return _ok({"files": files}, "Files staged")
    return _err(result["stderr"])


@app.tool()
def git_commit(message: str, cwd: str = ".") -> str:
    """Commit staged changes.

    Args:
        message: Commit message.
        cwd: Repository directory.

    Returns:
        JSON with commit result.
    """
    result = _git(f'commit -m "{message}"', cwd)
    if result["success"]:
        return _ok({"message": message, "output": result["stdout"]}, "Changes committed")

    if "nothing to commit" in result["stderr"] or "nothing to commit" in result["stdout"]:
        return _ok({"message": message, "nothing_to_commit": True}, "Nothing to commit")

    return _err(result["stderr"])


@app.tool()
def git_commit_all(message: str, cwd: str = ".") -> str:
    """Stage all changes and commit in one step.

    Args:
        message: Commit message.
        cwd: Repository directory.

    Returns:
        JSON with commit result.
    """
    add_result = _git("add -A", cwd)
    if not add_result["success"]:
        return _err(f"Failed to stage: {add_result['stderr']}")

    result = _git(f'commit -m "{message}"', cwd)
    if result["success"]:
        return _ok({"message": message, "output": result["stdout"]}, "All changes committed")

    if "nothing to commit" in (result.get("stderr", "") + result.get("stdout", "")):
        return _ok({"nothing_to_commit": True}, "Nothing to commit")

    return _err(result["stderr"])


@app.tool()
def git_log(count: int = 10, cwd: str = ".") -> str:
    """Get recent commit log.

    Args:
        count: Number of commits to show.
        cwd: Repository directory.

    Returns:
        JSON with commit list.
    """
    result = _git(f"log --oneline -n {count}", cwd)
    if result["success"]:
        commits = []
        for line in result["stdout"].splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                commits.append({"hash": parts[0], "message": parts[1]})
        return _ok({"commits": commits, "count": len(commits)})
    return _err(result["stderr"])


@app.tool()
def git_diff(staged: bool = False, cwd: str = ".") -> str:
    """Get diff of changes.

    Args:
        staged: If True, show staged changes. If False, show unstaged.
        cwd: Repository directory.

    Returns:
        JSON with diff output.
    """
    flag = "--cached" if staged else ""
    result = _git(f"diff {flag}", cwd)
    if result["success"]:
        has_changes = bool(result["stdout"])
        return _ok({
            "has_changes": has_changes,
            "diff": result["stdout"][:5000] if has_changes else "",
        })
    return _err(result["stderr"])


@app.tool()
def git_branch(cwd: str = ".") -> str:
    """List branches and show current branch.

    Args:
        cwd: Repository directory.

    Returns:
        JSON with branches and current branch.
    """
    result = _git("branch", cwd)
    if result["success"]:
        branches = []
        current = None
        for line in result["stdout"].splitlines():
            name = line.strip().lstrip("* ")
            branches.append(name)
            if line.strip().startswith("*"):
                current = name

        return _ok({"branches": branches, "current": current})
    return _err(result["stderr"])


@app.tool()
def git_create_branch(name: str, cwd: str = ".") -> str:
    """Create and switch to a new branch.

    Args:
        name: Branch name.
        cwd: Repository directory.

    Returns:
        JSON with result.
    """
    result = _git(f"checkout -b {name}", cwd)
    if result["success"]:
        return _ok({"branch": name}, f"Created and switched to branch: {name}")
    return _err(result["stderr"])


@app.tool()
def git_checkout(branch: str, cwd: str = ".") -> str:
    """Switch to an existing branch.

    Args:
        branch: Branch name to switch to.
        cwd: Repository directory.

    Returns:
        JSON with result.
    """
    result = _git(f"checkout {branch}", cwd)
    if result["success"]:
        return _ok({"branch": branch}, f"Switched to branch: {branch}")
    return _err(result["stderr"])


@app.tool()
def git_info(cwd: str = ".") -> str:
    """Get comprehensive git repository information.

    Args:
        cwd: Repository directory.

    Returns:
        JSON with repo info (branch, remote, status, last commit).
    """
    # Check if in a git repo
    check = _git("rev-parse --git-dir", cwd)
    if not check["success"]:
        return _ok({"is_repo": False}, "Not a git repository")

    info = {"is_repo": True}

    # Current branch
    branch = _git("rev-parse --abbrev-ref HEAD", cwd)
    if branch["success"]:
        info["branch"] = branch["stdout"]

    # Remote URL
    remote = _git("config --get remote.origin.url", cwd)
    if remote["success"] and remote["stdout"]:
        info["remote"] = remote["stdout"]

    # Last commit
    last = _git("log --oneline -1", cwd)
    if last["success"]:
        info["last_commit"] = last["stdout"]

    # Status summary
    status = _git("status --porcelain", cwd)
    if status["success"]:
        changes = len([l for l in status["stdout"].splitlines() if l.strip()])
        info["pending_changes"] = changes

    return _ok(info)


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    app.run(transport="stdio", show_banner=False, log_level="error")
