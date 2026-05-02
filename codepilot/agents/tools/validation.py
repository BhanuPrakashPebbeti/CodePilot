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

    def _str(key: str, default: str = "") -> str:
        val = state.get(key)
        return str(val).strip() if val is not None else default

    def _int(key: str, default: int = 0) -> int:
        val = state.get(key)
        if val is None:
            return default
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    app_ready = _str("app_ready", "false").lower()
    runtime_error = _str("runtime_error")
    test_result = _str("test_result")
    iteration = _int("iteration_count", 0)
    app_type = _str("app_type")

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

    # Condition 4: test result must be PASS or start with SKIP.
    # Any other value (FAIL, IN_PROGRESS, empty for web/api) blocks exit.
    _valid_result = test_result == "PASS" or test_result.startswith("SKIP")
    if test_result.startswith("FAIL"):
        blocking.append(f"test_result={test_result[:200]} — tests are failing")
    elif not _valid_result and app_type in ("web", "fullstack", "api"):
        if not test_result:
            blocking.append(
                f"test_result is empty for app_type={app_type!r} — "
                "TestAgent has not reported results yet"
            )
        else:
            blocking.append(
                f"test_result={test_result!r} is not a valid result for app_type={app_type!r}. "
                "TestAgent must set test_result to exactly 'PASS' or 'SKIP: <reason>'. "
                "Values like 'IN_PROGRESS' are invalid — the agent is still testing, "
                "not reporting a final result."
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

    def _str(key: str) -> str:
        val = state.get(key)
        return str(val).strip() if val is not None else ""

    def _int(key: str) -> int:
        val = state.get(key)
        try:
            return int(val) if val is not None else 0
        except (TypeError, ValueError):
            return 0

    runtime_error = _str("runtime_error")
    test_result = _str("test_result")
    iteration = _int("iteration_count")

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
