"""External MCP toolsets for CodePilot agents.

Architecture: Notion and Slack use local Python tools (notion_tools.py +
slack_hitl.py) for deterministic schema control and HITL support without
subprocess overhead.

Remaining MCP servers (both run via npx, stdio transport):
  Playwright → @playwright/mcp            — headed browser UI testing + screenshots
  GitHub     → @modelcontextprotocol/server-github — repo creation, push, PR

Playwright runs in headed (visible) mode by default so browser actions are
observable. Set CODEPILOT_BROWSER_HEADLESS=true to override.
"""

import os
import shutil
from typing import Optional

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from ..utils.logger import get_logger

logger = get_logger(__name__)

_TIMEOUT = 300.0


def _npx_available() -> bool:
    return shutil.which("npx") is not None


def _headless() -> bool:
    """Return True only when explicitly opted into headless mode."""
    return os.environ.get("CODEPILOT_BROWSER_HEADLESS", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Individual toolsets
# ---------------------------------------------------------------------------

def get_playwright_toolset() -> Optional[McpToolset]:
    """Playwright MCP — headed browser automation, UI testing, screenshots.

    Runs in headed (visible) mode by default so testers can observe browser
    actions in real time. Override with CODEPILOT_BROWSER_HEADLESS=true.
    """
    if not _npx_available():
        logger.info("npx not found — Playwright MCP skipped")
        return None

    args = ["-y", "@playwright/mcp@latest"]
    if _headless():
        args.append("--headless")
        logger.info("Playwright MCP starting in headless mode (CODEPILOT_BROWSER_HEADLESS=true)")
    else:
        logger.info("Playwright MCP starting in headed mode — browser window will be visible")

    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="npx",
                args=args,
            ),
            timeout=_TIMEOUT,
        )
    )


def get_github_toolset(token: Optional[str] = None) -> Optional[McpToolset]:
    """GitHub MCP — create repos, push code, open pull requests.

    Env: GITHUB_PERSONAL_ACCESS_TOKEN or GITHUB_TOKEN
    """
    github_token = (
        token
        or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )
    if not github_token:
        logger.info("No GitHub token — GitHub MCP skipped")
        return None
    if not _npx_available():
        logger.info("npx not found — GitHub MCP skipped")
        return None
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="npx",
                args=["-y", "@modelcontextprotocol/server-github"],
                env={**os.environ, "GITHUB_PERSONAL_ACCESS_TOKEN": github_token},
            ),
            timeout=_TIMEOUT,
        )
    )


# ---------------------------------------------------------------------------
# Agent-level bundles (called from builder.py)
# ---------------------------------------------------------------------------

def get_test_mcp_tools() -> list:
    """Playwright for browser UI testing in TestAgent."""
    tools = []
    if ts := get_playwright_toolset():
        tools.append(ts)
    return tools


def get_finalizer_mcp_tools(
    github_token: Optional[str] = None,
) -> list:
    """GitHub MCP for repo creation and PR in FinalizerAgent.

    Notion and Slack are now local Python tools (notion_tools + slack_hitl),
    so they are NOT included here.
    """
    tools = []
    if ts := get_github_toolset(github_token):
        tools.append(ts)
    return tools
