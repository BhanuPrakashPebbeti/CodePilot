"""Git MCP server for local git operations."""
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

app = FastMCP(name="git")


def _run_git(command: str, cwd: str = ".") -> dict:
    """Run git command and return result.

    Args:
        command: Git command to run.
        cwd: Working directory.

    Returns:
        Dict with stdout, stderr, and exit code.
    """
    try:
        result = subprocess.run(
            f"git {command}",
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out", "exit_code": -1, "success": False}
    except Exception as e:
        return {"error": str(e), "exit_code": -1, "success": False}


@app.tool()
def init_repo(path: str = ".") -> str:
    """Initialize git repository.

    Args:
        path: Repository path.

    Returns:
        Result message.
    """
    result = _run_git("init", path)
    if result["success"]:
        return f"Repository initialized at {path}"
    return f"Error: {result.get('stderr', result.get('error'))}"


@app.tool()
def create_branch(name: str) -> str:
    """Create and checkout new branch.

    Args:
        name: Branch name.

    Returns:
        Result message.
    """
    result = _run_git(f"checkout -b {name}")
    if result["success"]:
        return f"Branch created and checked out: {name}"
    return f"Error: {result.get('stderr', result.get('error'))}"


@app.tool()
def commit_all(message: str) -> str:
    """Add all changes and commit.

    Args:
        message: Commit message.

    Returns:
        Result message.
    """
    # Add all changes
    add_result = _run_git("add -A")
    if not add_result["success"]:
        return f"Error adding files: {add_result.get('stderr')}"

    # Commit
    commit_result = _run_git(f'commit -m "{message}"')
    if commit_result["success"]:
        return f"Committed: {message}"

    # Check if there are no changes to commit
    if "nothing to commit" in commit_result.get("stderr", ""):
        return "Nothing to commit (no changes)"

    return f"Error: {commit_result.get('stderr', commit_result.get('error'))}"


@app.tool()
def git_status() -> str:
    """Get git status.

    Returns:
        Status output.
    """
    result = _run_git("status --short")
    if result["success"]:
        return result["stdout"] if result["stdout"] else "Working tree clean"
    return f"Error: {result.get('stderr')}"


@app.tool()
def git_diff() -> str:
    """Get git diff of uncommitted changes.

    Returns:
        Diff output.
    """
    result = _run_git("diff")
    if result["success"]:
        return result["stdout"] if result["stdout"] else "No uncommitted changes"
    return f"Error: {result.get('stderr')}"


@app.tool()
def get_current_branch() -> str:
    """Get current branch name.

    Returns:
        Branch name.
    """
    result = _run_git("rev-parse --abbrev-ref HEAD")
    if result["success"]:
        return result["stdout"]
    return f"Error: {result.get('stderr')}"


@app.tool()
def log(max_count: int = 5) -> str:
    """Get recent commit log.

    Args:
        max_count: Number of commits to show.

    Returns:
        Log output.
    """
    result = _run_git(f"log --oneline -n {max_count}")
    if result["success"]:
        return result["stdout"]
    return f"Error: {result.get('stderr')}"


@app.tool()
def get_git_info() -> str:
    """Get git repository information.

    Returns:
        Git info summary.
    """
    info = []

    # Check if in git repo
    status_result = _run_git("rev-parse --git-dir")
    if not status_result["success"]:
        return "Not in a git repository"

    # Get current branch
    branch_result = _run_git("rev-parse --abbrev-ref HEAD")
    if branch_result["success"]:
        info.append(f"Branch: {branch_result['stdout']}")

    # Get remote
    remote_result = _run_git("config --get remote.origin.url")
    if remote_result["success"] and remote_result["stdout"]:
        info.append(f"Remote: {remote_result['stdout']}")

    # Get status
    status = git_status()
    info.append(f"Status: {status}")

    return "\n".join(info)

if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"

    logging.getLogger().setLevel(logging.ERROR)

    app.run(
        transport="stdio",
        show_banner=False,
        log_level="error"
    )
