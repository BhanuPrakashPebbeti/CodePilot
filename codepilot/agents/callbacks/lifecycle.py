"""ADK lifecycle callbacks for the LoopAgent pipeline.

Attached as ``before_agent_callback`` / ``after_agent_callback`` on the
LoopAgent and its sub-agents to manage iteration state, reset trackers,
and emit per-iteration timing profiling logs.
"""

import time

from google.adk.agents.callback_context import CallbackContext

from .guardrails import reset_tool_trackers
from ...utils.logger import get_logger

logger = get_logger(__name__)

# Module-level iteration timing registry: iteration_number → start_time
_iteration_start_times: dict[int, float] = {}


def increment_iteration(callback_context: CallbackContext) -> None:
    """``before_agent_callback`` on the LoopAgent.

    Increments ``iteration_count`` in ADK session state, resets per-agent
    tool-call trackers, and records the iteration start time for profiling.
    """
    try:
        current = int(callback_context.state.get("iteration_count", 0))
    except (TypeError, ValueError):
        current = 0

    new_count = current + 1
    callback_context.state["iteration_count"] = str(new_count)
    _iteration_start_times[new_count] = time.monotonic()
    reset_tool_trackers()

    logger.info(
        "[Profiling] Loop iteration %d started",
        new_count,
    )


def log_iteration_end(callback_context: CallbackContext) -> None:
    """``after_agent_callback`` on the LoopAgent — logs iteration elapsed time.

    Wire this as ``after_agent_callback`` on the LoopAgent in builder.py
    to capture how long each full iteration (Developer→Runtime→Test→Debug) took.
    """
    try:
        current = int(callback_context.state.get("iteration_count", 0))
    except (TypeError, ValueError):
        current = 0

    start = _iteration_start_times.get(current)
    if start is not None:
        elapsed = time.monotonic() - start
        logger.info(
            "[Profiling] Loop iteration %d completed in %.1fs",
            current, elapsed,
        )
