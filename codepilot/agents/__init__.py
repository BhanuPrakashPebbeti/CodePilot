"""ADK-based multi-agent architecture for CodePilot.

Refactored pipeline
-------------------
CodePilotPipeline (SequentialAgent)
  ├── PlannerAgent    (LlmAgent — memory check, plan, optional Notion sync)
  ├── DevelopmentLoop (LoopAgent — iterative implement→run→test→fix)
  │   ├── DeveloperAgent  (LlmAgent — writes/edits code, local tools only)
  │   ├── RuntimeAgent    (LlmAgent — builds, runs, verifies)
  │   ├── TestAgent       (LlmAgent — Playwright browser + HTTP tests)
  │   └── DebugAgent      (LlmAgent — fixes, check_exit_conditions, exit_loop)
  └── FinalizerAgent  (LlmAgent — README, git, GitHub MCP, Slack MCP)

Tool classification
-------------------
Local FunctionTools  → fs, exec, git, workspace, testing, environment,
                       planning, memory, debug_tools, validation, state
External MCP         → Playwright, GitHub (official), Notion (official),
                       Slack (official)

ReviewAgent removed: Developer self-corrects; Review added latency.
"""

# Apply warning suppression BEFORE any google.adk import.
from .patches import _suppress_third_party_warnings as _suppress
_suppress()

from .builder import build_codepilot_agent
from .runner import CodePilotRunner, create_codepilot_runner
from .prompts import (
    PLANNER_INSTRUCTION,
    DEVELOPER_INSTRUCTION,
    REVIEW_INSTRUCTION,
    RUNTIME_INSTRUCTION,
    BROWSER_INSTRUCTION,
    TEST_INSTRUCTION,
    DEBUG_INSTRUCTION,
    FINALIZER_INSTRUCTION,
)

__all__ = [
    "build_codepilot_agent",
    "CodePilotRunner",
    "create_codepilot_runner",
    "PLANNER_INSTRUCTION",
    "DEVELOPER_INSTRUCTION",
    "REVIEW_INSTRUCTION",
    "RUNTIME_INSTRUCTION",
    "BROWSER_INSTRUCTION",
    "TEST_INSTRUCTION",
    "DEBUG_INSTRUCTION",
    "FINALIZER_INSTRUCTION",
]
