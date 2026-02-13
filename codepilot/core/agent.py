"""CodePilot autonomous agent — Plan → Execute → Verify → Fix.

Architecture:
  - Renderer: Rich terminal output (Claude Code style)
  - PermissionGate: Interactive safety prompts for dangerous commands
  - PhaseTracker: Tracks PLAN/EXECUTE/VERIFY/FIX phases
  - Planning MCP Server: Todo-driven development (agent works through tasks)
  - Environment MCP Server: Runtime detection and management

The agent streams events from LangGraph's ReAct agent and routes them
through the Renderer for clean, structured, human-readable output.
"""

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.rule import Rule

from .exceptions import ConfigurationError, LLMError, MCPError
from .permissions import PermissionGate, PermissionLevel
from .renderer import Renderer, console
from .session import SessionManager
from ..config import ConfigManager
from ..llm import OllamaProvider, OpenRouterProvider, LLMProvider
from ..utils.constants import PROVIDER_OLLAMA, PROVIDER_OPENROUTER
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Suppress noisy loggers
logging.getLogger("fastmcp").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """You are CodePilot, an autonomous AI software engineer. You build complete, working software projects.

## MANDATORY WORKFLOW

You MUST follow this exact workflow for EVERY task. No exceptions.

### Step 0: PLAN
Before writing any code, create a structured plan:
1. Use `detect_project()` and `get_project_tree()` to understand context
2. Use `detect_runtimes()` or `check_runtime(name)` to verify required tools are available
3. Use `create_plan(goal, tasks)` to define ordered tasks

Example:
```
create_plan(
    goal="Build React + FastAPI sudoku solver",
    tasks="Detect environment and install dependencies,Create FastAPI backend with solver,Create React frontend with grid UI,Connect frontend to backend,Install all dependencies,Run and test the full application"
)
```

### Step 1: EXECUTE (task by task)
For EACH task in your plan:
1. `get_current_task()` — see what's next
2. `start_task(task_id)` — mark it in-progress
3. Do the work (write files, run commands, install deps)
4. `complete_task(task_id)` — mark it done
5. If something fails: `fail_task(task_id, error)` then fix or `replan()`

### Step 2: VERIFY
After completing all tasks:
- Check syntax: `check_python_syntax()`, `check_json_syntax()`
- Run tests: `run_pytest()`, `run_npm_test()`
- Start servers: `verify_server_starts()`
- Assert files exist: `assert_file_exists()`

### Step 3: FIX
If verification fails:
1. Read the error output carefully
2. `parse_error()` to understand the problem
3. Fix the issue
4. Verify again
5. Repeat until everything works

## CRITICAL RULES

1. **ALWAYS create a plan first** — never free-roam
2. **ALWAYS use task tracking** — start_task → work → complete_task/fail_task
3. **ALWAYS write COMPLETE file content** — never use "..." or placeholders
4. **ALWAYS verify after each major step** — don't assume success
5. **ALWAYS run and test before finishing** — install deps, run code, verify it works
6. **NEVER skip testing** — the project must end in a runnable state
7. **NEVER use sudo or install system packages** — ask the user instead
8. **NEVER use curl | bash patterns** — these are dangerous and blocked

### Running and Testing Checklist

For Python projects:
- `pip_install(packages="...")`
- `check_python_syntax(file_path="...")`
- `run_command(command="python main.py", cwd="project_dir")`
- `verify_server_starts(command="python app.py")`

For JavaScript/React/Node projects:
- First check: `check_runtime(name="node")`
- If missing: `get_install_command(runtime="node")` and inform user
- `run_command(command="npm install", cwd="project_dir")`
- `run_command(command="npm run build", cwd="project_dir")`
- `verify_server_starts(command="npm start", cwd="project_dir")`

For full-stack:
- Install + test backend FIRST
- Install + test frontend SECOND
- Verify both communicate

## TOOL REFERENCE

### Planning & Task Management
- `create_plan(goal, tasks)` — create development plan with ordered tasks
- `get_current_task()` — get next task to work on
- `start_task(task_id)` — mark task in-progress
- `complete_task(task_id)` — mark task done
- `fail_task(task_id, error)` — mark task failed
- `skip_task(task_id, reason)` — skip non-critical task
- `add_task(title)` — add task discovered during execution
- `replan(reason, new_tasks)` — adjust plan after failures
- `get_plan_status()` — full progress report

### Environment Detection
- `detect_runtimes()` — check all installed dev tools
- `check_runtime(name, min_version)` — check specific runtime
- `get_install_command(runtime)` — get install instructions
- `check_venv(directory)` — check Python virtual env
- `create_venv(directory, name)` — create Python virtual env
- `check_node_project(directory)` — verify Node.js setup

### Workspace Intelligence
- `detect_project(directory)` — project type, language, frameworks
- `get_project_tree(directory)` — directory tree
- `find_files(directory, pattern)` — find files by glob
- `get_file_overview(path)` — imports, classes, functions
- `read_dependencies(directory)` — parse dependency files
- `search_codebase(directory, query)` — search text across files

### File Operations
- `write_file(path, content)` — write/overwrite file
- `create_file(path, content)` — create new file
- `read_file(path)` — read file content
- `read_lines(path, start, end)` — read line range
- `replace_in_file(path, search, replace)` — find & replace
- `edit_lines(path, start_line, end_line, new_content)` — replace lines
- `insert_lines(path, line_number, content)` — insert after line
- `delete_lines(path, start_line, end_line)` — delete lines
- `create_directory(path)` — create directory
- `create_project_structure(base_path, structure)` — scaffold dirs
- `list_directory(path)` / `list_dir(path)` — list contents
- `delete_file(path)` — delete file
- `copy_file(source, destination)` — copy
- `move_file(source, destination)` — move/rename

### Command Execution
- `run_command(command, cwd)` — run shell command
- `run_python(code_or_file, cwd)` — run Python
- `pip_install(packages)` — install Python packages
- `npm_install(packages, cwd, dev)` — install npm packages
- `npm_run(script, cwd)` — run npm script
- `check_tools_available()` — check installed tools
- `get_system_info()` — OS info

### Testing & Verification
- `run_pytest(directory, args)` — run pytest
- `run_npm_test(directory)` — run npm test
- `run_single_test(test_file, framework)` — run one test
- `check_python_syntax(file_path)` — validate Python
- `check_json_syntax(file_path)` — validate JSON
- `lint_python(file_path)` — run linter
- `verify_server_starts(command, cwd)` — check server starts
- `assert_file_exists(paths)` — verify files exist
- `assert_file_contains(file_path, expected)` — verify content
- `assert_command_succeeds(command)` — verify command works

### Debugging
- `parse_error(error_text)` — parse stack trace
- `find_errors_in_output(output)` — scan for error patterns
- `diagnose_import_error(module_name)` — diagnose imports
- `read_log_tail(file_path, lines)` — read log file
- `check_port_in_use(port)` — check port usage
- `diff_files(file1, file2)` — compare files

### Git
- `git_init(path)` — init repository
- `git_status(cwd)` — parsed status
- `git_add(files)` — stage files
- `git_commit(message)` — commit
- `git_commit_all(message)` — stage all + commit
- `git_log(count)` — recent commits
- `git_diff(staged)` — show changes
- `git_branch()` — list branches
- `git_create_branch(name)` — create branch
- `git_checkout(branch)` — switch branch
- `git_info()` — comprehensive repo info
"""


class CodePilotAgent:
    """Main autonomous coding agent with Plan → Execute → Verify → Fix loop."""
    
    def __init__(
        self,
        config_manager: ConfigManager,
        project_dir: str = ".",
        session_manager: Optional[SessionManager] = None,
    ):
        self.config_manager = config_manager
        self.project_dir = os.path.abspath(project_dir)
        self.session_manager = session_manager or SessionManager(project_dir)
        
        self.llm_provider: Optional[LLMProvider] = None
        self.mcp_client: Optional[MultiServerMCPClient] = None
        self.tools: List = []
        self.agent = None
        self.messages: List = []
        
        # New subsystems
        self.renderer = Renderer()
        self.permissions = PermissionGate()
        
        self._initialize_llm()
    
    # ========================================================================
    # INITIALIZATION
    # ========================================================================
    
    def _initialize_llm(self) -> None:
        """Initialize LLM provider using active_provider and active_model from config."""
        try:
            config = self.config_manager.config
            llm_config = config.llm
            provider_type = llm_config.active_provider
            model = llm_config.active_model
            
            if provider_type == PROVIDER_OPENROUTER:
                try:
                    api_key = self.config_manager.get_api_key()
                except ConfigurationError:
                    api_key = os.getenv("OPENROUTER_API_KEY")
                    if not api_key:
                        raise ConfigurationError("No OpenRouter API key found")
                
                self.llm_provider = OpenRouterProvider(
                    api_key=api_key,
                    model=model,
                    temperature=llm_config.temperature,
                    max_tokens=llm_config.max_tokens,
                )
            elif provider_type == PROVIDER_OLLAMA:
                self.llm_provider = OllamaProvider(
                    model=model,
                    base_url=llm_config.base_url or "http://localhost:11434",
                    temperature=llm_config.temperature,
                    max_tokens=llm_config.max_tokens,
                )
            else:
                raise ConfigurationError(f"Unknown provider: {provider_type}")
            
            logger.info(f"LLM: {provider_type}/{model}")
        except Exception as e:
            raise LLMError(f"LLM initialization failed: {e}")
    
    async def _initialize_mcp_async(self) -> None:
        """Initialize all MCP servers and load tools."""
        try:
            mcp_config = self._get_mcp_config()
            if not mcp_config:
                logger.warning("No MCP servers configured")
                return
            
            self.mcp_client = MultiServerMCPClient(mcp_config)
            self.tools = await self.mcp_client.get_tools()
            
            tool_names = [t.name for t in self.tools if hasattr(t, 'name')]
            logger.info(f"Loaded {len(self.tools)} tools: {', '.join(tool_names[:15])}...")
        except Exception as e:
            logger.warning(f"MCP init failed: {e}")
            self.tools = []
    
    def _get_mcp_config(self) -> Dict[str, Any]:
        """Build MCP server configuration."""
        py = sys.executable
        base = "codepilot.mcp.servers"
        
        config = {
            "workspace": {
                "command": py,
                "args": ["-m", f"{base}.workspace_server"],
                "transport": "stdio",
            },
            "filesystem": {
                "command": py,
                "args": ["-m", f"{base}.filesystem_server"],
                "transport": "stdio",
            },
            "bash": {
                "command": py,
                "args": ["-m", f"{base}.bash_server"],
                "transport": "stdio",
            },
            "testing": {
                "command": py,
                "args": ["-m", f"{base}.testing_server"],
                "transport": "stdio",
            },
            "debug": {
                "command": py,
                "args": ["-m", f"{base}.debug_server"],
                "transport": "stdio",
            },
            "git": {
                "command": py,
                "args": ["-m", f"{base}.git_server"],
                "transport": "stdio",
            },
            "planning": {
                "command": py,
                "args": ["-m", f"{base}.planning_server"],
                "transport": "stdio",
            },
            "environment": {
                "command": py,
                "args": ["-m", f"{base}.environment_server"],
                "transport": "stdio",
            },
            "todo": {
                "command": py,
                "args": ["-m", f"{base}.todo_server"],
                "transport": "stdio",
            },
        }
        
        # Optional: GitHub server (only if token configured)
        try:
            if self.config_manager.config.github.token:
                config["github"] = {
                    "command": py,
                    "args": ["-m", f"{base}.github_server"],
                    "transport": "stdio",
                }
        except Exception:
            pass
        
        return config
    
    def _initialize_agent(self) -> None:
        """Create the LangGraph ReAct agent."""
        if not self.llm_provider:
            raise LLMError("LLM not initialized")
        
        if not self.llm_provider.supports_tools():
            console.print(
                f"[yellow]⚠️  Model {self.llm_provider.get_model_name()} may not support tools well[/yellow]"
            )
        
        self.agent = create_react_agent(
            self.llm_provider.get_llm(),
            self.tools,
            prompt=SYSTEM_PROMPT,
        )
        
        tool_count = len(self.tools)
        console.print(f"[dim]Agent ready • {tool_count} tools loaded[/dim]")
    
    def _ensure_initialized(self) -> None:
        """Lazy initialization of MCP + agent on first use."""
        if self.agent is None:
            asyncio.run(self._initialize_mcp_async())
            self._initialize_agent()
    
    # ========================================================================
    # EXECUTION — Single path, no double-run
    # ========================================================================
    
    def run(self, task: str) -> str:
        """Execute a task.
        
        Args:
            task: Task description.
            
        Returns:
            Agent's final response.
        """
        self._ensure_initialized()
        
        try:
            if not self.session_manager.session:
                self.session_manager.start_session()
            
            session_task = self.session_manager.add_task(task)
            session_task.status = "in_progress"
            session_task.started_at = __import__('datetime').datetime.now()
            self.session_manager._save()
            
            result = asyncio.run(self._execute_task(task))
            
            session_task.status = "completed"
            session_task.completed_at = __import__('datetime').datetime.now()
            self.session_manager._save()
            
            return result
        
        except Exception as e:
            logger.error(f"Task failed: {e}")
            if 'session_task' in locals():
                session_task.status = "failed"
                session_task.error = str(e)
                self.session_manager._save()
            raise
    
    async def _execute_task(self, task: str) -> str:
        """Execute task with rich streaming output via Renderer.
        
        Routes LangGraph streaming events to the Renderer:
        - on_chat_model_stream → renderer.on_thinking()
        - on_tool_start → renderer.on_tool_start() + permission check
        - on_tool_end → renderer.on_tool_end()
        - completion → renderer.on_complete()
        """
        try:
            user_message = HumanMessage(content=task)
            self.messages.append(user_message)
            
            self.renderer.reset()
            final_ai_content = ""
            
            async for event in self.agent.astream_events(
                {"messages": self.messages},
                version="v2",
                config={"recursion_limit": 150},
            ):
                kind = event.get("event")
                
                # --- AI thinking (stream tokens) ---
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        content = chunk.content
                        if isinstance(content, str) and content.strip():
                            self.renderer.on_thinking(content)
                
                # --- Tool start ---
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    tool_input = event.get("data", {}).get("input", {})
                    
                    # Permission check for bash commands
                    if tool_name == "run_command":
                        cmd = tool_input.get("command", "")
                        decision = self.permissions.check(cmd)
                        if decision.level == PermissionLevel.BLOCKED:
                            self.renderer.flush_thinking()
                            console.print(f"\n  [red bold]🚫 BLOCKED:[/red bold] {decision.reason}")
                            console.print(f"  [dim]{cmd}[/dim]\n")
                            continue
                        elif decision.level == PermissionLevel.NEEDS_PERMISSION:
                            self.renderer.flush_thinking()
                            decision = self.permissions.prompt(decision)
                            if not decision.approved:
                                console.print(f"  [yellow]⊘ Denied by user[/yellow]\n")
                                continue
                    
                    self.renderer.on_tool_start(tool_name, tool_input)
                
                # --- Tool end ---
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    output = event.get("data", {}).get("output", "")
                    output_str = str(output)
                    
                    self.renderer.on_tool_end(tool_name, output_str)
                
                # --- Final AI message ---
                elif kind == "on_chat_model_end":
                    output = event.get("data", {}).get("output")
                    if output and hasattr(output, "content") and isinstance(output.content, str):
                        final_ai_content = output.content
            
            # Flush any remaining thinking and show summary
            self.renderer.flush_thinking()
            self.renderer.on_complete()
            
            if final_ai_content:
                self.messages.append(AIMessage(content=final_ai_content))
            
            return final_ai_content or "Task completed."
        
        except Exception as e:
            logger.error(f"Execution error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise LLMError(f"Execution failed: {e}")
    
    # ========================================================================
    # INTERACTIVE MODE
    # ========================================================================
    
    def run_interactive(self) -> None:
        """Interactive REPL session."""
        self._ensure_initialized()
        
        console.print("\n[dim]Type your request, 'quit' to exit, 'clear' to reset[/dim]\n")
        
        while True:
            try:
                user_input = input("codepilot> ").strip()
                
                if not user_input:
                    continue
                
                if user_input.lower() in ("quit", "exit", "q"):
                    console.print("[dim]Goodbye![/dim]")
                    break
                
                if user_input.lower() == "clear":
                    self.messages = []
                    console.print("[dim]History cleared[/dim]")
                    continue
                
                if user_input.lower() == "history":
                    self._show_history()
                    continue
                
                if user_input.lower() == "tools":
                    self._show_tools()
                    continue
                
                if user_input.lower() == "help":
                    self._show_help()
                    continue
                
                result = self.run(user_input)
                console.print(f"\n{result}\n")
            
            except KeyboardInterrupt:
                console.print("\n[dim]Interrupted. Type 'quit' to exit.[/dim]")
                continue
            except Exception as e:
                console.print(f"\n[red]Error: {e}[/red]\n")
    
    def _show_help(self) -> None:
        console.print("""
[bold]Commands:[/bold]
  quit, exit, q  — Exit session
  clear          — Clear conversation history
  history        — Show message history
  tools          — List available tools
  help           — Show this help

Or type any task to execute.
""")
    
    def _show_history(self) -> None:
        if not self.messages:
            console.print("[dim]No history[/dim]")
            return
        
        console.print("\n[bold]History:[/bold]")
        for msg in self.messages:
            role = msg.type if hasattr(msg, 'type') else 'unknown'
            content = str(msg.content)[:100]
            console.print(f"  [{role}] {content}{'...' if len(str(msg.content)) > 100 else ''}")
        console.print()
    
    def _show_tools(self) -> None:
        if not self.tools:
            console.print("[dim]No tools loaded[/dim]")
            return
        
        console.print(f"\n[bold]Available Tools ({len(self.tools)}):[/bold]")
        for tool in sorted(self.tools, key=lambda t: t.name if hasattr(t, 'name') else ''):
            name = tool.name if hasattr(tool, 'name') else str(tool)
            desc = ""
            if hasattr(tool, 'description'):
                desc = tool.description.split('\n')[0][:60]
            console.print(f"  [cyan]{name:<30}[/cyan] {desc}")
        console.print()


def create_agent(
    config_manager: ConfigManager,
    project_dir: str = ".",
    session_manager: Optional[SessionManager] = None,
) -> CodePilotAgent:
    """Factory function to create agent."""
    return CodePilotAgent(
        config_manager=config_manager,
        project_dir=project_dir,
        session_manager=session_manager,
    )
