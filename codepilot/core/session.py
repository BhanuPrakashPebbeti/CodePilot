"""Per-project session storage with full isolation.

Each named project gets its own directory under ~/.codepilot/sessions/<project_name>/
containing four files:

  metadata.json  — project name, workspace path, created_at, last_active, priority
  messages.json  — conversation history with role/content/timestamp/priority
  memory.json    — structured long-term memory (episodic/semantic/procedural)
  summary.json   — rolling summary that replaces old messages once threshold is hit

No two projects share memory, messages, or context.  Switching projects means
loading a completely different set of files.

Context building
----------------
When calling the LLM, SessionStore.build_context(task) assembles a compact
context block using this priority order (most important first):

  1. Session summary (replaces history older than SUMMARIZE_AFTER messages)
  2. High-priority messages (decisions, errors, completions) — last 3
  3. Recent messages — last RECENT_WINDOW entries
  4. Relevant long-term memory (episodic events matching the current task)

This keeps total context overhead under ~1 500 tokens regardless of session age.

Summarisation
-------------
When the message count exceeds SUMMARIZE_AFTER, the oldest messages are
condensed into a plain-text summary (no LLM needed — rule-based extraction).
Only the most recent KEEP_AFTER_SUMMARY messages are retained.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..utils.constants import (
    SESSION_MEMORY_FILE,
    SESSION_MESSAGES_FILE,
    SESSION_METADATA_FILE,
    SESSION_SUMMARY_FILE,
    SESSIONS_DIR,
)
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Summarisation thresholds
SUMMARIZE_AFTER = 40     # summarise when message count exceeds this
KEEP_AFTER_SUMMARY = 10  # messages to retain after summarisation

# Context window limits
RECENT_WINDOW = 6        # recent messages to include in context
MAX_HIGH_PRIORITY = 3    # high-priority messages to include
MAX_MEMORY_ENTRIES = 3   # long-term memory entries to include

# Priority levels
HIGH   = "high"
MEDIUM = "medium"
LOW    = "low"

_HIGH_PRIORITY_KEYWORDS = frozenset({
    "completed", "success", "failed", "error", "fixed", "deployed",
    "blocked", "decision", "important", "critical", "resolved",
})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _infer_priority(content: str) -> str:
    """Assign message priority based on content keywords."""
    lower = content.lower()
    if any(kw in lower for kw in _HIGH_PRIORITY_KEYWORDS):
        return HIGH
    if len(content) < 30:
        return LOW
    return MEDIUM


def _slug(name: str) -> str:
    """Normalise a project name to a safe directory name."""
    slug = re.sub(r"[^\w\-]", "-", name.strip().lower())
    return re.sub(r"-{2,}", "-", slug).strip("-") or "project"


class SessionStore:
    """Isolated per-project session: messages, memory, summaries, context.

    Usage::

        # Create a new project session
        store = SessionStore("kanban-board")
        store.create(workspace_path="/home/user/projects/kanban")

        # Resume an existing session
        store = SessionStore("kanban-board")   # already exists

        # Record interactions
        store.add_message("user", "Add drag-and-drop support")
        store.add_message("assistant", "Drag-and-drop implemented. Run with: npm start", priority=HIGH)

        # Store structured memories
        store.add_episodic("WebSocket connection dropped", resolution="Fixed CORS config")
        store.add_semantic("preferred_stack", "React + FastAPI")

        # Build LLM context
        ctx = store.build_context(current_task="Fix the failing tests")
    """

    def __init__(self, project_name: str) -> None:
        self.project_name = _slug(project_name)
        self.session_dir = SESSIONS_DIR / self.project_name
        self._meta: Optional[dict] = None

    # ── Existence / creation ──────────────────────────────────────────────

    def exists(self) -> bool:
        return (self.session_dir / SESSION_METADATA_FILE).exists()

    def create(self, workspace_path: str, priority: str = MEDIUM) -> dict:
        """Create a new project session directory and metadata.

        Args:
            workspace_path: Absolute path to the project's workspace.
            priority: Session priority — "high", "medium", or "low".

        Returns:
            The metadata dict.

        Raises:
            FileExistsError: If the session already exists.
        """
        if self.exists():
            raise FileExistsError(
                f"Session '{self.project_name}' already exists. "
                "Use SessionStore.load() to resume it."
            )
        self.session_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "project_name": self.project_name,
            "workspace_path": str(workspace_path),
            "created_at": _utcnow(),
            "last_active": _utcnow(),
            "priority": priority,
        }
        self._save_json(SESSION_METADATA_FILE, meta)
        self._save_json(SESSION_MESSAGES_FILE, [])
        self._save_json(SESSION_MEMORY_FILE, {"episodic": [], "semantic": {}, "procedural": []})
        self._save_json(SESSION_SUMMARY_FILE, {"text": "", "message_count": 0, "updated_at": ""})
        self._meta = meta
        logger.info("Created session '%s' → %s", self.project_name, workspace_path)
        return meta

    def load_metadata(self) -> dict:
        """Return the session metadata, touching last_active."""
        meta = self._load_json(SESSION_METADATA_FILE, {})
        meta["last_active"] = _utcnow()
        self._save_json(SESSION_METADATA_FILE, meta)
        self._meta = meta
        return meta

    @property
    def workspace_path(self) -> str:
        if self._meta is None:
            self._meta = self._load_json(SESSION_METADATA_FILE, {})
        return self._meta.get("workspace_path", ".")

    # ── Messages ──────────────────────────────────────────────────────────

    def add_message(
        self,
        role: str,
        content: str,
        priority: Optional[str] = None,
    ) -> None:
        """Append a message to the conversation history.

        Priority is inferred from content if not provided:
          HIGH   — contains completion/error/decision keywords
          MEDIUM — normal interaction (default)
          LOW    — very short messages

        Triggers summarisation when count exceeds SUMMARIZE_AFTER.

        Args:
            role:     "user" or "assistant"
            content:  Message text (will be truncated to 2 000 chars for storage).
            priority: "high", "medium", or "low". Auto-inferred if None.
        """
        messages = self._load_json(SESSION_MESSAGES_FILE, [])
        messages.append({
            "role": role,
            "content": content[:2000],
            "timestamp": _utcnow(),
            "priority": priority or _infer_priority(content),
        })
        self._save_json(SESSION_MESSAGES_FILE, messages)
        if len(messages) > SUMMARIZE_AFTER:
            self._summarize(messages)

    def get_messages(self) -> list[dict]:
        return self._load_json(SESSION_MESSAGES_FILE, [])

    def get_recent_history_display(self, max_messages: int = 10) -> list[dict]:
        """Return the most recent messages formatted for terminal display.

        Used by ``codepilot open`` to restore conversation context visibly.
        Each returned dict has keys: role, content (truncated), timestamp.

        Args:
            max_messages: Maximum number of recent messages to return.

        Returns:
            List of dicts with role/content/timestamp, oldest first.
        """
        messages = self.get_messages()
        recent = messages[-max_messages:] if len(messages) > max_messages else messages
        return [
            {
                "role": m.get("role", "?"),
                "content": m.get("content", "")[:500],
                "timestamp": m.get("timestamp", "")[:19].replace("T", " "),
            }
            for m in recent
        ]

    # ── Long-term structured memory ───────────────────────────────────────

    def add_episodic(self, event: str, resolution: str = "") -> None:
        """Record a past interaction (what happened + how it was resolved).

        Example::
            store.add_episodic("Frontend failed to load", "Fixed CORS in FastAPI")
        """
        mem = self._load_json(SESSION_MEMORY_FILE, {"episodic": [], "semantic": {}, "procedural": []})
        mem["episodic"].append({
            "event": event[:300],
            "resolution": resolution[:300],
            "timestamp": _utcnow(),
        })
        # Cap episodic entries — keep the most recent 20
        mem["episodic"] = mem["episodic"][-20:]
        self._save_json(SESSION_MEMORY_FILE, mem)

    def add_semantic(self, fact_key: str, fact_value: str) -> None:
        """Store a persistent fact about the project or user.

        Example::
            store.add_semantic("preferred_stack", "React + FastAPI")
        """
        mem = self._load_json(SESSION_MEMORY_FILE, {"episodic": [], "semantic": {}, "procedural": []})
        mem["semantic"][fact_key] = fact_value[:300]
        self._save_json(SESSION_MEMORY_FILE, mem)

    def add_procedural(self, pattern: str, steps: list) -> None:
        """Record a repeatable workflow pattern.

        Example::
            store.add_procedural("debug React app", ["check console", "verify API"])
        """
        mem = self._load_json(SESSION_MEMORY_FILE, {"episodic": [], "semantic": {}, "procedural": []})
        mem["procedural"].append({
            "pattern": pattern[:200],
            "steps": [s[:200] for s in steps[:10]],
            "timestamp": _utcnow(),
        })
        mem["procedural"] = mem["procedural"][-10:]
        self._save_json(SESSION_MEMORY_FILE, mem)

    def get_memory(self) -> dict:
        return self._load_json(SESSION_MEMORY_FILE, {"episodic": [], "semantic": {}, "procedural": []})

    # ── Summaries ─────────────────────────────────────────────────────────

    def save_summary(self, text: str) -> None:
        """Persist a manually written summary (e.g. from FinalizerAgent)."""
        self._save_json(SESSION_SUMMARY_FILE, {
            "text": text[:1000],
            "message_count": len(self.get_messages()),
            "updated_at": _utcnow(),
        })

    def get_summary_text(self) -> str:
        return self._load_json(SESSION_SUMMARY_FILE, {}).get("text", "")

    # ── Context builder ───────────────────────────────────────────────────

    def build_context(self, current_task: str = "") -> str:
        """Assemble an optimised context block for LLM injection.

        Includes (in order):
          1. Rolling session summary (if any)
          2. High-priority messages (last MAX_HIGH_PRIORITY)
          3. Recent messages (last RECENT_WINDOW)
          4. Relevant long-term memory entries

        The result is compact and bounded — safe to prepend to any LLM call.
        """
        parts: list[str] = []

        # 1. Summary (replaces old history)
        summary = self.get_summary_text()
        if summary:
            parts.append(f"[Session Summary]\n{summary}")

        messages = self.get_messages()

        # 2. High-priority messages not already in recent window
        recent_set = set(
            json.dumps(m, sort_keys=True)
            for m in messages[-RECENT_WINDOW:]
        )
        high_priority = [
            m for m in messages
            if m.get("priority") == HIGH
            and json.dumps(m, sort_keys=True) not in recent_set
        ][-MAX_HIGH_PRIORITY:]
        if high_priority:
            lines = ["[Key Events]"]
            for m in high_priority:
                lines.append(f"{m['role'].upper()}: {m['content'][:300]}")
            parts.append("\n".join(lines))

        # 3. Recent messages
        recent = messages[-RECENT_WINDOW:]
        if recent:
            lines = ["[Recent Conversation]"]
            for m in recent:
                lines.append(f"{m['role'].upper()}: {m['content'][:400]}")
            parts.append("\n".join(lines))

        # 4. Relevant long-term memory
        relevant = self._relevant_memory(current_task)
        if relevant:
            parts.append(relevant)

        if not parts:
            return ""
        return "\n\n".join(parts) + "\n\n"

    # ── Listing / deletion ────────────────────────────────────────────────

    @staticmethod
    def list_all() -> list[dict]:
        """Return metadata for all existing sessions, sorted by last_active."""
        if not SESSIONS_DIR.exists():
            return []
        results = []
        for meta_file in SESSIONS_DIR.glob(f"*/{SESSION_METADATA_FILE}"):
            try:
                data = json.loads(meta_file.read_text())
                results.append(data)
            except Exception:
                pass
        results.sort(key=lambda m: m.get("last_active", ""), reverse=True)
        return results

    @staticmethod
    def delete(project_name: str) -> bool:
        """Delete a session and all its files.

        Returns True if deleted, False if it did not exist.
        """
        import shutil
        slug = _slug(project_name)
        target = SESSIONS_DIR / slug
        if not target.exists():
            return False
        shutil.rmtree(target)
        logger.info("Deleted session '%s'", slug)
        return True

    # ── Internal ──────────────────────────────────────────────────────────

    def _summarize(self, messages: list[dict]) -> None:
        """Condense old messages into a summary; keep only the most recent ones."""
        to_summarize = messages[:-KEEP_AFTER_SUMMARY]
        keep = messages[-KEEP_AFTER_SUMMARY:]

        # Build summary text from high-priority events and completions
        events: list[str] = []
        for m in to_summarize:
            if m.get("priority") == HIGH:
                snippet = m["content"][:150].replace("\n", " ")
                events.append(f"[{m['role']}] {snippet}")

        existing_summary = self.get_summary_text()
        parts = []
        if existing_summary:
            parts.append(existing_summary)
        if events:
            parts.append(f"Earlier session ({len(to_summarize)} messages): " + " | ".join(events))
        elif to_summarize:
            parts.append(
                f"Earlier session: {len(to_summarize)} messages exchanged covering "
                f"{len({m['role'] for m in to_summarize})} participants."
            )

        new_summary = " ".join(parts)[:1000]
        self._save_json(SESSION_SUMMARY_FILE, {
            "text": new_summary,
            "message_count": len(to_summarize),
            "updated_at": _utcnow(),
        })
        self._save_json(SESSION_MESSAGES_FILE, keep)
        logger.debug("Summarised %d messages for session '%s'", len(to_summarize), self.project_name)

    def _relevant_memory(self, task: str) -> str:
        """Return a short block of long-term memory relevant to the current task."""
        if not task:
            return ""
        task_words = set(re.findall(r"\w+", task.lower()))
        if not task_words:
            return ""

        mem = self.get_memory()
        hits: list[str] = []

        # Episodic: match event keywords against task
        for entry in mem.get("episodic", [])[-10:]:
            event_words = set(re.findall(r"\w+", entry.get("event", "").lower()))
            if task_words & event_words:
                line = f"• Past event: {entry['event']}"
                if entry.get("resolution"):
                    line += f" → {entry['resolution']}"
                hits.append(line)
            if len(hits) >= MAX_MEMORY_ENTRIES:
                break

        # Semantic facts (always include, compact)
        semantic = mem.get("semantic", {})
        if semantic:
            facts = "; ".join(f"{k}: {v}" for k, v in list(semantic.items())[:5])
            hits.append(f"• Known facts: {facts}")

        if not hits:
            return ""
        return "[Relevant Memory]\n" + "\n".join(hits)

    def _load_json(self, filename: str, default: Any) -> Any:
        path = self.session_dir / filename
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except Exception as exc:
            logger.warning("Could not read %s: %s", path, exc)
            return default

    def _save_json(self, filename: str, data: Any) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / filename
        try:
            path.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            logger.error("Could not write %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Backward-compat alias — existing code that imports SessionManager still works
# ---------------------------------------------------------------------------

SessionManager = SessionStore
