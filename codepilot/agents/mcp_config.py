"""MCP toolset configuration for ADK agents.

Maps CodePilot's FastMCP servers to ADK McpToolset instances.
Each agent gets only the tools it needs — no agent has access to all tools.

Toolset instances are cached (lru_cache) so agents sharing a server
reuse the same subprocess rather than spawning duplicates.

Server → Agent mapping
----------------------
planning    → Planner, Developer
filesystem  → Developer, Reviewer, Debug, Finalizer
bash        → Developer, Runtime, Debug, Finalizer
workspace   → Planner, Developer, Reviewer
testing     → Runtime, Browser
debug       → Reviewer, Debug
git         → Developer, Finalizer
environment → Planner, Developer
github      → Developer (optional, requires GITHUB_TOKEN)
playwright  → Browser (optional, requires npx)
memory      → Planner, Debug, Finalizer  ← persistent cross-session memory
"""

import os
import sys
from functools import lru_cache
from typing import List, Optional

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

from ..utils.logger import get_logger

logger = get_logger(__name__)

# Per-tool call timeout (seconds).  High because npm install / cargo build
# can take several minutes.  Connection establishment is still fast.
MCP_TIMEOUT = 300.0


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _python() -> str:
    return sys.executable


def _module(name: str) -> str:
    return f"codepilot.mcp.servers.{name}"


def _project_dir() -> str:
    return os.environ.get("CODEPILOT_PROJECT_DIR") or os.getcwd()


def _conn(name: str, env: Optional[dict] = None) -> StdioConnectionParams:
    """Build StdioConnectionParams for a CodePilot MCP server.

    The subprocess CWD is set to CODEPILOT_PROJECT_DIR so all relative
    paths in MCP tools resolve against the user's project directory.
    """
    return StdioConnectionParams(
        server_params=StdioServerParameters(
            command=_python(),
            args=["-m", _module(name)],
            env=env,
            cwd=_project_dir(),
        ),
        timeout=MCP_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Cached MCP toolset singletons
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _planning_toolset() -> McpToolset:
    return McpToolset(connection_params=_conn("planning_server"))


@lru_cache(maxsize=1)
def _filesystem_toolset() -> McpToolset:
    return McpToolset(connection_params=_conn("filesystem_server"))


@lru_cache(maxsize=1)
def _bash_toolset() -> McpToolset:
    return McpToolset(connection_params=_conn("bash_server"))


@lru_cache(maxsize=1)
def _workspace_toolset() -> McpToolset:
    return McpToolset(connection_params=_conn("workspace_server"))


@lru_cache(maxsize=1)
def _testing_toolset() -> McpToolset:
    return McpToolset(connection_params=_conn("testing_server"))


@lru_cache(maxsize=1)
def _debug_toolset() -> McpToolset:
    return McpToolset(connection_params=_conn("debug_server"))


@lru_cache(maxsize=1)
def _git_toolset() -> McpToolset:
    return McpToolset(connection_params=_conn("git_server"))


@lru_cache(maxsize=1)
def _environment_toolset() -> McpToolset:
    return McpToolset(connection_params=_conn("environment_server"))


@lru_cache(maxsize=1)
def _memory_toolset() -> McpToolset:
    """Persistent structured memory — conversation summaries, project notes,
    error→fix patterns, and user preferences."""
    return McpToolset(connection_params=_conn("memory_server"))


def _github_toolset(token: Optional[str] = None) -> Optional[McpToolset]:
    """GitHub MCP server.  Returns None when no token is available."""
    github_token = token or os.environ.get("GITHUB_TOKEN")
    if not github_token:
        return None
    env = {**os.environ, "GITHUB_TOKEN": github_token}
    return McpToolset(connection_params=_conn("github_server", env=env))


@lru_cache(maxsize=1)
def _playwright_toolset() -> McpToolset:
    """Playwright browser automation — headed mode for visible testing."""
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="npx",
                args=["-y", "@playwright/mcp@latest", "--headed"],
            ),
            timeout=MCP_TIMEOUT,
        ),
    )


# Public aliases for any external callers that use the old names.
planning_toolset    = _planning_toolset
workspace_toolset   = _workspace_toolset
testing_toolset     = _testing_toolset
debug_toolset       = _debug_toolset
git_toolset         = _git_toolset
environment_toolset = _environment_toolset
memory_toolset      = _memory_toolset
github_toolset      = _github_toolset
playwright_toolset  = _playwright_toolset


# ---------------------------------------------------------------------------
# Agent-specific tool bundles
# ---------------------------------------------------------------------------

def get_planner_tools(github_token: Optional[str] = None) -> List:
    """Planner: explore project context + check past sessions before planning."""
    return [
        _planning_toolset(),
        _workspace_toolset(),
        _environment_toolset(),
        _memory_toolset(),   # recall what was built in previous sessions
    ]


def get_developer_tools(github_token: Optional[str] = None) -> List:
    """Developer: full file/bash/git access + optional GitHub."""
    tools = [
        _planning_toolset(),
        _filesystem_toolset(),
        _bash_toolset(),
        _workspace_toolset(),
        _git_toolset(),
        _environment_toolset(),
    ]
    gh = _github_toolset(github_token)
    if gh:
        tools.append(gh)
    return tools


def get_review_tools() -> List:
    """Reviewer: read files, detect project structure, run syntax checks."""
    return [
        _filesystem_toolset(),
        _workspace_toolset(),
        _debug_toolset(),
        _bash_toolset(),
    ]


def get_runtime_tools() -> List:
    """Runtime: run commands, start servers, verify endpoints."""
    return [
        _bash_toolset(),
        _testing_toolset(),
    ]


def get_browser_tools() -> List:
    """Browser/Test: Playwright for UI testing, fallback to HTTP tests."""
    tools: list = []
    try:
        tools.append(_playwright_toolset())
    except Exception as exc:
        logger.warning("Playwright toolset unavailable — skipping: %s", exc)
        _playwright_toolset.cache_clear()
    tools.append(_testing_toolset())
    return tools


def get_debug_tools() -> List:
    """Debug: error parsing, file fixes, bash, + memory for known fixes."""
    return [
        _debug_toolset(),
        _filesystem_toolset(),
        _bash_toolset(),
        _memory_toolset(),   # search for known error→fix patterns
    ]


def get_finalizer_tools() -> List:
    """Finalizer: cleanup, git commit, README, + memory to save session summary."""
    return [
        _bash_toolset(),
        _filesystem_toolset(),
        _git_toolset(),
        _memory_toolset(),   # persist conversation summary for future sessions
    ]
