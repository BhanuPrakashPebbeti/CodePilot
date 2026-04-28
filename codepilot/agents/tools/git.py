"""Local git tools — replaces git_server.py MCP."""

import subprocess
from pathlib import Path

from google.adk.tools.tool_context import ToolContext

from .exec import _clean_env, _resolve_cwd


def _git(args: str, cwd: Path) -> dict:
    r = subprocess.run(
        f"git {args}", shell=True, cwd=str(cwd),
        capture_output=True, text=True, env=_clean_env(),
    )
    return {"ok": r.returncode == 0, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}


def git_init(tool_context: ToolContext, cwd: str = ".") -> dict:
    """Initialize a git repository in cwd.

    Args:
        cwd: Directory to initialize (default: project root).

    Returns:
        dict with ok.
    """
    return _git("init", _resolve_cwd(cwd, tool_context))


def git_status(tool_context: ToolContext, cwd: str = ".") -> dict:
    """Show working tree status.

    Args:
        cwd: Repository directory.

    Returns:
        dict with ok and stdout (status output).
    """
    return _git("status --short", _resolve_cwd(cwd, tool_context))


def git_add(path: str, tool_context: ToolContext, cwd: str = ".") -> dict:
    """Stage a file or pattern for commit.

    Args:
        path: File path or glob pattern (e.g. "." to stage all).
        cwd: Repository directory.

    Returns:
        dict with ok.
    """
    return _git(f"add {path}", _resolve_cwd(cwd, tool_context))


def git_commit(message: str, tool_context: ToolContext, cwd: str = ".") -> dict:
    """Create a commit with staged changes.

    Args:
        message: Commit message.
        cwd: Repository directory.

    Returns:
        dict with ok and stdout.
    """
    return _git(f'commit -m "{message}"', _resolve_cwd(cwd, tool_context))


def git_commit_all(message: str, tool_context: ToolContext, cwd: str = ".") -> dict:
    """Stage all changes and create a commit.

    Args:
        message: Commit message.
        cwd: Repository directory.

    Returns:
        dict with ok and stdout.
    """
    p = _resolve_cwd(cwd, tool_context)
    _git("add -A", p)
    return _git(f'commit -m "{message}"', p)


def git_log(tool_context: ToolContext, cwd: str = ".", n: int = 10) -> dict:
    """Show recent commit log.

    Args:
        cwd: Repository directory.
        n: Number of commits to show (default 10).

    Returns:
        dict with ok and stdout.
    """
    return _git(f"log --oneline -n {n}", _resolve_cwd(cwd, tool_context))


def git_diff(tool_context: ToolContext, cwd: str = ".") -> dict:
    """Show unstaged changes.

    Args:
        cwd: Repository directory.

    Returns:
        dict with ok and stdout (diff output).
    """
    return _git("diff", _resolve_cwd(cwd, tool_context))


def git_info(tool_context: ToolContext, cwd: str = ".") -> dict:
    """Return branch name, remote URL, and latest commit hash.

    Args:
        cwd: Repository directory.

    Returns:
        dict with ok, branch, remote, commit.
    """
    p = _resolve_cwd(cwd, tool_context)
    branch = _git("rev-parse --abbrev-ref HEAD", p)
    remote = _git("remote get-url origin", p)
    commit = _git("rev-parse --short HEAD", p)
    return {
        "ok": True,
        "branch": branch.get("stdout", ""),
        "remote": remote.get("stdout", ""),
        "commit": commit.get("stdout", ""),
    }


def git_create_branch(branch: str, tool_context: ToolContext, cwd: str = ".") -> dict:
    """Create and checkout a new branch.

    Args:
        branch: Branch name to create.
        cwd: Repository directory.

    Returns:
        dict with ok.
    """
    return _git(f"checkout -b {branch}", _resolve_cwd(cwd, tool_context))


def git_checkout(branch: str, tool_context: ToolContext, cwd: str = ".") -> dict:
    """Checkout an existing branch.

    Args:
        branch: Branch name to checkout.
        cwd: Repository directory.

    Returns:
        dict with ok.
    """
    return _git(f"checkout {branch}", _resolve_cwd(cwd, tool_context))


def git_push(
    remote: str,
    branch: str,
    tool_context: ToolContext,
    cwd: str = ".",
    set_upstream: bool = True,
) -> dict:
    """Push a branch to a remote repository.

    Args:
        remote: Remote name (e.g. "origin").
        branch: Branch to push.
        cwd: Repository directory.
        set_upstream: Set tracking (--set-upstream-to).

    Returns:
        dict with ok and stdout.
    """
    flag = "-u" if set_upstream else ""
    return _git(f"push {flag} {remote} {branch}", _resolve_cwd(cwd, tool_context))
