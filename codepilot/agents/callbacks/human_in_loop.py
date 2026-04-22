"""Human-in-the-loop confirmation for destructive operations.

Implements the ``before_tool_callback`` ADK hook to pause execution and
ask the user for confirmation before irreversible or high-impact actions.

Activation:
  Set ``CODEPILOT_CONFIRM_DESTRUCTIVE=true`` (or ``1`` / ``yes``) in the
  environment before running CodePilot.  When unset, this callback is a
  no-op and adds zero overhead.

Agentic AI concept demonstrated:
  Human-in-the-loop control — a fundamental safety pattern for autonomous
  agents operating on real systems.  The agent proposes an action; the
  human approves or vetoes it before execution.
"""

import os
import re
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from ...utils.logger import get_logger

logger = get_logger(__name__)

# Tools that are always destructive regardless of arguments
_ALWAYS_CONFIRM = frozenset({
    "delete_file",
    "push_to_github",
    "create_repo",
})

# Shell command patterns that are dangerous
# Each entry is (regex_pattern, human_readable_description)
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-[rf]*r[rf]*\s+/", "recursive delete from filesystem root"),
    (r"git\s+push\s+--force(?!-with-lease)", "destructive force push (overwrites remote history)"),
    (r"(?i)DROP\s+(TABLE|DATABASE|SCHEMA)\b", "SQL DROP statement — permanently destroys data"),
    (r"(?i)DELETE\s+FROM\b(?!\s+\S+\s+WHERE)", "unbounded SQL DELETE (no WHERE clause)"),
    (r"\bmkfs\.", "filesystem format — erases all data on device"),
    (r"\bdd\s+.*\bof=/dev/", "raw disk write via dd — can destroy partitions"),
    (r">\s*/dev/(s|h|v|x)d[a-z]", "redirect output to raw block device"),
]


def _is_active() -> bool:
    """Return True when the human-in-the-loop guard is enabled."""
    return os.environ.get("CODEPILOT_CONFIRM_DESTRUCTIVE", "").lower() in (
        "1", "true", "yes",
    )


def _classify(tool_name: str, args: dict) -> Optional[str]:
    """Return a human-readable reason if the operation is destructive, else None."""
    if tool_name in _ALWAYS_CONFIRM:
        return f"'{tool_name}' may have irreversible side-effects"

    if tool_name == "run_command":
        cmd = args.get("command", "")
        for pattern, desc in _DANGEROUS_PATTERNS:
            if re.search(pattern, cmd):
                return desc

    return None


def confirm_before_destructive_tool(
    tool,
    args: dict,
    tool_context: ToolContext,
) -> Optional[dict]:
    """``before_tool_callback`` — pause and ask the user before destructive ops.

    Returns ``None`` (proceed) or an error dict (block + inform the agent).

    ADK contract:
      - ``None``  → execute the tool as normal
      - ``dict``  → skip tool execution, deliver this as the tool response
    """
    if not _is_active():
        return None

    reason = _classify(tool.name, args)
    if reason is None:
        return None

    # ── Display the pending operation ────────────────────────────────────
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.prompt import Confirm

        console = Console()
        lines = [f"[bold red]Destructive operation detected[/bold red]: {reason}"]
        lines.append(f"  [dim]tool:[/dim] {tool.name}")
        for k, v in list(args.items())[:4]:
            lines.append(f"  [dim]{k}:[/dim] {str(v)[:120]}")
        console.print(Panel("\n".join(lines), border_style="red"))

        confirmed = Confirm.ask("\nAllow this operation?", default=False)
    except (EOFError, KeyboardInterrupt):
        confirmed = False

    if not confirmed:
        logger.info("User DENIED: %s(%s)", tool.name, args)
        return {
            "ok": False,
            "error": (
                f"Operation cancelled by the user. "
                f"The user did not approve: {reason}. "
                "Choose a safer alternative or explain why this is necessary."
            ),
        }

    logger.info("User APPROVED: %s(%s)", tool.name, args)
    return None
