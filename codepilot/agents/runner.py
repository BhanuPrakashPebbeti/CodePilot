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
Structured  → ~/.codepilot/memory.db          (local memory tools — typed memories)

The first two are automatic (ADK handles them).  The third requires
agents to call memory tools (store_memory / get_recent_conversations).
"""

import asyncio
import json
import logging as _logging
import os
import sqlite3
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
from ..core.drift import DriftDetector
from ..core.renderer import Renderer, console
from ..core.session import SessionManager
from ..core.workspace import prompt_on_drift
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
        workspace: Path,                       # required — set by select_workspace()
        session_manager: Optional[SessionManager] = None,
        max_iterations: int = 8,
    ) -> None:
        self.config_manager = config_manager
        self.workspace = workspace.resolve()   # locked workspace — never changes
        self.project_dir = str(self.workspace) # string alias used by agents
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

        # Context drift tracking
        self._task_history: list[str] = []    # descriptions of completed tasks (oldest first)
        self._drift_detector = DriftDetector(
            provider=self.provider,
            model=self.model,
            api_key=self.api_key,
        )

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
            os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = self.github_token

        # Notion local tools read NOTION_TOKEN + NOTION_PARENT_PAGE_ID from env
        cfg = self.config_manager.config
        if cfg.notion.token:
            os.environ.setdefault("NOTION_TOKEN", cfg.notion.token)
        if cfg.notion.parent_page_id:
            os.environ.setdefault("NOTION_PARENT_PAGE_ID", cfg.notion.parent_page_id)

        # Slack local tools read SLACK_BOT_TOKEN + SLACK_CHANNEL from env
        if cfg.slack.bot_token:
            os.environ.setdefault("SLACK_BOT_TOKEN", cfg.slack.bot_token)
        if cfg.slack.channel:
            os.environ.setdefault("SLACK_CHANNEL", cfg.slack.channel)

        # Workspace is locked — set once, never overwritten
        os.environ["CODEPILOT_PROJECT_DIR"] = self.project_dir

    def switch_workspace(self, new_workspace: Path) -> None:
        """Switch to a new workspace (only triggered from the REPL drift prompt).

        Resets task history so the drift detector re-learns the new project.
        """
        self.workspace = new_workspace.resolve()
        self.project_dir = str(self.workspace)
        self.session_manager = SessionManager(self.project_dir)
        self._task_history = []                    # reset — new project starts fresh
        os.environ["CODEPILOT_PROJECT_DIR"] = self.project_dir
        console.print(
            f"\n[green]✓ Workspace switched:[/green] [bold]{self.workspace}[/bold]\n"
        )
        logger.info("Workspace switched to: %s", self.workspace)

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

        Queries the local memory SQLite database directly (faster
        than going through an MCP subprocess just for context loading).
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
        """Run a single task through the full pipeline against the locked workspace.

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
        finally:
            # Record task in history regardless of success/failure.
            # Keep last 10 entries — enough for drift detection, cheap to store.
            self._task_history.append(task)
            if len(self._task_history) > 10:
                self._task_history.pop(0)

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
                f"[dim]{self.provider}/{self.model}[/dim]\n"
                f"[dim]📁 {self.workspace}[/dim]",
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
        """Interactive REPL — run multiple tasks against the locked workspace.

        Features
        --------
        - Locked workspace — all tasks operate on the same project directory
        - Drift detection — warns when a new task appears unrelated to the project
        - Memory context injection — loads relevant past sessions automatically
        - Persistent command history (↑/↓ arrows)
        - Built-in commands: workspace, memory, clear, history, help, exit
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
                "[bold cyan]CodePilot — Interactive Mode[/bold cyan]\n"
                f"[dim]{self.provider}/{self.model}[/dim]\n"
                f"[dim]📁 Workspace: {self.workspace}[/dim]\n"
                "[dim]Type 'help' for commands · 'exit' to quit[/dim]",
                border_style="cyan",
            )
        )

        while True:
            try:
                task = input("\n> ").strip()
                if not task:
                    continue

                # ── Built-in commands ─────────────────────────────────────
                cmd = task.lower()
                if cmd in ("exit", "quit", "q"):
                    console.print("[dim]Goodbye![/dim]")
                    break
                if cmd == "clear":
                    os.system("clear" if os.name != "nt" else "cls")
                    continue
                if cmd == "history":
                    n = readline.get_current_history_length()
                    for i in range(1, n + 1):
                        console.print(f"  {i}: {readline.get_history_item(i)}")
                    continue
                if cmd in ("help", "?"):
                    self._show_help()
                    continue
                if cmd == "memory":
                    self._show_memory()
                    continue
                if cmd == "workspace":
                    console.print(
                        f"[cyan]Locked workspace:[/cyan] [bold]{self.workspace}[/bold]"
                    )
                    continue

                # ── Agentic context-drift check ───────────────────────────
                if self._task_history:
                    console.print("[dim]Checking context…[/dim]", end="\r")
                    drift = self._drift_detector.check(self._task_history, task)
                    console.print(" " * 25, end="\r")   # clear the "Checking" line

                    if drift:
                        history_summary = " → ".join(self._task_history[-3:])
                        proceed, new_ws = prompt_on_drift(
                            self.workspace, history_summary, task
                        )
                        if not proceed:
                            console.print("[dim]Request cancelled.[/dim]")
                            continue
                        if new_ws:
                            self.switch_workspace(new_ws)

                # ── Load memory context ───────────────────────────────────
                memory_ctx = self._load_memory_context()
                if memory_ctx:
                    console.print(
                        "[dim]↳ Memory context loaded from previous sessions[/dim]"
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
                "  [cyan]<task>[/cyan]       — Run a development task in the locked workspace\n"
                "  [cyan]workspace[/cyan]    — Show the locked workspace path\n"
                "  [cyan]memory[/cyan]       — Show memory from previous sessions\n"
                "  [cyan]clear[/cyan]        — Clear the terminal\n"
                "  [cyan]history[/cyan]      — Show command history\n"
                "  [cyan]help[/cyan]         — Show this help\n"
                "  [cyan]exit[/cyan]         — End the session\n"
                "  [cyan]↑/↓[/cyan]          — Navigate previous commands\n\n"
                "[bold]Workspace rules:[/bold]\n"
                "  • All file operations are confined to the locked workspace\n"
                "  • Paths outside the workspace are automatically rejected\n"
                "  • If your request seems unrelated to the current project,\n"
                "    CodePilot will ask before switching workspaces\n\n"
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
    workspace: Path,
    session_manager: Optional[SessionManager] = None,
) -> CodePilotRunner:
    return CodePilotRunner(
        config_manager=config_manager,
        workspace=workspace,
        session_manager=session_manager,
    )
