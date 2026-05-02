"""Guided integration onboarding for CodePilot.

Each function walks the user through setting up one integration:
  onboard_slack()  — token entry + interactive channel picker + membership check
  onboard_notion() — token entry + parent-page picker + one-time DB initialization
  onboard_github() — tries gh CLI auth first, falls back to PAT prompt

All functions return the updated config kwargs dict (never raise on failure —
the user can always skip integrations and configure them later).
"""

import shutil
import subprocess
from typing import Optional

from rich.prompt import Confirm, Prompt
from rich.table import Table

from .utils import console
from .utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def onboard_slack() -> dict:
    """Walk the user through Slack onboarding.

    Returns:
        {"bot_token": str, "channel": str} or {} if skipped.
    """
    console.print("\n[bold cyan]Slack Setup[/bold cyan]")
    console.print("[dim]Required bot scopes: chat:write, channels:history, channels:read[/dim]")
    console.print("[dim]Optional scope:      channels:join  (auto-join public channels)[/dim]")
    console.print(
        "[dim]Create a bot at: https://api.slack.com/apps → "
        "New App → From scratch → add scopes → Install to workspace[/dim]\n"
    )

    bot_token = Prompt.ask("Paste your Slack bot token (xoxb-...)", password=True)
    if not bot_token or not bot_token.startswith("xoxb-"):
        console.print("[yellow]⚠ Invalid token format — Slack skipped[/yellow]")
        return {}

    channel = _pick_slack_channel(bot_token)
    return {"bot_token": bot_token, "channel": channel}


def _pick_slack_channel(bot_token: str) -> str:
    """List workspace channels and let the user choose one interactively."""
    try:
        from slack_sdk import WebClient
    except ImportError:
        console.print("[yellow]  slack-sdk not installed — using default channel #codepilot[/yellow]")
        return "#codepilot"

    client = WebClient(token=bot_token)
    channels = []
    try:
        for page in client.conversations_list(types="public_channel,private_channel", limit=200):
            channels.extend(page.get("channels", []))
    except Exception as exc:
        console.print(f"[yellow]  Could not list channels ({exc}) — defaulting to #codepilot[/yellow]")
        return "#codepilot"

    if not channels:
        console.print("[yellow]  No channels found — defaulting to #codepilot[/yellow]")
        return "#codepilot"

    # Sort: bot-is-member first, then alphabetical
    channels.sort(key=lambda c: (not c.get("is_member", False), c.get("name", "")))

    table = Table(show_header=True, header_style="bold cyan", show_lines=False)
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Channel", style="cyan")
    table.add_column("Members", style="dim", justify="right")
    table.add_column("Bot joined?", style="dim", justify="center")

    for i, ch in enumerate(channels[:30], 1):
        joined = "✓" if ch.get("is_member") else ""
        table.add_row(str(i), f"#{ch['name']}", str(ch.get("num_members", "?")), joined)

    console.print(table)

    choice = Prompt.ask(
        f"Select a channel (1-{min(len(channels), 30)}) or type a channel name",
        default="1",
    )
    try:
        idx = int(choice)
        if 1 <= idx <= min(len(channels), 30):
            selected = f"#{channels[idx - 1]['name']}"
        else:
            selected = choice if choice.startswith("#") else f"#{choice}"
    except ValueError:
        selected = choice if choice.startswith("#") else f"#{choice}"

    # Attempt to join if the bot is not yet a member
    _try_join_channel(client, selected)
    console.print(f"[green]✓ Slack channel set to {selected}[/green]")
    return selected


def _try_join_channel(client, channel_name: str) -> None:
    """Try to join the channel (requires channels:join scope). Silent on failure."""
    try:
        name = channel_name.lstrip("#")
        resp = client.conversations_list(types="public_channel", limit=200)
        for page in resp:
            for ch in page.get("channels", []):
                if ch["name"] == name and not ch.get("is_member"):
                    client.conversations_join(channel=ch["id"])
                    console.print(f"[dim]  Bot joined #{name}[/dim]")
                    return
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------

def onboard_notion(config_mgr) -> dict:
    """Walk the user through Notion onboarding.

    1. Prompts for integration token.
    2. Lists accessible pages and lets user pick the parent.

    Per-project databases (Tasks, Activity Log, Test Artifacts) are created
    automatically at the start of each project by notion_setup_project() —
    no global database setup is needed during onboarding.

    Returns:
        {"token": str, "parent_page_id": str} or {} if skipped.
    """
    console.print("\n[bold cyan]Notion Setup[/bold cyan]")
    console.print(
        "[dim]Create an integration at: https://notion.so/profile/integrations\n"
        "Then open the page you want to use, click ··· → Connections → add your integration.\n"
        "CodePilot creates fresh databases inside a new page for each project.[/dim]\n"
    )

    token = Prompt.ask("Paste your Notion integration token (secret_...)", password=True)
    if not token or not token.startswith("secret_"):
        console.print("[yellow]⚠ Invalid token — Notion skipped[/yellow]")
        return {}

    parent_page_id = _pick_notion_page(token)
    if not parent_page_id:
        return {}

    console.print(
        "[green]✓ Notion configured[/green]\n"
        "[dim]  Project databases will be created automatically when you start a project.[/dim]"
    )
    return {"token": token, "parent_page_id": parent_page_id}


def _pick_notion_page(token: str) -> Optional[str]:
    """List Notion pages accessible to the integration and let the user pick."""
    try:
        from notion_client import Client
    except ImportError:
        console.print("[yellow]  notion-client not installed — enter page ID manually[/yellow]")
        return _manual_notion_page_id()

    client = Client(auth=token)
    pages = []
    try:
        results = client.search(filter={"property": "object", "value": "page"}, page_size=30)
        pages = results.get("results", [])
    except Exception as exc:
        console.print(f"[yellow]  Could not list pages ({exc})[/yellow]")
        return _manual_notion_page_id()

    if not pages:
        console.print(
            "[yellow]  No pages found — make sure you've shared at least one page "
            "with your integration (page → ··· → Connections).[/yellow]"
        )
        return _manual_notion_page_id()

    table = Table(show_header=True, header_style="bold cyan", show_lines=False)
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Page title", style="cyan")
    table.add_column("ID", style="dim")

    page_ids = []
    for i, page in enumerate(pages[:20], 1):
        title_parts = (
            page.get("properties", {})
            .get("title", {})
            .get("title", [])
        )
        title = title_parts[0]["text"]["content"] if title_parts else "(Untitled)"
        page_id = page["id"]
        page_ids.append(page_id)
        table.add_row(str(i), title, page_id[:8] + "…")

    console.print(table)
    console.print("[dim]  CodePilot will create Projects and Tasks databases under the selected page.[/dim]")

    choice = Prompt.ask(
        f"Select a page (1-{len(page_ids)}) or paste a full page ID",
        default="1",
    )
    try:
        idx = int(choice)
        if 1 <= idx <= len(page_ids):
            selected_id = page_ids[idx - 1]
            console.print(f"[green]✓ Parent page selected (ID: {selected_id[:8]}…)[/green]")
            return selected_id
    except ValueError:
        pass

    # Treat raw input as a page ID (strip dashes, validate length)
    raw = choice.replace("-", "")
    if len(raw) == 32:
        return choice
    console.print("[yellow]  Invalid selection — Notion skipped[/yellow]")
    return None


def _manual_notion_page_id() -> Optional[str]:
    pid = Prompt.ask(
        "Enter Notion parent page ID (32-char hex from page URL, or Enter to skip)",
        default="",
    )
    return pid.strip() or None


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def onboard_github() -> dict:
    """Walk the user through GitHub onboarding.

    Tries the GitHub CLI first (gh auth login / gh auth status).
    Falls back to personal access token (PAT) prompt.

    Returns:
        {"token": str} or {} if skipped.
    """
    console.print("\n[bold cyan]GitHub Setup[/bold cyan]")
    console.print("[dim]Required scopes: repo, workflow (optional)[/dim]\n")

    # Try gh CLI first
    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                # Extract token from gh CLI
                token_result = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True, text=True, timeout=10,
                )
                if token_result.returncode == 0:
                    token = token_result.stdout.strip()
                    console.print("[green]✓ GitHub CLI already authenticated — token retrieved[/green]")
                    return {"token": token}
        except Exception:
            pass

        # Offer gh auth login
        if Confirm.ask("  Run `gh auth login` to authenticate via GitHub CLI?", default=True):
            try:
                subprocess.run(["gh", "auth", "login"], check=True)
                token_result = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True, text=True, timeout=10,
                )
                if token_result.returncode == 0:
                    token = token_result.stdout.strip()
                    console.print("[green]✓ GitHub CLI authentication successful[/green]")
                    return {"token": token}
            except (subprocess.CalledProcessError, Exception) as exc:
                console.print(f"[yellow]  gh auth login failed ({exc}) — falling back to PAT[/yellow]")

    # PAT fallback
    console.print("[dim]Create a PAT at: https://github.com/settings/tokens → Generate new token (classic)[/dim]")
    console.print("[dim]Required scopes: repo, workflow[/dim]")
    token = Prompt.ask("Paste your GitHub personal access token (ghp_...)", password=True)
    if not token:
        console.print("[yellow]  GitHub skipped[/yellow]")
        return {}
    console.print("[green]✓ GitHub token saved[/green]")
    return {"token": token}
