"""Cross-session global memory — user preferences and patterns.

Stored at ~/.codepilot/global_memory.json.  This file is shared across
all projects and holds stable user-level facts that should influence every
session:

  preferred_stack   — e.g. "React + FastAPI"
  coding_style      — e.g. "modular", "functional"
  frequent_tasks    — list of commonly requested task types
  notes             — free-form user notes

GlobalMemory is read-only from the agent's perspective.  Users update it
via ``codepilot config global-memory`` or by calling GlobalMemory.set().

The context block returned by get_context() is intentionally small
(< 200 tokens) so it can always be prepended to every LLM call.
"""

import json
from pathlib import Path
from typing import Any

from ..utils.constants import GLOBAL_MEMORY_FILE
from ..utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULTS: dict = {
    "preferred_stack": "",
    "coding_style": "",
    "frequent_tasks": [],
    "notes": "",
}


class GlobalMemory:
    """Read/write access to the cross-session global memory file."""

    @staticmethod
    def load() -> dict:
        """Return the current global memory dict (with defaults for missing keys)."""
        if not GLOBAL_MEMORY_FILE.exists():
            return dict(_DEFAULTS)
        try:
            data = json.loads(GLOBAL_MEMORY_FILE.read_text())
            return {**_DEFAULTS, **data}
        except Exception as exc:
            logger.warning("Could not load global memory: %s", exc)
            return dict(_DEFAULTS)

    @staticmethod
    def set(key: str, value: Any) -> None:
        """Update a single key in global memory.

        Args:
            key:   Any string key (standard keys in _DEFAULTS or custom).
            value: Value to store (must be JSON-serialisable).
        """
        data = GlobalMemory.load()
        data[key] = value
        try:
            GLOBAL_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            GLOBAL_MEMORY_FILE.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            logger.error("Could not save global memory: %s", exc)

    @staticmethod
    def get_context() -> str:
        """Return a compact context string for LLM injection.

        Returns an empty string if no meaningful global memory is set.
        """
        data = GlobalMemory.load()
        lines: list[str] = []

        if data.get("preferred_stack"):
            lines.append(f"Preferred stack: {data['preferred_stack']}")
        if data.get("coding_style"):
            lines.append(f"Coding style: {data['coding_style']}")
        if data.get("frequent_tasks"):
            tasks = ", ".join(str(t) for t in data["frequent_tasks"][:5])
            lines.append(f"Frequent tasks: {tasks}")
        if data.get("notes"):
            lines.append(f"Notes: {data['notes'][:200]}")

        if not lines:
            return ""
        return "[User Preferences]\n" + "\n".join(lines)
