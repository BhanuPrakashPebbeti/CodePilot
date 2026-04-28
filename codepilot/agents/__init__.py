"""ADK-based multi-agent architecture for CodePilot.

Pipeline
--------
CodePilotPipeline (SequentialAgent)
  ├── PlannerAgent    (LlmAgent — memory check, plan, Notion project/task creation)
  ├── DevelopmentLoop (LoopAgent — iterative implement→run→test→fix)
  │   ├── DeveloperAgent  (LlmAgent — writes/edits code, conventional commits)
  │   ├── RuntimeAgent    (LlmAgent — builds, runs, verifies)
  │   ├── TestAgent       (LlmAgent — Playwright browser + HTTP tests + screenshots)
  │   └── DebugAgent      (LlmAgent — fixes, Slack HITL, check_exit_conditions, exit_loop)
  └── FinalizerAgent  (LlmAgent — README, git, GitHub MCP PR, Notion status, Slack notify)

Tool classification
-------------------
Local FunctionTools  → fs, exec, git, workspace, testing, environment,
                       planning, memory, debug_tools, validation, state,
                       notion_tools, slack_hitl
External MCP         → Playwright (browser UI), GitHub (repo + PR)

ReviewAgent removed: Developer self-corrects; Review added latency without benefit.
"""

# Apply warning suppression BEFORE any google.adk import.
from .patches import _suppress_third_party_warnings as _suppress
_suppress()

from .builder import build_codepilot_agent
from .runner import CodePilotRunner, create_codepilot_runner
from .prompts import (
    PLANNER_INSTRUCTION,
    DEVELOPER_INSTRUCTION,
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
    "RUNTIME_INSTRUCTION",
    "BROWSER_INSTRUCTION",
    "TEST_INSTRUCTION",
    "DEBUG_INSTRUCTION",
    "FINALIZER_INSTRUCTION",
]
