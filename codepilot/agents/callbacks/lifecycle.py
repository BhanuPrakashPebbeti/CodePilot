"""ADK lifecycle callbacks for the LoopAgent pipeline.

Attached as ``before_agent_callback`` / ``after_agent_callback`` on the
LoopAgent and its sub-agents to manage iteration state and reset trackers.
"""

from google.adk.agents.callback_context import CallbackContext

from .guardrails import reset_tool_trackers
from ...utils.logger import get_logger

logger = get_logger(__name__)


def increment_iteration(callback_context: CallbackContext) -> None:
    """``before_agent_callback`` on the LoopAgent.

    Increments ``iteration_count`` in ADK session state and resets the
    per-agent tool-call trackers so the guardrails count from zero on
    each new iteration rather than accumulating across the whole loop.
    """
    try:
        current = int(callback_context.state.get("iteration_count", 0))
    except (TypeError, ValueError):
        current = 0
    callback_context.state["iteration_count"] = str(current + 1)
    reset_tool_trackers()
    logger.debug("Loop iteration %d started", current + 1)
