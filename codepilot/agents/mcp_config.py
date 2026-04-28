"""External MCP toolsets — only for third-party services that cannot be local.

After the refactor, MCP is used ONLY for systems that live outside the process:
  - Playwright  → browser automation (requires browser process)
  - GitHub      → official GitHub MCP server (API calls to github.com)
  - Notion      → official Notion MCP server (API calls to notion.com)
  - Slack       → official Slack MCP server  (API calls to slack.com)

All internal capabilities (filesystem, execution, git, testing, etc.) have
been converted to local FunctionTools in codepilot/agents/tools/.

Connections
-----------
Playwright runs as a local npx subprocess (StdioConnectionParams).
GitHub/Notion/Slack use the official remote MCP servers via
StreamableHTTPConnectionParams — no local subprocess needed.
"""

import os
from typing import Optional

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams, StreamableHTTPConnectionParams
from mcp import StdioServerParameters

from ..utils.logger import get_logger

logger = get_logger(__name__)

MCP_TIMEOUT = 300.0


# ---------------------------------------------------------------------------
# Playwright (local subprocess — requires npx)
# ---------------------------------------------------------------------------

_playwright_cache: Optional[McpToolset] = None


def get_playwright_toolset() -> Optional[McpToolset]:
    """Playwright MCP — browser automation for UI testing.

    Returns None if npx is not available (CLI testing skips browser).
    """
    global _playwright_cache
    if _playwright_cache is not None:
        return _playwright_cache
    try:
        import shutil
        if not shutil.which("npx"):
            logger.warning("npx not found — Playwright MCP unavailable")
            return None
        _playwright_cache = McpToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command="npx",
                    args=["-y", "@playwright/mcp@latest", "--headless"],
                ),
                timeout=MCP_TIMEOUT,
            )
        )
        return _playwright_cache
    except Exception as e:
        logger.warning("Playwright MCP init failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# GitHub MCP (remote — official server at api.githubcopilot.com)
# ---------------------------------------------------------------------------

def get_github_toolset(token: Optional[str] = None) -> Optional[McpToolset]:
    """Official GitHub MCP server — repo creation, push, PR management.

    Requires a GitHub Personal Access Token (classic or fine-grained).
    Returns None when no token is available.

    Docs: https://google.github.io/adk-docs/integrations/github/
    """
    github_token = token or os.environ.get("GITHUB_TOKEN")
    if not github_token:
        logger.info("No GITHUB_TOKEN — GitHub MCP unavailable")
        return None
    try:
        return McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://api.githubcopilot.com/mcp/",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    # Restrict to the toolsets we actually need
                    "X-MCP-Toolsets": "repos,issues,pulls",
                },
                timeout=MCP_TIMEOUT,
            )
        )
    except Exception as e:
        logger.warning("GitHub MCP init failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Notion MCP (remote — official notionhq/notion-mcp-server)
# ---------------------------------------------------------------------------

def get_notion_toolset(token: Optional[str] = None) -> Optional[McpToolset]:
    """Official Notion MCP server — task database and plan tracking.

    The agent uses this to:
      - Create a task list database in Notion (Planner)
      - Update task status (Developer, Debug)
      - Read next tasks (Developer)

    Requires a Notion integration token from https://www.notion.so/profile/integrations.
    Returns None when no token is available.

    Docs: https://developers.notion.com/guides/mcp/mcp
    """
    notion_token = token or os.environ.get("NOTION_TOKEN")
    if not notion_token:
        logger.info("No NOTION_TOKEN — Notion MCP unavailable")
        return None
    try:
        return McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://api.notion.com/mcp",
                headers={
                    "Authorization": f"Bearer {notion_token}",
                    "Notion-Version": "2022-06-28",
                },
                timeout=MCP_TIMEOUT,
            )
        )
    except Exception as e:
        # Fall back to npm local server if remote not available
        try:
            import shutil
            if shutil.which("npx"):
                return McpToolset(
                    connection_params=StdioConnectionParams(
                        server_params=StdioServerParameters(
                            command="npx",
                            args=["-y", "@notionhq/notion-mcp-server"],
                            env={**os.environ, "OPENAPI_MCP_HEADERS": f'{{"Authorization":"Bearer {notion_token}","Notion-Version":"2022-06-28"}}'},
                        ),
                        timeout=MCP_TIMEOUT,
                    )
                )
        except Exception:
            pass
        logger.warning("Notion MCP init failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Slack MCP (remote — official Slack MCP server)
# ---------------------------------------------------------------------------

def get_slack_toolset(token: Optional[str] = None) -> Optional[McpToolset]:
    """Official Slack MCP server — notifications and user input.

    The Finalizer uses this to:
      - Send completion notifications to a Slack channel
      - Post failure alerts with details
      - Ask for user input (the agent posts, waits for reply)

    Requires a Slack Bot OAuth token (xoxb-...) with channels:write and
    chat:write scopes.  Returns None when no token is available.

    Docs: https://docs.slack.dev/ai/slack-mcp-server/
    """
    slack_token = token or os.environ.get("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.info("No SLACK_BOT_TOKEN — Slack MCP unavailable")
        return None
    try:
        return McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url="https://mcp.slack.com/",
                headers={"Authorization": f"Bearer {slack_token}"},
                timeout=MCP_TIMEOUT,
            )
        )
    except Exception as e:
        logger.warning("Slack MCP init failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Convenience bundles for builder.py
# ---------------------------------------------------------------------------

def get_planner_mcp_tools(notion_token: Optional[str] = None) -> list:
    """External MCP tools for the Planner (Notion only)."""
    tools = []
    notion = get_notion_toolset(notion_token)
    if notion:
        tools.append(notion)
    return tools


def get_test_mcp_tools() -> list:
    """External MCP tools for the TestAgent (Playwright only)."""
    tools = []
    pw = get_playwright_toolset()
    if pw:
        tools.append(pw)
    return tools


def get_finalizer_mcp_tools(
    github_token: Optional[str] = None,
    slack_token: Optional[str] = None,
    notion_token: Optional[str] = None,
) -> list:
    """External MCP tools for the Finalizer (GitHub + Slack + Notion)."""
    tools = []
    gh = get_github_toolset(github_token)
    if gh:
        tools.append(gh)
    slack = get_slack_toolset(slack_token)
    if slack:
        tools.append(slack)
    notion = get_notion_toolset(notion_token)
    if notion:
        tools.append(notion)
    return tools
