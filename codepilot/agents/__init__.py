"""ADK-based multi-agent architecture for CodePilot.

Pipeline hierarchy
------------------
CodePilotPipeline (SequentialAgent)
  ├── PlannerAgent       (LlmAgent — checks memory, decomposes task into plan)
  ├── DevelopmentLoop    (LoopAgent — iterative implement→review→run→test→fix)
  │   ├── DeveloperAgent (LlmAgent — writes/edits code via MCP filesystem+bash)
  │   ├── ReviewAgent    (LlmAgent — static review before runtime)
  │   ├── RuntimeAgent   (LlmAgent — builds, runs, verifies by project type)
  │   ├── TestAgent      (LlmAgent — browser UI testing for web, skip for others)
  │   └── DebugAgent     (LlmAgent — searches memory, diagnoses, fixes, exits)
  └── FinalizerAgent     (LlmAgent — README, git commit, saves session memory)

Guardrails (ADK-native callbacks — see callbacks/)
---------------------------------------------------
before_tool_callback  → guard_tool_loop + confirm_before_destructive_tool
before_agent_callback → increment_iteration (LoopAgent)
on_tool_error_callback → hallucinated-tool handler

Memory (see memory/)
--------------------
SqliteMemoryService   → ADK automatic session persistence (~/.codepilot/session_memory.db)
memory_server.py      → structured agent memory via MCP (~/.codepilot/memory.db)
"""

from .builder import build_codepilot_agent
from .runner import CodePilotRunner, create_codepilot_runner
from .prompts import (
    PLANNER_INSTRUCTION,
    DEVELOPER_INSTRUCTION,
    REVIEW_INSTRUCTION,
    RUNTIME_INSTRUCTION,
    BROWSER_INSTRUCTION,
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
    "DEBUG_INSTRUCTION",
    "FINALIZER_INSTRUCTION",
]
