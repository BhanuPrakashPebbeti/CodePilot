"""Rich terminal renderer for CodePilot — Claude-Code-style streaming output.

Provides structured, phase-aware, human-readable terminal output.
No raw JSON. No tool names without context. Clean, informative, beautiful.

Architecture:
  Renderer             — main entry, owns the console and phase state
  PhaseTracker         — tracks PLAN / EXECUTE / VERIFY / FIX phases
  ToolRenderer         — formats tool call arguments and results
  PermissionGate       — interactive permission prompts
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.tree import Tree


console = Console()


# ============================================================================
# PHASE TRACKING
# ============================================================================

class Phase(str, Enum):
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    FIX = "fix"
    COMPLETE = "complete"


PHASE_STYLE = {
    Phase.PLAN:     ("💭", "bold blue",    "Planning"),
    Phase.EXECUTE:  ("🔨", "bold yellow",  "Executing"),
    Phase.VERIFY:   ("🧪", "bold magenta", "Verifying"),
    Phase.FIX:      ("🔧", "bold red",     "Fixing"),
    Phase.COMPLETE: ("✅", "bold green",   "Complete"),
}


@dataclass
class StepRecord:
    """Record of a single tool call."""
    tool_name: str
    arguments: Dict[str, Any]
    phase: Phase
    start_time: float
    end_time: Optional[float] = None
    ok: Optional[bool] = None
    summary: str = ""
    output_lines: int = 0


@dataclass
class PhaseTracker:
    """Tracks current development phase and step counts."""
    current: Phase = Phase.PLAN
    steps: List[StepRecord] = field(default_factory=list)
    phase_counts: Dict[Phase, int] = field(default_factory=lambda: {p: 0 for p in Phase})

    def transition(self, new_phase: Phase) -> bool:
        """Transition to a new phase. Returns True if phase changed."""
        if new_phase == self.current:
            return False
        self.current = new_phase
        return True

    def record_step(self, step: StepRecord) -> None:
        self.steps.append(step)
        self.phase_counts[step.phase] = self.phase_counts.get(step.phase, 0) + 1


# ============================================================================
# TOOL CLASSIFICATION
# ============================================================================

# Maps tool names → human-readable action verbs
TOOL_LABELS = {
    # File writes
    "write_file":        "Write",
    "create_file":       "Create",
    "append_file":       "Append",
    "edit_lines":        "Edit",
    "edit_line":         "Edit",
    "insert_lines":      "Insert",
    "delete_lines":      "Delete",
    "replace_in_file":   "Replace",
    "copy_file":         "Copy",
    "move_file":         "Move",
    "delete_file":       "Delete",
    # File reads
    "read_file":         "Read",
    "read_lines":        "Read",
    "count_lines":       "Count",
    "file_exists":       "Check",
    "get_file_info":     "Inspect",
    "file_summary":      "Summarize",
    # Directories
    "create_directory":         "Create dir",
    "create_project_structure": "Scaffold",
    "list_directory":           "List",
    "list_dir":                 "List",
    "project_tree":             "Tree",
    # Bash
    "run_command":         "Run",
    "run_python":          "Python",
    "pip_install":         "pip install",
    "npm_install":         "npm install",
    "npm_run":             "npm run",
    "check_tools_available": "Check tools",
    "get_system_info":     "System info",
    # Workspace
    "detect_project":      "Detect project",
    "get_project_tree":    "Project tree",
    "find_files":          "Find files",
    "get_file_overview":   "Overview",
    "read_dependencies":   "Dependencies",
    "search_codebase":     "Search",
    "search_in_files":     "Search",
    # Test
    "run_pytest":          "pytest",
    "run_npm_test":        "npm test",
    "run_single_test":     "Test",
    "check_python_syntax": "Syntax check",
    "check_json_syntax":   "JSON check",
    "lint_python":         "Lint",
    "verify_server_starts":"Verify server",
    "assert_file_exists":  "Assert exists",
    "assert_file_contains":"Assert contains",
    "assert_command_succeeds": "Assert cmd",
    # Debug
    "parse_error":           "Parse error",
    "find_errors_in_output": "Find errors",
    "diagnose_import_error": "Diagnose import",
    "read_log_tail":         "Read log",
    "check_port_in_use":     "Check port",
    "diff_files":            "Diff",
    # Git
    "git_init":          "git init",
    "git_status":        "git status",
    "git_add":           "git add",
    "git_commit":        "git commit",
    "git_commit_all":    "git commit -a",
    "git_log":           "git log",
    "git_diff":          "git diff",
    "git_branch":        "git branch",
    "git_create_branch": "git branch",
    "git_checkout":      "git checkout",
    "git_info":          "git info",
    # Tasks
    "create_plan":       "Plan",
    "get_current_task":  "Next task",
    "start_task":        "Start task",
    "complete_task":     "Complete task",
    "fail_task":         "Fail task",
    "skip_task":         "Skip task",
    "add_task":          "Add task",
    "replan":            "Replan",
    "get_plan_status":   "Status",
    "list_tasks":        "Tasks",
    "clear_tasks":       "Clear",
    "update_task":       "Update task",
    "get_next_task":     "Next task",
    "get_progress_status":"Progress",
}

# Tool categories for icon selection
_FILE_WRITE  = {"write_file","create_file","append_file","edit_lines","edit_line",
                "insert_lines","delete_lines","replace_in_file","copy_file",
                "move_file","delete_file"}
_FILE_READ   = {"read_file","read_lines","count_lines","file_exists",
                "get_file_info","file_summary"}
_DIR         = {"create_directory","create_project_structure","list_directory",
                "list_dir","project_tree"}
_BASH        = {"run_command","run_python","pip_install","npm_install",
                "npm_run","check_tools_available","get_system_info"}
_TEST        = {"run_pytest","run_npm_test","run_single_test",
                "check_python_syntax","check_json_syntax","lint_python",
                "verify_server_starts","assert_file_exists",
                "assert_file_contains","assert_command_succeeds"}
_DEBUG       = {"parse_error","find_errors_in_output","diagnose_import_error",
                "read_log_tail","check_port_in_use","diff_files"}
_GIT         = {"git_init","git_status","git_add","git_commit","git_commit_all",
                "git_log","git_diff","git_branch","git_create_branch",
                "git_checkout","git_info"}
_WORKSPACE   = {"detect_project","get_project_tree","find_files",
                "get_file_overview","read_dependencies","search_codebase",
                "search_in_files"}
_TASK        = {"create_plan","get_current_task","start_task","complete_task",
                "fail_task","skip_task","add_task","replan","get_plan_status",
                "list_tasks","clear_tasks","update_task","get_next_task",
                "get_progress_status"}


def _tool_icon(name: str) -> str:
    if name in _FILE_WRITE: return "📝"
    if name in _FILE_READ:  return "📖"
    if name in _DIR:        return "📁"
    if name in _BASH:       return "💻"
    if name in _TEST:       return "🧪"
    if name in _DEBUG:      return "🔍"
    if name in _GIT:        return "🔀"
    if name in _WORKSPACE:  return "🗂 "
    if name in _TASK:       return "📋"
    return "⚙ "


def _infer_phase(tool_name: str) -> Phase:
    """Infer the development phase from the tool being called."""
    if tool_name in _TASK:
        return Phase.PLAN
    if tool_name in (_WORKSPACE | _FILE_READ):
        return Phase.PLAN
    if tool_name in (_FILE_WRITE | _DIR | _BASH):
        return Phase.EXECUTE
    if tool_name in _TEST:
        return Phase.VERIFY
    if tool_name in _DEBUG:
        return Phase.FIX
    if tool_name in _GIT:
        return Phase.EXECUTE
    return Phase.EXECUTE


# ============================================================================
# RENDERER
# ============================================================================

class Renderer:
    """Main terminal renderer for CodePilot agent execution.
    
    Provides clean, structured, phase-aware output:
    
      ╭─ 💭 Planning ─────────────────────────╮
      │                                        │
      │  🗂  Detect project → ./               │
      │     ✓ Python / FastAPI                  │
      │                                        │
      ╰────────────────────────────────────────╯
      
      ╭─ 🔨 Executing ────────────────────────╮
      │                                        │
      │  📝 Write → src/main.py (45 lines)     │
      │     ✓ Written                          │
      │                                        │
      │  💻 Run → pip install flask            │
      │     ✓ Installed                        │
      │     │ Successfully installed flask-3.0 │
      │                                        │
      ╰────────────────────────────────────────╯
    """

    def __init__(self) -> None:
        self.tracker = PhaseTracker()
        self.start_time = time.time()
        self.step_count = 0
        self._current_step: Optional[StepRecord] = None
        self._last_thinking_had_content = False
        self._thinking_buffer = ""

    def reset(self) -> None:
        """Reset for a new task execution."""
        self.tracker = PhaseTracker()
        self.start_time = time.time()
        self.step_count = 0
        self._current_step = None
        self._last_thinking_had_content = False
        self._thinking_buffer = ""

    # ------------------------------------------------------------------
    # THINKING (AI reasoning text)
    # ------------------------------------------------------------------

    def on_thinking(self, text: str) -> None:
        """Handle streamed AI thinking/reasoning tokens."""
        if not text or not text.strip():
            return
        self._thinking_buffer += text

    def flush_thinking(self) -> None:
        """Flush accumulated thinking text to terminal."""
        text = self._thinking_buffer.strip()
        if not text:
            return
        self._thinking_buffer = ""
        self._last_thinking_had_content = True
        # Show thinking as indented dim text — compact, no panel
        lines = text.split("\n")
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Skip lines that are just markdown headers for cleaner output
            if stripped.startswith("##"):
                header = stripped.lstrip("#").strip()
                console.print(f"  [bold dim]{header}[/bold dim]", highlight=False)
            elif stripped.startswith("- ") or stripped.startswith("* "):
                console.print(f"    [dim]{stripped}[/dim]", highlight=False)
            elif stripped.startswith("`"):
                console.print(f"    [dim cyan]{stripped}[/dim cyan]", highlight=False)
            else:
                console.print(f"  [dim italic]{stripped}[/dim italic]", highlight=False)

    # ------------------------------------------------------------------
    # TOOL START
    # ------------------------------------------------------------------

    def on_tool_start(self, tool_name: str, tool_input: Dict[str, Any]) -> None:
        """Handle tool invocation start — show what the agent is doing."""
        self.flush_thinking()
        self.step_count += 1

        # Phase transition
        new_phase = _infer_phase(tool_name)
        if self.tracker.transition(new_phase):
            self._print_phase_header(new_phase)

        # Record
        step = StepRecord(
            tool_name=tool_name,
            arguments=tool_input,
            phase=new_phase,
            start_time=time.time(),
        )
        self._current_step = step

        # Print tool action line
        icon = _tool_icon(tool_name)
        label = TOOL_LABELS.get(tool_name, tool_name)
        detail = self._format_tool_args(tool_name, tool_input)

        if detail:
            console.print(f"  {icon} [bold]{label}[/bold] [dim]→[/dim] {detail}", highlight=False)
        else:
            console.print(f"  {icon} [bold]{label}[/bold]", highlight=False)

    # ------------------------------------------------------------------
    # TOOL END
    # ------------------------------------------------------------------

    def on_tool_end(self, tool_name: str, output_str: str) -> None:
        """Handle tool result — show success/failure and relevant output."""
        # Parse
        parsed = None
        try:
            parsed = json.loads(output_str)
        except (json.JSONDecodeError, TypeError):
            pass

        # Update step record
        if self._current_step:
            self._current_step.end_time = time.time()
            if parsed:
                self._current_step.ok = parsed.get("ok", True)
                self._current_step.summary = parsed.get("message", "")
            self.tracker.record_step(self._current_step)

        # Dispatch to category-specific formatter
        if tool_name in _BASH:
            self._render_bash_output(tool_name, parsed, output_str)
        elif tool_name in _FILE_WRITE | _DIR:
            self._render_file_result(parsed, output_str)
        elif tool_name in _TEST:
            self._render_test_result(tool_name, parsed, output_str)
        elif tool_name in _WORKSPACE:
            self._render_workspace_result(tool_name, parsed, output_str)
        elif tool_name in _GIT:
            self._render_git_result(parsed, output_str)
        elif tool_name in _TASK:
            self._render_task_result(tool_name, parsed, output_str)
        elif tool_name in _DEBUG:
            self._render_debug_result(tool_name, parsed, output_str)
        elif tool_name in _FILE_READ:
            self._render_status(parsed, output_str)
        else:
            self._render_status(parsed, output_str)

    # ------------------------------------------------------------------
    # COMPLETION SUMMARY
    # ------------------------------------------------------------------

    def on_complete(self) -> None:
        """Print final summary after task execution."""
        self.flush_thinking()
        elapsed = time.time() - self.start_time

        console.print()
        console.print(Rule(style="dim"))

        # Phase breakdown
        parts = []
        for phase in (Phase.PLAN, Phase.EXECUTE, Phase.VERIFY, Phase.FIX):
            count = self.tracker.phase_counts.get(phase, 0)
            if count > 0:
                icon, _, label = PHASE_STYLE[phase]
                parts.append(f"{icon} {label}: {count}")

        succeeded = sum(1 for s in self.tracker.steps if s.ok is True)
        failed = sum(1 for s in self.tracker.steps if s.ok is False)

        summary = f"  [dim]{elapsed:.1f}s[/dim] · [dim]{self.step_count} steps[/dim]"
        if succeeded:
            summary += f" · [green]{succeeded} succeeded[/green]"
        if failed:
            summary += f" · [red]{failed} failed[/red]"
        console.print(summary, highlight=False)

        if parts:
            console.print(f"  [dim]{' │ '.join(parts)}[/dim]", highlight=False)
        console.print()

    # ------------------------------------------------------------------
    # PHASE HEADER
    # ------------------------------------------------------------------

    def _print_phase_header(self, phase: Phase) -> None:
        icon, style, label = PHASE_STYLE[phase]
        console.print()
        console.print(f"  [{style}]{'─' * 3} {icon} {label} {'─' * 40}[/{style}]", highlight=False)
        console.print()

    # ------------------------------------------------------------------
    # ARGUMENT FORMATTING
    # ------------------------------------------------------------------

    def _format_tool_args(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Return a concise human-readable string for tool arguments."""
        if not args:
            return ""

        # File write: path + line count
        if tool_name in ("write_file", "create_file"):
            path = args.get("path", "")
            content = args.get("content", "")
            n = content.count("\n") + 1 if content else 0
            return f"[cyan]{escape(path)}[/cyan] [dim]({n} lines)[/dim]"

        # File read: path
        if tool_name in ("read_file", "get_file_overview", "get_file_info",
                         "check_python_syntax", "check_json_syntax", "lint_python",
                         "file_exists", "file_summary", "count_lines"):
            path = args.get("path") or args.get("file_path", "")
            return f"[cyan]{escape(path)}[/cyan]"

        # Read lines: path + range
        if tool_name == "read_lines":
            path = args.get("path", "")
            s, e = args.get("start", "?"), args.get("end", "?")
            return f"[cyan]{escape(path)}[/cyan] [dim]L{s}–{e}[/dim]"

        # Replace
        if tool_name == "replace_in_file":
            path = args.get("path", "")
            search = (args.get("search", "") or "")[:40]
            return f"[cyan]{escape(path)}[/cyan] [dim]\"{escape(search)}…\"[/dim]"

        # Edit/insert/delete lines
        if tool_name in ("edit_lines", "insert_lines", "delete_lines", "edit_line"):
            path = args.get("path", "")
            return f"[cyan]{escape(path)}[/cyan]"

        # Directories
        if tool_name == "create_directory":
            return f"[cyan]{escape(args.get('path', ''))}[/cyan]"
        if tool_name == "create_project_structure":
            base = args.get("base_path", "")
            structure = args.get("structure", "")
            dirs = [d.strip() for d in structure.split(",")][:5]
            return f"[cyan]{escape(base)}/[/cyan] [dim]({', '.join(dirs)}{'…' if len(structure.split(',')) > 5 else ''})[/dim]"
        if tool_name in ("list_directory", "list_dir"):
            return f"[cyan]{escape(args.get('path', '.'))}[/cyan]"

        # Bash commands
        if tool_name == "run_command":
            cmd = args.get("command", "")
            cwd = args.get("cwd", "")
            loc = f" [dim](in {escape(cwd)})[/dim]" if cwd and cwd != "." else ""
            return f"[yellow]$ {escape(cmd)}[/yellow]{loc}"
        if tool_name == "run_python":
            code = args.get("code_or_file", "")
            if "\n" in code or len(code) > 60:
                return "[yellow]$ python <inline>[/yellow]"
            return f"[yellow]$ python {escape(code)}[/yellow]"
        if tool_name == "pip_install":
            return f"[yellow]{escape(args.get('packages', ''))}[/yellow]"
        if tool_name == "npm_install":
            pkgs = args.get("packages", "")
            cwd = args.get("cwd", "")
            dev = " [dim]--save-dev[/dim]" if args.get("dev") else ""
            loc = f" [dim](in {escape(cwd)})[/dim]" if cwd and cwd != "." else ""
            return f"[yellow]{escape(pkgs)}{dev}[/yellow]{loc}"
        if tool_name == "npm_run":
            script = args.get("script", "")
            cwd = args.get("cwd", "")
            loc = f" [dim](in {escape(cwd)})[/dim]" if cwd and cwd != "." else ""
            return f"[yellow]{escape(script)}[/yellow]{loc}"

        # Test
        if tool_name == "run_pytest":
            d = args.get("directory", ".")
            return f"[dim]{escape(d)}[/dim]"
        if tool_name == "verify_server_starts":
            cmd = args.get("command", "")
            return f"[yellow]$ {escape(cmd)}[/yellow]"
        if tool_name == "assert_file_exists":
            return f"[dim]{escape(args.get('paths', ''))}[/dim]"

        # Workspace
        if tool_name in ("detect_project", "get_project_tree"):
            return f"[dim]{escape(args.get('directory', '.'))}[/dim]"
        if tool_name in ("search_codebase", "search_in_files"):
            return f"[dim]\"{escape(args.get('query', ''))}\"[/dim]"
        if tool_name == "find_files":
            return f"[dim]{escape(args.get('pattern', '*'))}[/dim]"

        # Git
        if tool_name in ("git_commit", "git_commit_all"):
            return f"[dim]\"{escape(args.get('message', ''))}\"[/dim]"
        if tool_name == "git_create_branch":
            return f"[dim]{escape(args.get('name', ''))}[/dim]"

        # Debug
        if tool_name == "parse_error":
            text = (args.get("error_text", "") or "")[:60]
            return f"[dim]\"{escape(text)}…\"[/dim]"
        if tool_name == "diagnose_import_error":
            return f"[dim]{escape(args.get('module_name', ''))}[/dim]"

        # Tasks
        if tool_name == "create_plan":
            return f"[dim]{escape(args.get('goal', '')[:60])}[/dim]"
        if tool_name == "add_task":
            return f"[dim]{escape(args.get('title', ''))}[/dim]"
        if tool_name in ("complete_task", "fail_task", "start_task", "skip_task"):
            return f"[dim]#{args.get('task_id', '?')}[/dim]"

        return ""

    # ------------------------------------------------------------------
    # OUTPUT RENDERERS
    # ------------------------------------------------------------------

    def _render_bash_output(self, tool_name: str, parsed: Optional[dict], raw: str) -> None:
        """Render bash/command output: show stdout/stderr cleanly."""
        if not parsed:
            self._render_raw_fallback(raw)
            return

        ok = parsed.get("ok", False)
        data = parsed.get("data", {})
        stdout = stderr = ""
        if isinstance(data, dict):
            stdout = data.get("stdout", "") or data.get("output", "") or ""
            stderr = data.get("stderr", "") or ""
        elif isinstance(data, str):
            stdout = data

        msg = parsed.get("message", "")
        err = parsed.get("error", "")

        if ok:
            status = msg or "Done"
            console.print(f"     [green]✓ {escape(status[:120])}[/green]", highlight=False)
        else:
            status = err or "Failed"
            console.print(f"     [red]✗ {escape(status[:150])}[/red]", highlight=False)

        # Show stdout (truncated to 30 lines)
        if stdout and stdout.strip():
            lines = stdout.strip().split("\n")
            max_show = 30
            for line in lines[:max_show]:
                console.print(f"     [dim]│[/dim] {escape(line[:200])}", highlight=False)
            if len(lines) > max_show:
                console.print(f"     [dim]│ … {len(lines) - max_show} more lines[/dim]", highlight=False)

        # Show stderr
        if stderr and stderr.strip() and not ok:
            for line in stderr.strip().split("\n")[:15]:
                console.print(f"     [red dim]│ {escape(line[:200])}[/red dim]", highlight=False)

    def _render_file_result(self, parsed: Optional[dict], raw: str) -> None:
        if not parsed:
            self._render_raw_fallback(raw)
            return
        if parsed.get("ok"):
            msg = parsed.get("message", "Done")
            console.print(f"     [green]✓ {escape(msg[:120])}[/green]", highlight=False)
        else:
            err = parsed.get("error", "Failed")
            console.print(f"     [red]✗ {escape(err[:120])}[/red]", highlight=False)

    def _render_test_result(self, tool_name: str, parsed: Optional[dict], raw: str) -> None:
        if not parsed:
            self._render_raw_fallback(raw)
            return

        ok = parsed.get("ok", False)
        data = parsed.get("data", {})
        msg = parsed.get("message", "")

        if ok:
            console.print(f"     [green]✓ {escape(msg or 'Passed')}[/green]", highlight=False)
        else:
            err = parsed.get("error", "Failed")
            console.print(f"     [red]✗ {escape(err[:120])}[/red]", highlight=False)

        # For test runners, show summary + output
        if isinstance(data, dict):
            summary = data.get("summary", {})
            if summary:
                parts = []
                if summary.get("passed"):  parts.append(f"[green]{summary['passed']} passed[/green]")
                if summary.get("failed"):  parts.append(f"[red]{summary['failed']} failed[/red]")
                if summary.get("errors"):  parts.append(f"[red]{summary['errors']} errors[/red]")
                if parts:
                    console.print(f"     [dim]│[/dim] {' · '.join(parts)}", highlight=False)

            # Show stdout for failures
            stdout = data.get("stdout", "")
            if stdout and not ok:
                for line in stdout.strip().split("\n")[:20]:
                    console.print(f"     [dim]│[/dim] {escape(line[:200])}", highlight=False)

            # Detailed errors
            errors = data.get("errors", "")
            if errors and isinstance(errors, str):
                console.print(f"     [red dim]  {escape(errors[:200])}[/red dim]", highlight=False)

    def _render_workspace_result(self, tool_name: str, parsed: Optional[dict], raw: str) -> None:
        if not parsed:
            self._render_raw_fallback(raw)
            return

        if isinstance(parsed, dict) and parsed.get("ok") is not None:
            if parsed.get("ok"):
                msg = parsed.get("message", "")
                if msg:
                    console.print(f"     [green]✓ {escape(msg[:120])}[/green]", highlight=False)
                else:
                    console.print(f"     [green]✓ Done[/green]", highlight=False)
            else:
                err = parsed.get("error", "Failed")
                console.print(f"     [red]✗ {escape(err[:120])}[/red]", highlight=False)
            return

        # detect_project returns raw JSON without ok wrapper
        if tool_name == "detect_project" and isinstance(parsed, dict):
            langs = parsed.get("languages", [])
            fws = parsed.get("frameworks", [])
            entry = parsed.get("entry_points", [])
            console.print(f"     [green]✓[/green] [dim]{', '.join(langs) if langs else 'unknown'}[/dim]", highlight=False)
            if fws:
                console.print(f"     [dim]  Frameworks: {', '.join(fws)}[/dim]", highlight=False)
            if entry:
                console.print(f"     [dim]  Entry: {', '.join(entry[:3])}[/dim]", highlight=False)
            return

        # get_project_tree returns a plain string
        if tool_name == "get_project_tree" and isinstance(raw, str) and not raw.startswith("{"):
            lines = raw.strip().split("\n")
            for line in lines[:25]:
                console.print(f"     [dim]│ {line}[/dim]", highlight=False)
            if len(lines) > 25:
                console.print(f"     [dim]│ … {len(lines) - 25} more[/dim]", highlight=False)
            return

        # Fallback
        self._render_status(parsed, raw)

    def _render_git_result(self, parsed: Optional[dict], raw: str) -> None:
        if not parsed:
            self._render_raw_fallback(raw)
            return
        if parsed.get("ok"):
            msg = parsed.get("message", "Done")
            console.print(f"     [green]✓ {escape(msg[:120])}[/green]", highlight=False)
        else:
            err = parsed.get("error", "Failed")
            console.print(f"     [red]✗ {escape(err[:120])}[/red]", highlight=False)

    def _render_task_result(self, tool_name: str, parsed: Optional[dict], raw: str) -> None:
        """Render planning/task tool results with task list display."""
        if not parsed:
            # Old todo server returns plain strings
            if raw and raw.strip():
                console.print(f"     [green]✓ {escape(raw.strip()[:120])}[/green]", highlight=False)
            return

        if parsed.get("ok"):
            msg = parsed.get("message", "Done")
            console.print(f"     [green]✓ {escape(msg[:120])}[/green]", highlight=False)

            data = parsed.get("data", {})
            # Show task list if present
            if isinstance(data, dict):
                tasks = data.get("tasks", [])
                if tasks:
                    console.print()
                    for t in tasks[:15]:
                        tid = t.get("id", "?")
                        title = t.get("title", "")
                        status = t.get("status", "pending")
                        icon = {"pending": "○", "in_progress": "◉",
                                "done": "✓", "failed": "✗",
                                "skipped": "⊘"}.get(status, "·")
                        style = {"pending": "dim", "in_progress": "yellow",
                                 "done": "green", "failed": "red",
                                 "skipped": "dim strike"}.get(status, "dim")
                        console.print(
                            f"     [{style}]{icon} #{tid}: {escape(title)}[/{style}]",
                            highlight=False
                        )
                    if len(tasks) > 15:
                        console.print(f"     [dim]… {len(tasks) - 15} more[/dim]", highlight=False)
                    console.print()

                # Show progress if present
                progress = data.get("progress", {})
                if progress:
                    total = progress.get("total", 0)
                    done = progress.get("done", 0)
                    pct = progress.get("percent", 0)
                    bar_len = 20
                    filled = int(bar_len * done / max(total, 1))
                    bar = "█" * filled + "░" * (bar_len - filled)
                    console.print(
                        f"     [dim]{bar} {pct:.0f}% ({done}/{total})[/dim]",
                        highlight=False
                    )
        else:
            err = parsed.get("error", "Failed")
            console.print(f"     [red]✗ {escape(err[:120])}[/red]", highlight=False)

    def _render_debug_result(self, tool_name: str, parsed: Optional[dict], raw: str) -> None:
        if not parsed:
            self._render_raw_fallback(raw)
            return

        if parsed.get("ok"):
            data = parsed.get("data", {})
            if tool_name == "parse_error" and isinstance(data, dict):
                etype = data.get("error_type", "")
                msg = data.get("message", "")
                fpath = data.get("file", "")
                line = data.get("line", "")
                suggestions = data.get("suggestions", [])
                if etype:
                    console.print(f"     [red bold]{escape(etype)}: {escape(msg or '')}[/red bold]", highlight=False)
                if fpath:
                    console.print(f"     [dim]  at {escape(fpath)}:{line}[/dim]", highlight=False)
                for s in suggestions[:3]:
                    console.print(f"     [yellow]  💡 {escape(s)}[/yellow]", highlight=False)
                return
            msg = parsed.get("message", "Done")
            console.print(f"     [green]✓ {escape(msg[:120])}[/green]", highlight=False)
        else:
            err = parsed.get("error", "Failed")
            console.print(f"     [red]✗ {escape(err[:120])}[/red]", highlight=False)

    def _render_status(self, parsed: Optional[dict], raw: str) -> None:
        """Generic status renderer."""
        if not parsed:
            self._render_raw_fallback(raw)
            return
        if parsed.get("ok"):
            msg = parsed.get("message", "Done")
            console.print(f"     [green]✓ {escape(msg[:120])}[/green]", highlight=False)
        elif "error" in parsed:
            console.print(f"     [red]✗ {escape(str(parsed['error'])[:120])}[/red]", highlight=False)
        else:
            console.print(f"     [dim]{escape(raw[:200])}[/dim]", highlight=False)

    def _render_raw_fallback(self, raw: str) -> None:
        """Fallback for non-JSON output."""
        if not raw or not raw.strip():
            return
        lines = raw.strip().split("\n")
        for line in lines[:10]:
            console.print(f"     [dim]{escape(line[:200])}[/dim]", highlight=False)
        if len(lines) > 10:
            console.print(f"     [dim]… {len(lines) - 10} more lines[/dim]", highlight=False)
