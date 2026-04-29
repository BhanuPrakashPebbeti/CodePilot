"""Build the CodePilot ADK multi-agent pipeline.

Architecture
------------
All internal capabilities (filesystem, execution, git, testing, memory,
Notion, Slack) are local Python FunctionTools — no MCP subprocess overhead.

Only external integrations use MCP:
  Playwright → browser UI testing                    (TestAgent)
  GitHub     → repo creation, push, PR (official MCP) (FinalizerAgent)

Notion and Slack now use local Python tools (notion_tools + slack_hitl)
for reliable schema control and human-in-the-loop decisions.

Agent pipeline
--------------
CodePilotPipeline (SequentialAgent)
  ├── PlannerAgent    (LlmAgent) — plan + Notion project/task creation
  ├── DevelopmentLoop (LoopAgent, max_iterations)
  │   ├── DeveloperAgent  (LlmAgent) — write/edit code + conventional commits
  │   ├── RuntimeAgent    (LlmAgent) — build, run, verify + Notion/Slack on fail
  │   ├── TestAgent       (LlmAgent) — Playwright / HTTP tests + screenshots
  │   └── DebugAgent      (LlmAgent) — fix or exit loop + HITL via Slack
  └── FinalizerAgent  (LlmAgent) — README + git + GitHub PR + Notion + Slack

Guardrails (ADK-native callbacks)
----------------------------------
before_tool_callback  → compose(guard_tool_loop, confirm_before_destructive_tool)
before_agent_callback → increment_iteration (LoopAgent only)
on_tool_error_callback → _handle_tool_error (hallucinated tool names)

Exit-loop enforcement
---------------------
Only DebugAgent has access to exit_loop.
DebugAgent must call check_exit_conditions() first.
exit_loop itself also logs final_status for audit.

Traceability
------------
Every agent writes to Notion (project page with tasks + execution log).
DebugAgent can trigger Slack HITL when stuck.
FinalizerAgent creates a GitHub PR and sends a Slack notification.
"""

from typing import Optional

from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.models.registry import LLMRegistry

from .callbacks import (
    compose_before_tool_callbacks,
    confirm_before_destructive_tool,
    guard_tool_loop,
    increment_iteration,
    log_iteration_end,
    record_iteration_state,
)
from .mcp_config import (
    get_finalizer_mcp_tools,
    get_test_mcp_tools,
)
from .prompts import (
    DEBUG_INSTRUCTION,
    DEVELOPER_INSTRUCTION,
    FINALIZER_INSTRUCTION,
    PLANNER_INSTRUCTION,
    RUNTIME_INSTRUCTION,
    TEST_INSTRUCTION,
)
from .tools import (
    DEBUG_TOOLS,
    DEVELOPER_TOOLS,
    FINALIZER_TOOLS,
    PLANNER_TOOLS,
    RUNTIME_TOOLS,
    TEST_TOOLS,
    exit_loop,
    set_state,
)
from ..utils.logger import get_logger

logger = get_logger(__name__)

_litellm_registered = False


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------

def _register_litellm_providers() -> None:
    global _litellm_registered
    if _litellm_registered:
        return
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError:
        logger.warning("LiteLlm not found — only Gemini models will work")
        return
    for pattern in [
        r"openrouter/.*", r"ollama/.*", r"together_ai/.*",
        r"deepseek/.*", r"mistral/.*", r"fireworks_ai/.*",
        r"huggingface/.*", r"cohere/.*", r"bedrock/.*",
    ]:
        LLMRegistry._register(pattern, LiteLlm)
    _litellm_registered = True


def _resolve_model(provider: str, model: str) -> str:
    if provider == "gemini":
        return model
    if provider == "ollama":
        return f"ollama/{model}"
    if provider == "openrouter":
        return f"openrouter/{model}"
    return model


# ---------------------------------------------------------------------------
# Tool-error callback
# ---------------------------------------------------------------------------

def _handle_tool_error(tool, args: dict, tool_context, error: Exception) -> Optional[dict]:
    msg = str(error)
    if "not found" in msg.lower():
        avail = msg.split("Available tools:")[1].split("\n")[0].strip() if "Available tools:" in msg else ""
        return {
            "ok": False,
            "error": (
                f"Tool '{tool.name}' does not exist. "
                f"Only call tools listed as available to you. "
                f"Available: {avail}. Retry with a valid tool name."
            ),
        }
    return None


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------

def build_codepilot_agent(
    provider: str = "ollama",
    model: str = "mistral",
    api_key: Optional[str] = None,
    github_token: Optional[str] = None,
    notion_token: Optional[str] = None,
    slack_token: Optional[str] = None,
    max_iterations: int = 10,
) -> SequentialAgent:
    """Assemble and return the CodePilot ADK pipeline.

    Args:
        provider:       LLM provider — "ollama", "openrouter", or "gemini".
        model:          Model name for the chosen provider.
        api_key:        API key for cloud providers (OpenRouter).
        github_token:   GitHub PAT for repo creation and PR (optional).
                        Also read from GITHUB_PERSONAL_ACCESS_TOKEN env var.
        notion_token:   Notion integration token (optional).
                        Also read from NOTION_TOKEN env var.
                        Local notion_tools read NOTION_TOKEN directly.
        slack_token:    Slack bot token (optional).
                        Also read from SLACK_BOT_TOKEN env var.
                        Local slack_hitl tools read SLACK_BOT_TOKEN directly.
        max_iterations: Maximum development loop iterations (default 10).

    Returns:
        The root SequentialAgent, ready to run via ``google.adk.runners.Runner``.
    """
    import os

    _register_litellm_providers()
    model_str = _resolve_model(provider, model)

    # Inject tokens into env so local tools (notion_tools, slack_hitl) pick them up.
    # The MCP servers read from env too, so this is a single source of truth.
    if notion_token:
        os.environ.setdefault("NOTION_TOKEN", notion_token)
    if slack_token:
        os.environ.setdefault("SLACK_BOT_TOKEN", slack_token)
    if github_token:
        os.environ.setdefault("GITHUB_PERSONAL_ACCESS_TOKEN", github_token)
        os.environ.setdefault("GITHUB_TOKEN", github_token)

    logger.info(
        "Building pipeline: %s/%s (max_iter=%d, notion=%s, slack=%s, github=%s)",
        provider, model, max_iterations,
        bool(notion_token or os.environ.get("NOTION_TOKEN")),
        bool(slack_token or os.environ.get("SLACK_BOT_TOKEN")),
        bool(github_token or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")),
    )

    # Composed before_tool_callback:
    #   1. Loop guard    — detects stuck/runaway loops
    #   2. Human-in-loop — opt-in destructive op confirmation
    tool_guard = compose_before_tool_callbacks(guard_tool_loop, confirm_before_destructive_tool)

    def _agent(**kwargs) -> LlmAgent:
        kwargs.setdefault("include_contents", "none")
        return LlmAgent(
            model=model_str,
            before_tool_callback=tool_guard,
            on_tool_error_callback=_handle_tool_error,
            **kwargs,
        )

    # ── 1. Planner ────────────────────────────────────────────────────────
    # Tools: workspace + env + planning + memory + notion_create/add_task/log + set_state
    # set_state is already in PLANNER_TOOLS (for notion_project_id storage).
    planner = _agent(
        name="PlannerAgent",
        instruction=PLANNER_INSTRUCTION,
        description=(
            "Understands request, checks memory, creates structured plan, "
            "creates Notion project page with tasks for external visibility."
        ),
        tools=PLANNER_TOOLS,   # includes set_state + notion tools
        output_key="plan_summary",
        include_contents="default",   # planner needs the initial user message
    )

    # ── 2a. Developer ─────────────────────────────────────────────────────
    # Tools: fs + exec + git + workspace + env + planning + notion_update_task/log
    developer = _agent(
        name="DeveloperAgent",
        instruction=DEVELOPER_INSTRUCTION,
        description=(
            "Writes code, installs dependencies, scaffolds projects, manages git. "
            "Uses conventional commit messages. Updates Notion task status."
        ),
        tools=DEVELOPER_TOOLS,
        output_key="developer_output",
    )

    # ── 2b. Runtime ───────────────────────────────────────────────────────
    # Tools: exec + testing + notion_log + slack_notify + set_state
    runtime = _agent(
        name="RuntimeAgent",
        instruction=RUNTIME_INSTRUCTION,
        description=(
            "Builds, runs, and verifies projects. Sets app_type/app_ready/app_url/"
            "runtime_error state. Logs to Notion and notifies Slack on failure."
        ),
        tools=[*RUNTIME_TOOLS, set_state],
        output_key="runtime_output",
    )

    # ── 2c. Test Agent ────────────────────────────────────────────────────
    # Tools: http_request + run_tests + notion_update/log + set_state
    # External MCP (optional): Playwright for browser UI testing + screenshots
    test_agent = _agent(
        name="TestAgent",
        instruction=TEST_INSTRUCTION,
        description=(
            "Browser UI testing for web projects (Playwright + screenshots); "
            "HTTP tests for APIs; skips for CLI/library. Logs to Notion."
        ),
        tools=[
            *TEST_TOOLS,
            set_state,
            *get_test_mcp_tools(),
        ],
        output_key="test_output",
    )

    # ── 2d. Debug Agent ───────────────────────────────────────────────────
    # Tools: debug + fs + exec + memory + validation + notion + slack HITL + set_state
    # ONLY agent with exit_loop — must call check_exit_conditions first
    debug = _agent(
        name="DebugAgent",
        instruction=DEBUG_INSTRUCTION,
        description=(
            "Diagnoses failures, applies fixes. Uses Slack HITL after 3+ failed "
            "attempts. Calls check_exit_conditions() then exit_loop when done."
        ),
        tools=[
            *DEBUG_TOOLS,
            set_state,
            exit_loop,       # ONLY the Debug Agent has this
        ],
        output_key="debug_output",
    )

    def _after_loop_iteration(callback_context) -> None:
        """Record state fingerprint for no-op detection + log elapsed time."""
        record_iteration_state(dict(callback_context.state))
        log_iteration_end(callback_context)

    # ── Development loop ──────────────────────────────────────────────────
    dev_loop = LoopAgent(
        name="DevelopmentLoop",
        sub_agents=[developer, runtime, test_agent, debug],
        max_iterations=max_iterations,
        before_agent_callback=increment_iteration,
        after_agent_callback=_after_loop_iteration,
    )

    # ── 3. Finalizer ──────────────────────────────────────────────────────
    # Tools: fs + git + exec + memory + notion_update_project/log + slack_notify + set_state
    # External MCP (optional): GitHub for repo creation + PR
    finalizer = _agent(
        name="FinalizerAgent",
        instruction=FINALIZER_INSTRUCTION,
        description=(
            "Stops servers, writes README, final git commit, pushes to GitHub, "
            "creates PR, marks project COMPLETED in Notion, notifies Slack."
        ),
        tools=[
            *FINALIZER_TOOLS,
            set_state,
            *get_finalizer_mcp_tools(github_token),
        ],
        output_key="final_summary",
    )

    # ── Root pipeline ─────────────────────────────────────────────────────
    root = SequentialAgent(
        name="CodePilotPipeline",
        description=(
            "Autonomous software engineering pipeline: "
            "Plan → Develop → Run → Test → Fix → Finalize. "
            "Language-agnostic. Integrated with Notion (traceability), "
            "Slack (HITL), and GitHub (delivery)."
        ),
        sub_agents=[planner, dev_loop, finalizer],
    )

    logger.info(
        "Pipeline built — model=%s, agents=Planner+Developer+Runtime+Test+Debug+Finalizer",
        model_str,
    )
    return root
