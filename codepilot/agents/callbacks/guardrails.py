"""ADK callback-based safety guardrails.

Uses ADK's native ``before_tool_callback`` mechanism — the correct ADK
pattern for intercepting tool calls without monkey-patching internals.

Guardrail levels (all implemented as a single composed callback):
  1. Soft nudge  — 3+ identical consecutive calls: ask the agent to reason
  2. Hard stop   — 8+ identical consecutive calls: escalate out of the agent
  3. Safety net  — 200+ total calls per agent: force-escalate (runaway loop)

The guards are intentionally lenient — agents should self-correct via
intelligent reasoning.  These only catch genuinely stuck loops.
"""

import hashlib
import json
from typing import Any, Dict, Optional

from google.adk.tools.tool_context import ToolContext

from ...utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

CONSECUTIVE_NUDGE = 3       # soft: ask agent to reconsider
CONSECUTIVE_HARD_STOP = 8   # hard: escalate out of agent turn
ABSOLUTE_SAFETY_NET = 200   # runaway: force-escalate no matter what

# Per-agent call tracking.  Reset at the start of each LoopAgent iteration
# via ``reset_tool_trackers()`` (called from lifecycle.py).
_trackers: Dict[str, Dict[str, Any]] = {}


def reset_tool_trackers() -> None:
    """Clear all per-agent trackers.  Called at each LoopAgent iteration."""
    _trackers.clear()


def _sig(tool_name: str, args: dict) -> str:
    """Compact fingerprint of a (tool, args) pair for identical-call detection."""
    try:
        raw = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
    except (TypeError, ValueError):
        raw = f"{tool_name}:{args!r}"
    return hashlib.md5(raw.encode()).hexdigest()  # noqa: S324 — not crypto


def guard_tool_loop(
    tool,
    args: dict,
    tool_context: ToolContext,
) -> Optional[dict]:
    """``before_tool_callback`` — ADK-native safety guard against stuck loops.

    Returns ``None`` to let the call proceed normally, or a dict error
    response to block the call and deliver feedback to the LLM.

    ADK contract:
      - Returning ``None``  → execute the tool normally
      - Returning a ``dict`` → skip tool execution, return this dict as
        the tool response (the LLM sees it as a tool result)
      - Setting ``tool_context.actions.escalate = True`` → escalate out
        of the current LoopAgent iteration
    """
    agent = getattr(tool_context, "agent_name", "unknown")
    sig = _sig(tool.name, args)

    if agent not in _trackers:
        _trackers[agent] = {"last_sig": None, "consecutive": 0, "total": 0}

    t = _trackers[agent]
    t["total"] += 1

    if sig == t["last_sig"]:
        t["consecutive"] += 1
    else:
        t["last_sig"] = sig
        t["consecutive"] = 1

    # ── Absolute safety net ──────────────────────────────────────────────
    if t["total"] > ABSOLUTE_SAFETY_NET:
        logger.error(
            "SAFETY NET: '%s' reached %d total tool calls — escalating",
            agent, t["total"],
        )
        tool_context.actions.escalate = True
        tool_context.actions.skip_summarization = True
        return {
            "ok": False,
            "error": (
                f"SAFETY NET: {t['total']} total tool calls made. "
                "You MUST produce your final output now and hand off."
            ),
        }

    # ── Hard stop: identical consecutive calls ───────────────────────────
    if t["consecutive"] >= CONSECUTIVE_HARD_STOP:
        logger.error(
            "HARD STOP: '%s' called %dx identically by '%s' — escalating",
            tool.name, t["consecutive"], agent,
        )
        tool_context.actions.escalate = True
        tool_context.actions.skip_summarization = True
        return {
            "ok": False,
            "error": (
                f"STOP: '{tool.name}' called {t['consecutive']} times with "
                "identical arguments. You are stuck. "
                "The next pipeline stage will take over."
            ),
        }

    # ── Soft nudge: identical consecutive calls ──────────────────────────
    if t["consecutive"] > CONSECUTIVE_NUDGE:
        logger.warning(
            "NUDGE: '%s' called %dx identically by '%s'",
            tool.name, t["consecutive"], agent,
        )
        return {
            "ok": False,
            "error": (
                f"You have called '{tool.name}' with identical arguments "
                f"{t['consecutive']} times in a row.  Repeating a failing "
                "call will NOT produce a different result.\n\n"
                "STOP and REASON:\n"
                "1. WHY did this call fail or not produce the expected result?\n"
                "2. What is the ROOT CAUSE?\n"
                "3. What DIFFERENT approach, tool, or arguments would work?\n\n"
                "Think step-by-step, then try a fundamentally different approach."
            ),
        }

    return None
