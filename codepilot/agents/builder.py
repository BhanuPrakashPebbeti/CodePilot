"""Build the CodePilot ADK multi-agent pipeline.

Refactored architecture
-----------------------
All internal capabilities (filesystem, execution, git, testing, etc.) are
now local Python FunctionTools — no MCP subprocess overhead.

Only external integrations use MCP:
  Playwright → browser UI testing
  GitHub     → repo creation, push, PR (official GitHub MCP)
  Notion     → plan tracking (official Notion MCP)
  Slack      → notifications (official Slack MCP)

Agent pipeline
--------------
CodePilotPipeline (SequentialAgent)
  ├── PlannerAgent    (LlmAgent) — understand + plan → Notion
  ├── DevelopmentLoop (LoopAgent, max_iterations)
  │   ├── DeveloperAgent  (LlmAgent) — write/edit code
  │   ├── RuntimeAgent    (LlmAgent) — build, run, verify
  │   ├── TestAgent       (LlmAgent) — Playwright / HTTP tests
  │   └── DebugAgent      (LlmAgent) — fix or exit loop (ONLY one with exit_loop)
  └── FinalizerAgent  (LlmAgent) — README + git + GitHub + Slack

ReviewAgent removed: Developer already self-corrects; Review added latency
without improving reliability for most errors.

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
"""

from typing import Optional

from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.models.registry import LLMRegistry

from .callbacks import (
    compose_before_tool_callbacks,
    confirm_before_destructive_tool,
    guard_tool_loop,
    increment_iteration,
)
from .mcp_config import (
    get_finalizer_mcp_tools,
    get_planner_mcp_tools,
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
        notion_token:   Notion integration token for plan tracking (optional).
        slack_token:    Slack bot token for notifications (optional).
        max_iterations: Maximum development loop iterations (default 10).

    Returns:
        The root SequentialAgent, ready to run via ``google.adk.runners.Runner``.
    """
    _register_litellm_providers()
    model_str = _resolve_model(provider, model)

    logger.info(
        "Building pipeline: %s/%s (max_iter=%d, notion=%s, slack=%s, github=%s)",
        provider, model, max_iterations,
        bool(notion_token), bool(slack_token), bool(github_token),
    )

    # Composed before_tool_callback:
    #   1. Loop guard    — detects stuck loops
    #   2. Human-in-loop — opt-in destructive confirmation
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
    # Local tools: project analysis, environment, planning, memory
    # External MCP (optional): Notion for plan persistence
    planner = _agent(
        name="PlannerAgent",
        instruction=PLANNER_INSTRUCTION,
        description="Understands the request, checks memory, creates structured plan, optionally persists to Notion.",
        tools=[
            *PLANNER_TOOLS,
            *get_planner_mcp_tools(notion_token),
        ],
        output_key="plan_summary",
        include_contents="default",   # planner needs the initial user message
    )

    # ── 2a. Developer ─────────────────────────────────────────────────────
    # Local tools only: fs, exec, git, workspace, env, planning
    developer = _agent(
        name="DeveloperAgent",
        instruction=DEVELOPER_INSTRUCTION,
        description="Writes code, installs dependencies, scaffolds projects, manages git.",
        tools=DEVELOPER_TOOLS,
        output_key="developer_output",
    )

    # ── 2b. Runtime ───────────────────────────────────────────────────────
    # Local tools only: exec, testing, set_state
    runtime = _agent(
        name="RuntimeAgent",
        instruction=RUNTIME_INSTRUCTION,
        description="Builds, runs, and verifies projects. Sets app_type/app_ready/app_url/runtime_error state.",
        tools=[*RUNTIME_TOOLS, set_state],
        output_key="runtime_output",
    )

    # ── 2c. Test Agent ────────────────────────────────────────────────────
    # Local tools: http_request, run_tests
    # External MCP (optional): Playwright for browser UI testing
    test_agent = _agent(
        name="TestAgent",
        instruction=TEST_INSTRUCTION,
        description="Browser UI testing for web projects via Playwright; HTTP tests for APIs; skips for CLI/library.",
        tools=[
            *TEST_TOOLS,
            set_state,
            *get_test_mcp_tools(),
        ],
        output_key="test_output",
    )

    # ── 2d. Debug Agent ───────────────────────────────────────────────────
    # Local tools: debug, fs, exec, memory, validation
    # ONLY agent with exit_loop — must call check_exit_conditions first
    debug = _agent(
        name="DebugAgent",
        instruction=DEBUG_INSTRUCTION,
        description="Diagnoses failures, applies fixes. Calls check_exit_conditions() then exit_loop when done.",
        tools=[
            *DEBUG_TOOLS,
            set_state,
            exit_loop,       # ONLY the Debug Agent has this
        ],
        output_key="debug_output",
    )

    # ── Development loop ──────────────────────────────────────────────────
    dev_loop = LoopAgent(
        name="DevelopmentLoop",
        sub_agents=[developer, runtime, test_agent, debug],
        max_iterations=max_iterations,
        before_agent_callback=increment_iteration,
    )

    # ── 3. Finalizer ──────────────────────────────────────────────────────
    # Local tools: fs, git, exec, memory
    # External MCP (optional): GitHub + Slack + Notion
    finalizer = _agent(
        name="FinalizerAgent",
        instruction=FINALIZER_INSTRUCTION,
        description="Stops servers, writes README, commits, pushes to GitHub, notifies Slack, saves memory.",
        tools=[
            *FINALIZER_TOOLS,
            set_state,
            *get_finalizer_mcp_tools(github_token, slack_token, notion_token),
        ],
        output_key="final_summary",
    )

    # ── Root pipeline ─────────────────────────────────────────────────────
    root = SequentialAgent(
        name="CodePilotPipeline",
        description=(
            "Autonomous software engineering pipeline: "
            "Plan → Develop → Run → Test → Fix → Finalize. "
            "Language-agnostic: web apps, CLIs, APIs, libraries, scripts."
        ),
        sub_agents=[planner, dev_loop, finalizer],
    )

    logger.info("Pipeline built — model=%s, agents=Planner+Developer+Runtime+Test+Debug+Finalizer", model_str)
    return root
