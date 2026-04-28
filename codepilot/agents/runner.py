"""ADK Runner wrapper — executes the CodePilot pipeline and streams output.

Key responsibilities
--------------------
- Configure ADK with persistent session storage (DatabaseSessionService)
- Wire up the SQLite-backed memory service (SqliteMemoryService)
- Apply ADK compatibility patches for non-Gemini providers
- Stream pipeline events to the Rich terminal renderer
- Inject previous-session memory context in interactive mode
- Manage CodePilot session lifecycle (local tracking, error handling)

Persistence model
-----------------
Sessions    → ~/.codepilot/sessions.db  (ADK DatabaseSessionService)
Memory      → ~/.codepilot/session_memory.db  (SqliteMemoryService — session events)
Structured  → ~/.codepilot/memory.db          (memory MCP server — typed memories)

The first two are automatic (ADK handles them).  The third requires
agents to call memory MCP tools (store_memory / get_recent_conversations).
"""

import asyncio
import json
import logging as _logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

# ── Suppress LiteLLM log spam before any ADK import ──────────────────────
os.environ.setdefault("LITELLM_LOG", "ERROR")
for _n in ("LiteLLM", "litellm", "LiteLLM Proxy", "LiteLLM Router", "httpx"):
    _logging.getLogger(_n).setLevel(_logging.ERROR)
try:
    import litellm as _litellm
    _litellm.suppress_debug_info = True
    _litellm.set_verbose = False
    _litellm.num_retries = 3
    _litellm.request_timeout = 120
except ImportError:
    pass
# ─────────────────────────────────────────────────────────────────────────

# Apply ADK patches before any ADK usage
from .patches import apply_all_patches
apply_all_patches()

from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types as genai_types
from rich.panel import Panel

from .builder import build_codepilot_agent
from ..config import ConfigManager
from ..core.renderer import Renderer, console
from ..core.session import SessionManager
from ..memory import SqliteMemoryService
from ..utils.constants import (
    CONFIG_DIR,
    PROVIDER_OLLAMA,
    PROVIDER_OPENROUTER,
)
from ..utils.logger import get_logger

logger = get_logger(__name__)

# ── Persistent storage paths ──────────────────────────────────────────────
_SESSIONS_DB = CONFIG_DIR / "sessions.db"
_SESSION_MEMORY_DB = CONFIG_DIR / "session_memory.db"
_STRUCTURED_MEMORY_DB = CONFIG_DIR / "memory.db"

# ── Retry configuration ───────────────────────────────────────────────────
_TRANSIENT_KEYWORDS = (
    "network", "connection", "timed out", "timeout",
    "502", "503", "504", "rate limit", "overloaded",
    "service unavailable", "bad gateway",
    "mcp session", "connection closed",
)
_TRANSIENT_TYPES = frozenset({
    "APIError", "APIConnectionError", "ServiceUnavailableError",
    "InternalServerError", "BadGatewayError", "Timeout", "RateLimitError",
    "McpError", "ClosedResourceError",
})
_MAX_RETRIES = 3
_RETRY_DELAY = 5.0


def _is_transient(exc: Exception) -> bool:
    if type(exc).__name__ in _TRANSIENT_TYPES:
        return True
    if isinstance(exc, ConnectionError):
        return True
    return any(kw in str(exc).lower() for kw in _TRANSIENT_KEYWORDS)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class CodePilotRunner:
    """Runs the CodePilot ADK pipeline with persistence and Rich rendering.

    Wraps ``google.adk.runners.Runner`` with:
    - Persistent ADK sessions (DatabaseSessionService, SQLite)
    - Persistent session-event memory (SqliteMemoryService)
    - Rich terminal rendering of all agent events
    - Local session tracking for CLI ``codepilot sessions`` command
    - Transient error retry (network drops, rate limits)
    - Memory context injection in interactive (REPL) mode
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        project_dir: str = ".",
        session_manager: Optional[SessionManager] = None,
        max_iterations: int = 8,
    ) -> None:
        self.config_manager = config_manager
        self.project_dir = str(Path(project_dir).resolve())
        self.session_manager = session_manager or SessionManager(self.project_dir)
        self.max_iterations = max_iterations
        self.renderer = Renderer()

        cfg = config_manager.config
        self.provider = cfg.llm.active_provider
        self.model = cfg.llm.active_model
        self.api_key = cfg.llm.api_key
        self.github_token = cfg.github.token if cfg.github else None
        self.notion_token = cfg.notion.token if cfg.notion else None
        self.slack_token = cfg.slack.bot_token if cfg.slack else None

        self._configure_env()

        # Initialise persistent services (created once per runner instance)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._session_service = DatabaseSessionService(
            db_url=f"sqlite+aiosqlite:///{_SESSIONS_DB}"
        )
        self._memory_service = SqliteMemoryService(db_path=str(_SESSION_MEMORY_DB))

    # ── Environment setup ─────────────────────────────────────────────────

    def _configure_env(self) -> None:
        if self.provider == PROVIDER_OPENROUTER and self.api_key:
            os.environ["OPENROUTER_API_KEY"] = self.api_key
            os.environ.setdefault("OPENAI_API_KEY", self.api_key)
        if self.provider == PROVIDER_OLLAMA:
            os.environ.setdefault("OLLAMA_API_BASE", "http://localhost:11434")
        if self.github_token:
            os.environ["GITHUB_TOKEN"] = self.github_token
        os.environ["CODEPILOT_PROJECT_DIR"] = self.project_dir

    # ── Stale-state cleanup ───────────────────────────────────────────────

    @staticmethod
    def _cleanup_stale_plan_state() -> None:
        import tempfile
        state = Path(tempfile.gettempdir()) / "codepilot_plan_state.json"
        try:
            state.unlink(missing_ok=True)
        except OSError:
            pass

    # ── Memory context helpers ────────────────────────────────────────────

    def _load_memory_context(self) -> str:
        """Read recent structured memories for this project.

        Queries the memory MCP server's SQLite database directly (faster
        than spawning the MCP subprocess just for context loading).
        Returns a formatted context block, or empty string if no memories.
        """
        try:
            if not _STRUCTURED_MEMORY_DB.exists():
                return ""
            with sqlite3.connect(str(_STRUCTURED_MEMORY_DB)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT type, content FROM memories
                    WHERE (project = ? OR project IS NULL)
                      AND type IN ('conversation', 'project', 'error_fix')
                    ORDER BY updated DESC
                    LIMIT 5
                    """,
                    (self.project_dir,),
                ).fetchall()
            if not rows:
                return ""
            lines = ["[Memory from previous sessions]"]
            for row in rows:
                lines.append(f"[{row['type']}] {row['content']}")
            lines.append("[End memory]")
            return "\n".join(lines) + "\n\n"
        except Exception as exc:
            logger.debug("Could not load memory context: %s", exc)
            return ""

    # ── Public run API ────────────────────────────────────────────────────

    def run(self, task: str, memory_context: str = "") -> None:
        """Run a single task through the full pipeline.

        Args:
            task:           Natural-language task description.
            memory_context: Optional pre-loaded memory to prepend to the task.
        """
        try:
            asyncio.run(self._run_async(task, memory_context))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Pipeline error:[/red] {e}")
            logger.exception("Pipeline execution failed")
            raise

    async def _run_async(self, task: str, memory_context: str = "") -> None:
        self._cleanup_stale_plan_state()

        root_agent = build_codepilot_agent(
            provider=self.provider,
            model=self.model,
            api_key=self.api_key,
            github_token=self.github_token,
            notion_token=self.notion_token,
            slack_token=self.slack_token,
            max_iterations=self.max_iterations,
        )

        runner = Runner(
            agent=root_agent,
            app_name="codepilot",
            session_service=self._session_service,
            memory_service=self._memory_service,
        )

        # Kept at this scope so retries can spin up a fresh session with the
        # same clean state instead of trying to resume a broken invocation.
        _initial_state = {
            "project_dir":     self.project_dir,
            "plan_summary":    "",
            "iteration_count": "0",
            "review_output":   "",
            "app_type":        "",
            "app_url":         "",
            "app_ready":       "false",
            "runtime_error":   "",
            "test_result":     "",
            "test_errors":     "",
            "debug_log":       "",
            "final_status":    "",
        }

        session = await self._session_service.create_session(
            app_name="codepilot",
            user_id="user",
            state=_initial_state,
        )

        # Local session tracking
        local_session = self.session_manager.start_session()
        local_task = self.session_manager.add_task(task)
        self.session_manager.start_task(local_task.task_id)

        self.renderer.reset()
        console.print(
            Panel(
                f"[bold cyan]CodePilot Pipeline[/bold cyan]\n"
                f"[dim]{self.provider}/{self.model}[/dim]",
                border_style="cyan",
            )
        )

        # Prepend memory context to the user message when available
        user_text = f"{memory_context}{task}" if memory_context else task
        user_msg = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=user_text)],
        )

        try:
            attempts = 0

            while True:
                try:
                    async for event in runner.run_async(
                        user_id="user",
                        session_id=session.id,
                        new_message=user_msg,
                    ):
                        self._handle_event(event)
                    break

                except Exception as inner:
                    attempts += 1
                    if not _is_transient(inner) or attempts > _MAX_RETRIES:
                        raise

                    # Rate-limit errors need a full minute to reset.
                    # All other transient errors (network, 503) retry quickly.
                    is_rate_limit = (
                        "rate limit" in str(inner).lower()
                        or "429" in str(inner)
                        or type(inner).__name__ == "RateLimitError"
                    )
                    wait = 65.0 if is_rate_limit else _RETRY_DELAY

                    console.print(
                        f"\n[yellow]⚠ Transient error "
                        f"(attempt {attempts}/{_MAX_RETRIES}):[/yellow] "
                        f"{type(inner).__name__}: {inner}\n"
                        f"[dim]Retrying in {wait:.0f}s…[/dim]"
                    )
                    await asyncio.sleep(wait)

                    # DatabaseSessionService cannot resume a broken invocation
                    # with new_message=None — it requires a fresh session.
                    session = await self._session_service.create_session(
                        app_name="codepilot",
                        user_id="user",
                        state=_initial_state,
                    )

            # Persist session events to long-term memory
            try:
                completed = await self._session_service.get_session(
                    app_name="codepilot",
                    user_id="user",
                    session_id=session.id,
                )
                if completed:
                    await self._memory_service.add_session_to_memory(completed)
            except Exception as exc:
                logger.debug("Memory persistence skipped: %s", exc)

            self.session_manager.complete_task(local_task.task_id)

        except Exception as e:
            self.session_manager.fail_task(local_task.task_id, str(e))
            raise

        finally:
            self.renderer.on_complete()
            self.session_manager.end_session()

    # ── Event handler ─────────────────────────────────────────────────────

    def _handle_event(self, event: Any) -> None:
        if not event or not event.content or not event.content.parts:
            return
        for part in event.content.parts:
            if part.text:
                self.renderer.on_thinking(part.text)
                self.renderer.flush_thinking()
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                try:
                    args = dict(fc.args) if fc.args else {}
                except (TypeError, ValueError):
                    args = {}
                self.renderer.on_tool_start(fc.name, args)
            if hasattr(part, "function_response") and part.function_response:
                fr = part.function_response
                self.renderer.on_tool_end(fr.name, self._extract_output(fr.response))

    @staticmethod
    def _extract_output(response: Any) -> str:
        """Unwrap ADK's MCP response envelope to get the raw tool JSON string."""
        if not response:
            return "{}"
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            content = response.get("content")
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict) and "text" in first:
                    return first["text"]
            structured = response.get("structuredContent")
            if isinstance(structured, dict) and "result" in structured:
                r = structured["result"]
                return r if isinstance(r, str) else json.dumps(r)
            return json.dumps(response)
        return str(response)

    # ── Interactive REPL ──────────────────────────────────────────────────

    def run_interactive(self) -> None:
        """Interactive REPL — run multiple tasks in one session.

        Features
        --------
        - Persistent command history (↑/↓ arrows)
        - Memory context injection: loads recent session summaries and
          prepends them to each new task so agents have context about
          what was previously built.
        - Built-in commands: exit, clear, history, help, memory
        """
        import readline
        import atexit

        history_file = Path.home() / ".codepilot_history"
        try:
            readline.read_history_file(str(history_file))
        except FileNotFoundError:
            pass
        readline.set_history_length(500)
        atexit.register(readline.write_history_file, str(history_file))

        console.print(
            Panel(
                "[bold cyan]CodePilot Interactive Mode[/bold cyan]\n"
                f"[dim]{self.provider}/{self.model} · "
                "Type 'help' for commands or 'exit' to quit[/dim]",
                border_style="cyan",
            )
        )

        while True:
            try:
                task = input("\n> ").strip()
                if not task:
                    continue

                # ── Built-in commands ─────────────────────────────────────
                if task.lower() in ("exit", "quit", "q"):
                    console.print("[dim]Goodbye![/dim]")
                    break
                if task.lower() == "clear":
                    os.system("clear" if os.name != "nt" else "cls")
                    continue
                if task.lower() == "history":
                    n = readline.get_current_history_length()
                    for i in range(1, n + 1):
                        console.print(f"  {i}: {readline.get_history_item(i)}")
                    continue
                if task.lower() in ("help", "?"):
                    self._show_help()
                    continue
                if task.lower() == "memory":
                    self._show_memory()
                    continue

                # ── Load memory context ───────────────────────────────────
                memory_ctx = self._load_memory_context()
                if memory_ctx:
                    console.print(
                        "[dim]↳ Loaded memory context from previous sessions[/dim]"
                    )

                self.run(task, memory_context=memory_ctx)

            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted. Type 'exit' to quit.[/yellow]")
            except EOFError:
                console.print("\n[dim]Goodbye![/dim]")
                break
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                logger.exception("Interactive task failed")

    def _show_memory(self) -> None:
        """Show recent structured memories for the current project."""
        ctx = self._load_memory_context()
        if ctx:
            console.print(Panel(ctx, title="Memory", border_style="dim"))
        else:
            console.print("[dim]No memory found for this project yet.[/dim]")

    def _show_help(self) -> None:
        console.print(
            Panel(
                "[bold]Commands:[/bold]\n"
                "  [cyan]<task>[/cyan]    — Execute a development task\n"
                "  [cyan]memory[/cyan]   — Show memory from previous sessions\n"
                "  [cyan]clear[/cyan]    — Clear the terminal\n"
                "  [cyan]history[/cyan]  — Show command history\n"
                "  [cyan]help[/cyan]     — Show this help\n"
                "  [cyan]exit[/cyan]     — End the session\n"
                "  [cyan]↑/↓[/cyan]      — Navigate previous commands\n\n"
                "[bold]Examples:[/bold]\n"
                '  "Create a REST API with Flask and PostgreSQL"\n'
                '  "Build a CLI tool that converts CSV to JSON"\n'
                '  "Add user authentication to my app"\n'
                '  "Fix the failing tests"\n\n'
                "[bold]Env flags:[/bold]\n"
                "  CODEPILOT_CONFIRM_DESTRUCTIVE=true  — prompt before\n"
                "    deleting files, force-pushing, or other risky ops",
                title="CodePilot Help",
                border_style="dim",
            )
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_codepilot_runner(
    config_manager: ConfigManager,
    project_dir: str = ".",
    session_manager: Optional[SessionManager] = None,
) -> CodePilotRunner:
    return CodePilotRunner(
        config_manager=config_manager,
        project_dir=project_dir,
        session_manager=session_manager,
    )
