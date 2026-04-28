"""ADK agent control tools — set_state and exit_loop.

exit_loop is GATED: Debug Agent must call check_exit_conditions() first
and confirm can_exit=True. This prevents premature loop termination.

Only the DebugAgent receives exit_loop in its tool list.
"""

from google.adk.tools.tool_context import ToolContext

from ...utils.logger import get_logger

logger = get_logger(__name__)

ALLOWED_STATE_KEYS = frozenset({
    # Core pipeline state
    "app_type",           # "web" | "api" | "fullstack" | "cli" | "library" | "script"
    "app_url",            # primary URL for web/API testing
    "app_ready",          # "true" when app is built and responding
    "runtime_error",      # error details from Runtime Agent (empty = no error)
    "test_result",        # "PASS" | "FAIL: ..." | "SKIP: ..."
    "test_errors",        # verbose error output from the Test Agent
    "debug_log",          # summary of what the Debug Agent fixed
    "final_status",       # "SUCCESS" | "PARTIAL: ..." | "FAILED: ..."
    # Traceability & external integrations
    "notion_project_id",  # Notion page ID for this project (set by PlannerAgent)
    "github_repo_url",    # GitHub repo URL after creation (set by FinalizerAgent)
    "hitl_decision",      # Last human-in-the-loop decision, e.g. "1: Retry fixing"
    "screenshot_paths",   # Comma-separated list of captured screenshot file paths
})


def set_state(key: str, value: str, tool_context: ToolContext) -> dict:
    """Set a shared state variable for inter-agent communication.

    Allowed keys: app_type, app_url, app_ready, runtime_error,
    test_result, test_errors, debug_log, final_status.

    Args:
        key:   State variable name (must be in the allowed set).
        value: String value to store.
    """
    if key not in ALLOWED_STATE_KEYS:
        return {
            "ok": False,
            "error": (
                f"Unknown state key '{key}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_STATE_KEYS))}"
            ),
        }
    tool_context.state[key] = value
    logger.info("set_state: %s = %.200s", key, value)
    return {"ok": True, "key": key, "value": value}


def exit_loop(reason: str, tool_context: ToolContext) -> dict:
    """Terminate the development loop.

    Call ONLY after check_exit_conditions() returns can_exit=True,
    OR after force_exit_conditions() when retries are exhausted.
    Always call set_state(key="final_status", ...) before this.

    Args:
        reason: Why the loop is exiting (e.g. "All tests pass").
    """
    final_status = tool_context.state.get("final_status", "")
    if not final_status:
        logger.warning("exit_loop called without final_status — defaulting to FAILED")
        tool_context.state["final_status"] = "FAILED: exit_loop called without final_status"

    logger.info("Loop terminating: %s | final_status=%s", reason, final_status)
    tool_context.actions.escalate = True
    tool_context.actions.skip_summarization = True
    return {"status": "loop_terminated", "reason": reason, "final_status": final_status}
