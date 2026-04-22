"""ADK agent callbacks package.

Organises all ``before_tool_callback``, ``after_tool_callback``, and
``before_agent_callback`` implementations in one place.

Public API
----------
guard_tool_loop               — safety guard against identical-call loops
reset_tool_trackers           — reset per-agent counters (called on each iteration)
increment_iteration           — increment LoopAgent iteration counter
confirm_before_destructive_tool — human-in-the-loop confirmation (opt-in)
compose_before_tool_callbacks — utility: chain multiple before_tool_callbacks
"""

from typing import Callable, Optional

from .guardrails import guard_tool_loop, reset_tool_trackers
from .lifecycle import increment_iteration
from .human_in_loop import confirm_before_destructive_tool


def compose_before_tool_callbacks(*fns: Callable) -> Callable:
    """Chain multiple ``before_tool_callback`` functions into one.

    ADK supports only a single ``before_tool_callback`` per agent.  This
    helper composes an arbitrary number of callbacks so they all run in
    order, short-circuiting on the first non-``None`` return value.

    Example::

        agent = LlmAgent(
            before_tool_callback=compose_before_tool_callbacks(
                guard_tool_loop,
                confirm_before_destructive_tool,
            ),
        )
    """
    def _composed(tool, args: dict, tool_context) -> Optional[dict]:
        for fn in fns:
            result = fn(tool, args, tool_context)
            if result is not None:
                return result
        return None
    return _composed


__all__ = [
    "guard_tool_loop",
    "reset_tool_trackers",
    "increment_iteration",
    "confirm_before_destructive_tool",
    "compose_before_tool_callbacks",
]
