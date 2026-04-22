"""Build the ADK multi-agent pipeline for CodePilot.

Pipeline architecture
---------------------
SequentialAgent (root — "CodePilotPipeline")
  ├── PlannerAgent       (LlmAgent)
  ├── DevelopmentLoop    (LoopAgent, configurable max_iterations)
  │   ├── DeveloperAgent (LlmAgent)
  │   ├── ReviewAgent    (LlmAgent)
  │   ├── RuntimeAgent   (LlmAgent)
  │   ├── TestAgent      (LlmAgent)
  │   └── DebugAgent     (LlmAgent — can call exit_loop)
  └── FinalizerAgent     (LlmAgent)

Why SequentialAgent?
--------------------
The outer pipeline is *deterministic*: planning must complete before
development begins, and finalisation must happen after the loop ends.
SequentialAgent is the correct ADK primitive for a fixed-order workflow.

The alternative — an LlmAgent orchestrator that calls specialist agents
as ``AgentTool`` sub-agents — is more flexible but less reliable with
smaller/weaker models because the orchestrator itself must make correct
routing decisions.  For a coding assistant where the workflow is known
in advance, SequentialAgent + LoopAgent gives better reliability.

Guardrails (ADK-native callbacks)
----------------------------------
All safety logic is expressed through ADK's callback system:

  before_tool_callback  → composed chain of:
                            1. ``guard_tool_loop``               (stuck-loop detection)
                            2. ``confirm_before_destructive_tool`` (human-in-the-loop, opt-in)
  before_agent_callback → ``increment_iteration``                (LoopAgent only)
  on_tool_error_callback → ``_handle_tool_error``               (hallucinated tools)

This is fully ADK-native — no monkey-patching of ADK internals is needed
for guardrails.  (ADK patches for provider compatibility are in patches.py.)
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
    get_browser_tools,
    get_debug_tools,
    get_developer_tools,
    get_finalizer_tools,
    get_planner_tools,
    get_review_tools,
    get_runtime_tools,
)
from .prompts import (
    BROWSER_INSTRUCTION,
    DEBUG_INSTRUCTION,
    DEVELOPER_INSTRUCTION,
    FINALIZER_INSTRUCTION,
    PLANNER_INSTRUCTION,
    REVIEW_INSTRUCTION,
    RUNTIME_INSTRUCTION,
)
from .tools import exit_loop, set_state
from ..utils.logger import get_logger

logger = get_logger(__name__)

_litellm_registered = False


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------

def _register_litellm_providers() -> None:
    """Register non-Gemini providers with ADK's LLMRegistry via LiteLLM.

    ADK registers openai/, groq/, and anthropic/ by default.  We add the
    patterns needed for OpenRouter, Ollama, and other providers.
    """
    global _litellm_registered
    if _litellm_registered:
        return

    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError:
        logger.warning(
            "google.adk.models.lite_llm not found — non-Gemini models will "
            "not work.  Install with: pip install 'google-adk[extensions]'"
        )
        return

    patterns = [
        r"openrouter/.*",
        r"ollama/.*",
        r"together_ai/.*",
        r"deepseek/.*",
        r"mistral/.*",
        r"fireworks_ai/.*",
        r"huggingface/.*",
        r"cohere/.*",
        r"replicate/.*",
        r"bedrock/.*",
        r"vertex_ai/.*",
    ]
    for p in patterns:
        LLMRegistry._register(p, LiteLlm)

    _litellm_registered = True
    logger.info("LiteLLM providers registered with ADK LLMRegistry")


# ---------------------------------------------------------------------------
# Model string resolution
# ---------------------------------------------------------------------------

def _resolve_model(provider: str, model: str) -> str:
    """Convert a CodePilot provider/model pair to an ADK model string."""
    if provider == "gemini":
        return model
    if provider == "ollama":
        return f"ollama/{model}"
    if provider == "openrouter":
        return f"openrouter/{model}"
    return model


# ---------------------------------------------------------------------------
# Tool-error callback (hallucinated tool names)
# ---------------------------------------------------------------------------

def _handle_tool_error(tool, args: dict, tool_context, error: Exception) -> Optional[dict]:
    """``on_tool_error_callback`` — catch hallucinated tool names gracefully.

    Without this, ADK raises ValueError and kills the pipeline when the
    LLM invokes a non-existent tool.  We return a helpful error so the
    model can self-correct instead.
    """
    msg = str(error)
    if "not found" in msg.lower():
        logger.warning("Hallucinated tool '%s' — returning error to model", tool.name)
        avail = ""
        if "Available tools:" in msg:
            avail = msg.split("Available tools:")[1].split("\n")[0].strip()
        return {
            "ok": False,
            "error": (
                f"Tool '{tool.name}' does not exist. "
                f"You MUST only call tools that are available to you. "
                f"Available tools: {avail}. "
                "Retry with a valid tool name."
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
    max_iterations: int = 10,
) -> SequentialAgent:
    """Assemble and return the complete CodePilot ADK agent pipeline.

    Args:
        provider:       LLM provider — "ollama", "openrouter", or "gemini".
        model:          Model name for the chosen provider.
        api_key:        API key for cloud providers (OpenRouter).
        github_token:   Optional GitHub token for the GitHub MCP server.
        max_iterations: Maximum development loop iterations before forced stop.

    Returns:
        The root SequentialAgent, ready to run via ``google.adk.runners.Runner``.
    """
    _register_litellm_providers()
    model_str = _resolve_model(provider, model)

    logger.info("Building CodePilot pipeline: %s/%s (max_iter=%d)", provider, model, max_iterations)

    # Composed before_tool_callback:
    #   1. Loop guard    — always active
    #   2. Human-in-loop — only active when CODEPILOT_CONFIRM_DESTRUCTIVE=true
    tool_guard = compose_before_tool_callbacks(
        guard_tool_loop,
        confirm_before_destructive_tool,
    )

    # ── Shared agent kwargs to avoid repetition ──────────────────────────
    def _agent(**kwargs) -> LlmAgent:
        return LlmAgent(
            model=model_str,
            include_contents="none",   # each agent works from state, not chat history
            before_tool_callback=tool_guard,
            on_tool_error_callback=_handle_tool_error,
            **kwargs,
        )

    # ── 1. Planner ────────────────────────────────────────────────────────
    planner = _agent(
        name="PlannerAgent",
        instruction=PLANNER_INSTRUCTION,
        description="Decomposes the user request into a structured development plan.",
        tools=get_planner_tools(github_token),
        output_key="plan_summary",
        include_contents="default",   # planner needs the initial user message
    )

    # ── 2a. Developer ─────────────────────────────────────────────────────
    developer = _agent(
        name="DeveloperAgent",
        instruction=DEVELOPER_INSTRUCTION,
        description="Writes code, installs dependencies, scaffolds projects.",
        tools=get_developer_tools(github_token),
        output_key="developer_output",
    )

    # ── 2b. Reviewer ──────────────────────────────────────────────────────
    reviewer = _agent(
        name="ReviewAgent",
        instruction=REVIEW_INSTRUCTION,
        description="Reviews code for bugs, missing imports, and config issues before runtime.",
        tools=get_review_tools(),
        output_key="review_output",
    )

    # ── 2c. Runtime ───────────────────────────────────────────────────────
    runtime = _agent(
        name="RuntimeAgent",
        instruction=RUNTIME_INSTRUCTION,
        description="Builds, runs, and verifies projects. Sets app_type/app_ready/app_url state.",
        tools=[*get_runtime_tools(), set_state],
        output_key="runtime_output",
    )

    # ── 2d. Test (Browser) ────────────────────────────────────────────────
    test_agent = _agent(
        name="TestAgent",
        instruction=BROWSER_INSTRUCTION,
        description="Browser UI testing for web projects; skips for CLI/library/script.",
        tools=[*get_browser_tools(), set_state],
        output_key="browser_output",
    )

    # ── 2e. Debug ─────────────────────────────────────────────────────────
    debug = _agent(
        name="DebugAgent",
        instruction=DEBUG_INSTRUCTION,
        description="Diagnoses failures, applies fixes, or terminates the loop on success.",
        tools=[*get_debug_tools(), set_state, exit_loop],
        output_key="debug_output",
    )

    # ── Development loop ──────────────────────────────────────────────────
    dev_loop = LoopAgent(
        name="DevelopmentLoop",
        sub_agents=[developer, reviewer, runtime, test_agent, debug],
        max_iterations=max_iterations,
        before_agent_callback=increment_iteration,
    )

    # ── 3. Finalizer ──────────────────────────────────────────────────────
    finalizer = _agent(
        name="FinalizerAgent",
        instruction=FINALIZER_INSTRUCTION,
        description="Cleans up, writes README, commits, saves session memory.",
        tools=get_finalizer_tools(),
        output_key="final_summary",
    )

    # ── Root pipeline ─────────────────────────────────────────────────────
    root = SequentialAgent(
        name="CodePilotPipeline",
        description=(
            "Autonomous software engineering pipeline: "
            "Plan → Develop → Review → Run → Test → Fix → Finalize. "
            "Project-type agnostic: works with web apps, CLIs, libraries, APIs, scripts."
        ),
        sub_agents=[planner, dev_loop, finalizer],
    )

    logger.info("CodePilot pipeline built successfully")
    return root
