"""ADK agent control tools — inter-agent communication and loop control.

These are *ADK function tools* (plain Python functions decorated with no
special decorator — ADK discovers them by type annotation).

Only two responsibilities live here:
  1. ``set_state``  — structured state passing between pipeline stages
  2. ``exit_loop``  — Debug Agent signals the LoopAgent to terminate

Safety guardrails and lifecycle hooks have moved to ``callbacks/``.
"""

from google.adk.tools.tool_context import ToolContext

from ..utils.logger import get_logger

logger = get_logger(__name__)

# Keys agents are allowed to write.  Any other key is rejected to prevent
# agents from inventing ad-hoc state variables that break the pipeline.
ALLOWED_STATE_KEYS = frozenset({
    "app_type",       # "web" | "api" | "fullstack" | "cli" | "library" | "script"
    "app_url",        # primary URL for web/API testing
    "app_ready",      # "true" when project is built and ready for testing
    "runtime_error",  # error details from Runtime Agent (empty = no error)
    "test_result",    # "PASS" | "FAIL: ..." | "SKIP: ..."
    "test_errors",    # detailed error output from the Test Agent
    "debug_log",      # summary of what the Debug Agent fixed
    "final_status",   # "SUCCESS" | "PARTIAL: ..." | "FAILED: ..."
})


def set_state(key: str, value: str, tool_context: ToolContext) -> dict:
    """Set a shared state variable for inter-agent communication.

    This is the *only* way for agents to pass structured information to
    each other across pipeline stages.  Use it for status flags, error
    details, test results, and project metadata.

    Allowed keys
    ------------
    app_type      — project type detected by Runtime Agent
    app_url       — primary URL (web/API projects only)
    app_ready     — "true" when the app is built and responding
    runtime_error — error text from Runtime Agent (empty = success)
    test_result   — "PASS", "FAIL: <details>", or "SKIP: <reason>"
    test_errors   — verbose error output from the Test Agent
    debug_log     — human-readable summary of fixes applied
    final_status  — overall outcome written by the Debug Agent before exit

    Args:
        key:   State variable name (must be one of the allowed keys above).
        value: String value to store.
    """
    if key not in ALLOWED_STATE_KEYS:
        return {
            "ok": False,
            "error": (
                f"Unknown state key '{key}'. "
                f"Allowed keys: {', '.join(sorted(ALLOWED_STATE_KEYS))}"
            ),
        }
    tool_context.state[key] = value
    logger.info("set_state: %s = %.200s", key, value)
    return {"ok": True, "key": key, "value": value}


def exit_loop(tool_context: ToolContext) -> dict:
    """Signal the development LoopAgent to terminate.

    Call this when the project is working (all tests pass) OR when
    further iterations would not help (debug exhausted).

    IMPORTANT: Always call ``set_state(key="final_status", value=...)``
    *before* calling ``exit_loop``.  The Finalizer Agent reads this value.

    Valid ``final_status`` values:
      "SUCCESS"             — all checks passed
      "PARTIAL: <details>"  — partially working, with known issues
      "FAILED: <reason>"    — could not get the project working
    """
    tool_context.actions.escalate = True
    tool_context.actions.skip_summarization = True
    return {"status": "loop_terminated"}
