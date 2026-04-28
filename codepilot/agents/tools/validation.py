"""Exit-condition validation — the ONLY gateway to loop termination.

The Debug Agent MUST call check_exit_conditions() before calling exit_loop().
This function enforces strict multi-condition checks so the loop cannot
exit prematurely because one agent misunderstood state.

Termination conditions (ALL must be true to exit with SUCCESS):
  1. app_ready == "true"
  2. runtime_error is empty
  3. test_result is "PASS" or starts with "SKIP" (non-web projects)
  4. At least 1 loop iteration completed

If conditions are NOT met, the function explains exactly what's still failing
so the Debug Agent knows what to fix next instead of exiting early.
"""

from google.adk.tools.tool_context import ToolContext

from ...utils.logger import get_logger

logger = get_logger(__name__)


def check_exit_conditions(tool_context: ToolContext) -> dict:
    """Evaluate whether all success conditions are met before exiting the loop.

    Call this BEFORE exit_loop. Only call exit_loop if this returns
    can_exit=True with status="success".

    Returns:
        dict with:
          can_exit (bool)       — True means it is safe to call exit_loop
          status (str)          — "success", "failure", or "partial"
          blocking (list[str])  — conditions that are still failing
          summary (str)         — human-readable explanation
    """
    state = tool_context.state
    app_ready = str(state.get("app_ready", "false")).lower()
    runtime_error = (state.get("runtime_error") or "").strip()
    test_result = (state.get("test_result") or "").strip()
    iteration = int(state.get("iteration_count") or "0")
    app_type = (state.get("app_type") or "").strip()

    blocking = []

    # Condition 1: must have run at least one iteration
    if iteration < 1:
        blocking.append("No iterations completed yet — loop just started")

    # Condition 2: project must be ready
    if app_ready != "true":
        blocking.append(f"app_ready={app_ready!r} — project has not been confirmed ready by RuntimeAgent")

    # Condition 3: no outstanding runtime errors
    if runtime_error:
        blocking.append(f"runtime_error is set: {runtime_error[:200]}")

    # Condition 4: test result must be PASS or SKIP (never FAIL or empty for web)
    if test_result.startswith("FAIL"):
        blocking.append(f"test_result={test_result[:200]}")
    elif not test_result and app_type in ("web", "fullstack", "api"):
        blocking.append(
            f"test_result is empty for app_type={app_type!r} — "
            "TestAgent has not reported results yet"
        )

    if not blocking:
        logger.info("Exit conditions met after %d iterations — SUCCESS", iteration)
        return {
            "can_exit": True,
            "status": "success",
            "blocking": [],
            "summary": (
                f"All conditions passed after {iteration} iteration(s). "
                f"app_ready=true, no runtime errors, test_result={test_result!r}. "
                "Safe to call exit_loop with final_status=SUCCESS."
            ),
        }

    logger.info("Exit blocked: %s", blocking)
    return {
        "can_exit": False,
        "status": "failure",
        "blocking": blocking,
        "summary": (
            f"Cannot exit yet — {len(blocking)} condition(s) not met:\n"
            + "\n".join(f"  • {b}" for b in blocking)
            + "\nFix these before calling exit_loop."
        ),
    }


def force_exit_conditions(tool_context: ToolContext) -> dict:
    """Force exit after exhausting all fix attempts (partial/failed outcome).

    Call this ONLY after 3+ failed fix attempts on the same error, or
    when the Debug Agent determines further iteration will not help.
    Sets final_status to PARTIAL/FAILED automatically based on state.

    Returns:
        dict with forced_status and summary.
    """
    state = tool_context.state
    runtime_error = (state.get("runtime_error") or "").strip()
    test_result = (state.get("test_result") or "").strip()
    iteration = int(state.get("iteration_count") or "0")

    if runtime_error:
        forced_status = f"FAILED: {runtime_error[:300]}"
    elif test_result.startswith("FAIL"):
        forced_status = f"PARTIAL: {test_result[:300]}"
    else:
        forced_status = "PARTIAL: loop exhausted without clear error"

    tool_context.state["final_status"] = forced_status
    logger.warning("Force exit after %d iterations: %s", iteration, forced_status)

    return {
        "ok": True,
        "forced_status": forced_status,
        "summary": (
            f"Forcing loop exit after {iteration} iteration(s). "
            f"Status: {forced_status}. "
            "Calling exit_loop now."
        ),
    }
