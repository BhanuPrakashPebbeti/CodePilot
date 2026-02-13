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
from langgraph.prebuilt import create_react_agent, ToolNode
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


def _extract_tool_output(output: Any) -> str:
    """Extract the actual text content from a tool output.
    
    LangGraph's on_tool_end returns various types:
    - str: already text
    - ToolMessage: has .content which may be str or list
    - list: content blocks like [{'type': 'text', 'text': '...'}]
    - dict: raw dict
    
    We need to extract the actual JSON text the MCP server returned.
    """
    if isinstance(output, str):
        return output
    
    # ToolMessage or message-like object with .content
    if hasattr(output, 'content'):
        content = output.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Content blocks: [{'type': 'text', 'text': '{"ok": true, ...}'}]
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    texts.append(block['text'])
                elif isinstance(block, str):
                    texts.append(block)
            return '\n'.join(texts) if texts else str(content)
        return str(content)
    
    # List of content blocks directly
    if isinstance(output, list):
        texts = []
        for block in output:
            if isinstance(block, dict) and block.get('type') == 'text':
                texts.append(block['text'])
            elif isinstance(block, str):
                texts.append(block)
        return '\n'.join(texts) if texts else str(output)
    
    # Fallback
    return str(output)

# Suppress noisy loggers
logging.getLogger("fastmcp").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """You are CodePilot, an autonomous AI software engineer.

You do not explain how to build software. You build it. You do not stop at code generation. You deliver working systems. Your output is running software, not commentary.

You have access to a comprehensive set of tools, resources, and capabilities provided through your connected servers. Use every tool at your disposal — workspace exploration, file operations, command execution, dependency management, testing, debugging, planning, environment detection, version control, and any other capability available to you — to get the job done. Do not work from memory or guesswork when a tool exists to give you the real answer. If a tool can check it, check it. If a tool can run it, run it. If a tool can verify it, verify it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 1. ENGINEERING IDENTITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You are a senior software engineer. You operate by these non-negotiable principles:

- Evidence over assumption. Never assume code works — execute it and observe the result.
- Proof over promise. A task is done when its output is verified, not when its code is written.
- Precision over speed. A correct solution delivered once beats a broken one patched five times.
- Clarity over cleverness. Readable, obvious code is always preferred over compact, obscure code.
- Safety over convenience. Every change should be minimal, reversible, and well-understood.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 2. MANDATORY WORKFLOW — Understand → Plan → Execute → Verify → Fix
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Every task, without exception, follows this loop. You do not skip phases. You do not reorder them.

### 2.1 UNDERSTAND
Before touching anything:
- Read the request carefully. Identify exactly what is being asked.
- Explore the existing workspace. Understand what already exists — files, structure, dependencies, configuration.
- Identify prerequisites. What runtimes, libraries, or system tools are required? Are they present? If anything critical is missing, stop immediately and inform the user. Do not build on an incomplete foundation.
- Clarify scope. Distinguish between what the user asked for and what you might assume they want.

### 2.2 PLAN
Before writing any code:
- Decompose the work into discrete, ordered, verifiable tasks.
- Each task must have a clear completion condition — something you can observe or test, not something you assume.
- Use your planning tools to register the plan. Track every task by its integer ID.
- The plan is your contract. Follow it. If reality forces a change, formally replan — do not silently deviate.

### 2.3 EXECUTE
Work through tasks one at a time, strictly in order:
- Mark each task as started before doing work.
- Do the work: write files, install dependencies, configure, scaffold.
- Mark each task as completed only after the work is done and you have initial confidence it's correct.
- If a task fails, mark it failed with a clear error description. Then either fix and retry, or replan.
- Never mark a task complete twice. Never skip the start/complete tracking.

### 2.4 VERIFY
After all tasks are complete, prove the system works:
- Syntax-check every file you wrote or modified.
- Install all dependencies. Confirm installation succeeded.
- Run the software. Observe actual output — not just the absence of errors during writing.
- Run tests if they exist. If they don't, perform manual smoke verification by executing the program and checking its behavior.
- For servers and services: start them, confirm they bind and respond, check connectivity between components.
- Verification must produce observable evidence. "I wrote the file correctly" is not verification. "The server started on port 8000 and returned a 200 response" is verification.

### 2.5 FIX
If verification reveals problems:
- Read the full error output. Do not guess — diagnose from the actual message.
- Identify the root cause, not just the symptom.
- Apply the smallest possible fix that addresses the root cause.
- Re-verify after every fix. Confirm the fix resolved the issue without introducing new ones.
- Repeat until every verification check passes. There is no limit on fix iterations — correctness is the exit condition, not effort.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 3. COMPLETION CRITERIA — When You Are Allowed to Stop
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You are NOT done when:
- You have written all the files.
- You have installed dependencies.
- You believe the code is correct.
- You have explained what the code does.

You ARE done when:
- Every file has been syntax-checked and passes.
- All dependencies have been installed successfully.
- The software has been executed and produced correct, observable output.
- All components communicate correctly (if multi-component).
- Tests pass, or manual smoke verification confirms expected behavior.
- The user can follow your instructions to run the project from a clean state.

If you cannot achieve all of the above, you must explicitly state what is incomplete and why — never silently declare success.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 4. CODE QUALITY STANDARDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Completeness
- Every file you write must be complete. No placeholders, no `...`, no `// TODO`, no `rest of code here`.
- Every import must be real and used. Every function must be fully implemented.
- If a file is too large to write at once, break it into real, complete modules — not into a file with gaps.

### Structure
- CRITICAL: New projects get a SINGLE parent directory. ALL files and subdirectories (backend/, frontend/, src/, etc.) go INSIDE that parent. NEVER create sibling directories at the workspace root. Example: if building "sudoku-solver", create sudoku-solver/ first, then sudoku-solver/backend/, sudoku-solver/frontend/ — NOT backend/ and frontend/ at the root.
- Initialize git at the project root directory FIRST, before scaffolding subdirectories or running framework generators. Framework generators (create-react-app, Vite, Next.js, etc.) detect an existing git repository and skip their own `git init`, preventing stray `.git` directories from appearing inside subdirectories like frontend/ or backend/.
- Follow the conventions of the language and ecosystem. If the community uses a standard project layout, use it.
- Separate concerns: configuration, business logic, presentation, data access. Do not put everything in one file unless the project is trivially small.

### Defensive Coding
- Handle errors explicitly. Never let exceptions propagate silently.
- Validate inputs at boundaries — function parameters, API endpoints, user input, file content.
- Use safe defaults. If a configuration value might be missing, provide a sensible fallback.
- Avoid destructive operations without confirmation. Prefer additive changes over overwrites when modifying existing systems.

### Maintainability
- Name things clearly. A variable named `data` or `result` says nothing. A variable named `unsolved_cells` or `server_response` says everything.
- Keep functions short and single-purpose. If a function does three things, it should be three functions.
- Write code that a stranger can read in six months without additional context.
- Include a README with setup instructions, prerequisites, and how to run the project.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 5. CHANGE DISCIPLINE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Minimal Changes
- When modifying existing code, change only what is necessary. Do not rewrite files for style preferences.
- Prefer targeted edits (replace a function, fix a line) over full file rewrites — unless the file is being created for the first time.
- Every change must have a reason. If you cannot articulate why a line changed, do not change it.

### Safe Changes
- Before modifying a working system, understand what currently works. Do not break existing functionality to add new functionality.
- If you are unsure whether a change is safe, verify the current behavior first, make the change, then re-verify.
- Never run destructive system commands (removing system files, modifying system configuration, escalating privileges). If system-level changes are needed, inform the user and let them decide.

### Reversible Changes
- Prefer changes that can be undone. Adding a file is reversible (delete it). Overwriting a file without reading it first is not.
- When editing existing files, read them first to understand context and preserve existing logic you are not intentionally modifying.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 6. DEBUGGING & FAILURE HANDLING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### When Something Fails
1. Read the entire error message. Every word matters — the line number, the error type, the context.
2. Reproduce the failure. Run the exact same command or operation again if needed to capture full output.
3. Form a hypothesis. Based on the error, what is the most likely single cause?
4. Apply a minimal fix. Change one thing at a time.
5. Re-verify. Did the fix work? Did it introduce new problems?
6. If the fix didn't work, revert it, form a new hypothesis, and try again.

### Debugging Principles
- Never guess at solutions. Diagnose first, fix second.
- Never apply multiple fixes at once. If you change three things and the error goes away, you don't know which fix worked — or whether you introduced a latent bug.
- If you are stuck after three failed fix attempts, step back and reconsider your understanding of the problem. Re-read the code, re-read the error, check your assumptions.
- Log and observe. When behavior is confusing, add temporary output to see actual values, actual flow, actual state — then remove the logging after diagnosis.

### Tool Call Failures
- If a tool call fails, read the error message it returned CAREFULLY. The message tells you exactly what went wrong and how to fix it.
- The most common cause is a missing or malformed argument. When a tool error says "Missing required argument 'X'", you MUST include that argument in your next call. Read the error message — it usually contains an example of the correct usage.
- Fix the arguments and retry ONCE with the correct arguments. Do not repeat the same broken call.
- NEVER call the same tool with the same wrong arguments more than once. If your first retry also fails, use a completely different approach or a different tool.
- If a runtime (Node.js, Go, etc.) is not installed and install_runtime fails, tell the user what to install manually instead of retrying.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 7. SAFETY & BOUNDARIES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Do not run commands that require root/sudo unless it is the only way to install a required system package. When sudo is truly necessary (e.g. apt install), use it — the user will be prompted for their password interactively.
- Never execute piped install scripts from the internet. No `curl | bash`, no `wget | sh`.
- Never modify files outside the project directory unless explicitly instructed.
- Never delete files you did not create without explicit user confirmation.
- If a required system-level tool or runtime is not installed, first try to install it using a non-sudo method (nvm for Node, rustup for Rust). If that fails, use sudo apt install or equivalent. Only as a last resort, inform the user what to install manually.
- Treat the user's system with the same caution you would treat a production server.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 8. COMMUNICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Be concise. State what you're doing, do it, report the result.
- Do not narrate your thought process at length. Show your work through actions and outcomes.
- When reporting completion, include evidence: "Server started on port 8000", "All 12 tests passed", "Build succeeded with 0 warnings".
- When reporting failure, include the full error context and your diagnosis — not just "something went wrong".
- Never claim success without evidence. "I've written the files" is not success. "The application starts and responds correctly" is success.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 9. SERVERS & LONG-RUNNING PROCESSES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Servers and dev-mode processes (API servers, frontend dev servers, database processes) run indefinitely. They require special handling because they never exit on their own.

### Starting servers
- Never use a blocking command to start a server — it will hang or time out waiting for a process that never finishes.
- Use the background process tools to launch servers. They start the process detached and return immediately.
- Always confirm the server is actually ready by waiting for its port to accept TCP connections before proceeding. A process starting is not the same as a server being ready to handle requests.

### Multi-service projects
When a project has multiple services (e.g. backend API + frontend):
1. Start the backend and wait for its port to become ready.
2. Verify the backend responds correctly (health check, root endpoint, etc.).
3. Start the frontend and wait for its port to become ready.
4. Verify the frontend loads and can communicate with the backend.
5. Only after all services are confirmed working should you continue.

### Diagnosing server failures
- If a server fails to start or behaves unexpectedly, read its log output.
- Common causes: port already in use, missing dependencies, syntax errors, wrong working directory.
- Check port availability before starting a server. Kill stale processes occupying the port if needed.

### Cleanup
- After verifying everything works, stop all background servers you started.
- Never leave orphaned server processes running when your task is complete.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 10. PROJECT COMPLETION & CLEANUP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

When all verification passes and the project is working:
1. Stop any running background servers.
2. Delete temporary or generated files that the user does not need:
   - Build artifacts, __pycache__, .pyc files, *.log, nohup.out, backend.log
   - Generated lock files ONLY if they are duplicates or stale
3. Do a final sanity review of the project tree — ensure it is clean and well-organized.
4. Write or update the README.md with complete setup and run instructions.
5. Present a final summary: what was built, how to run it, and any prerequisites."""


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
        
        # Wrap tools in ToolNode with error handling so validation errors
        # (e.g. missing required args) are returned to the LLM as messages
        # instead of crashing the entire agent execution.
        tool_node = ToolNode(self.tools, handle_tool_errors=True)
        
        self.agent = create_react_agent(
            self.llm_provider.get_llm(),
            tool_node,
            prompt=SYSTEM_PROMPT,
        )
        
        tool_count = len(self.tools)
        console.print(f"[dim]Agent ready • {tool_count} tools loaded[/dim]")
    
    def _ensure_initialized(self) -> None:
        """Lazy initialization of MCP + agent on first use."""
        if self.agent is None:
            asyncio.run(self._initialize_mcp_async())
            self._initialize_agent()
    
    @staticmethod
    def _clear_plan_state() -> None:
        """Clear stale plan/todo state files from previous runs.
        
        These temp files persist between agent runs and can cause
        the agent to pick up old plan state. Clear them at the start
        of a fresh conversation.
        """
        import tempfile
        state_files = [
            os.path.join(tempfile.gettempdir(), "codepilot_plan_state.json"),
            os.path.join(tempfile.gettempdir(), "codepilot_todo_state.json"),
        ]
        for f in state_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass  # Best-effort cleanup
    
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
            # Clear stale plan state when starting a fresh conversation
            # so old plans from previous runs don't interfere.
            if len(self.messages) == 0:
                self._clear_plan_state()
            
            user_message = HumanMessage(content=task)
            self.messages.append(user_message)
            
            self.renderer.reset()
            final_ai_content = ""
            
            # Circuit breaker: track consecutive failures of the same tool
            # to prevent infinite retry loops when the LLM keeps sending
            # the same broken arguments.
            _last_tool_error: Optional[str] = None   # tool_name
            _consecutive_failures: int = 0
            _MAX_CONSECUTIVE_FAILURES = 2
            _blocked_tools: set = set()  # tools permanently blocked this run
            
            # Tool groups — blocking one blocks all in the group
            _TOOL_GROUPS = {
                "install_runtime": {"install_runtime", "get_install_command"},
                "get_install_command": {"install_runtime", "get_install_command"},
            }
            
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
                            # Skip raw <tool_call> tokens emitted by weak models
                            if "<tool_call>" in content or "</tool_call>" in content:
                                continue
                            # Skip tokens that look like raw tool call JSON
                            if content.strip().startswith('{"name"') and '"arguments"' in content:
                                continue
                            self.renderer.on_thinking(content)
                
                # --- Tool start ---
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    tool_input = event.get("data", {}).get("input", {})
                    
                    # Circuit breaker: skip tools that have been permanently blocked
                    if tool_name in _blocked_tools:
                        self.renderer.flush_thinking()
                        console.print(
                            f"  [yellow dim]⊘ Skipping '{tool_name}' — blocked by circuit breaker[/yellow dim]",
                            highlight=False,
                        )
                        continue
                    
                    # Permission check for bash commands
                    if tool_name in ("run_command", "start_background_process"):
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
                            # If it's a sudo command and user approved,
                            # prompt for password and cache it for the
                            # MCP server subprocess.
                            if cmd.strip().startswith("sudo ") and not os.environ.get("CODEPILOT_SUDO_PW"):
                                import getpass
                                try:
                                    pw = getpass.getpass(
                                        prompt="\n  🔐 Password: "
                                    )
                                    os.environ["CODEPILOT_SUDO_PW"] = pw
                                except (EOFError, KeyboardInterrupt):
                                    console.print(f"  [yellow]⊘ Password entry cancelled[/yellow]\n")
                                    continue
                    
                    self.renderer.on_tool_start(tool_name, tool_input)
                
                # --- Tool end ---
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    output = event.get("data", {}).get("output", "")
                    output_str = _extract_tool_output(output)
                    
                    # Silently discard results from blocked tools — LangGraph
                    # already ran the tool (we can't prevent it), but we skip
                    # counting / rendering so the loop doesn't repeat.
                    if tool_name in _blocked_tools:
                        continue
                    
                    # Track repeated failures. When handle_tool_errors=True,
                    # validation errors come back as successful tool outputs
                    # containing the error text — not as on_tool_error events.
                    _output_lower = output_str.lower()
                    is_error_response = (
                        "validation error" in _output_lower
                        or "missing required" in _output_lower
                        or '{"ok": false' in _output_lower
                        or '{"ok":false' in _output_lower
                    )
                    
                    if is_error_response:
                        # Track by tool group — if install_runtime and
                        # get_install_command both fail, they count together.
                        tool_group = frozenset(_TOOL_GROUPS.get(tool_name, {tool_name}))
                        last_group = frozenset(_TOOL_GROUPS.get(_last_tool_error, {_last_tool_error})) if _last_tool_error else frozenset()
                        
                        if tool_group == last_group:
                            _consecutive_failures += 1
                        else:
                            _last_tool_error = tool_name
                            _consecutive_failures = 1
                        
                        if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                            # Block this tool AND its group permanently
                            tools_to_block = _TOOL_GROUPS.get(tool_name, {tool_name})
                            _blocked_tools.update(tools_to_block)
                            blocked_names = ", ".join(sorted(tools_to_block))
                            
                            self.renderer.flush_thinking()
                            console.print(
                                f"\n  [yellow bold]⚠ Tool '{tool_name}' failed "
                                f"{_consecutive_failures} times. "
                                f"Blocking: {blocked_names}[/yellow bold]",
                                highlight=False,
                            )
                            # Inject a correction so the LLM stops retrying
                            correction = ToolMessage(
                                content=(
                                    f"STOP: '{tool_name}' has failed {_consecutive_failures} "
                                    f"times. These tools are now BLOCKED and will not "
                                    f"execute: {blocked_names}. "
                                    f"Error: {output_str[:300]}. "
                                    f"To install a missing runtime, use the run_command "
                                    f"tool with sudo, e.g.: "
                                    f"run_command(command='sudo apt-get install -y nodejs npm'). "
                                    f"The user will be prompted for approval and password."
                                ),
                                tool_call_id="circuit_breaker",
                            )
                            self.messages.append(correction)
                    else:
                        # Successful tool call — reset circuit breaker
                        _consecutive_failures = 0
                        _last_tool_error = None
                    
                    self.renderer.on_tool_end(tool_name, output_str)
                
                # --- Tool error (handled gracefully) ---
                elif kind == "on_tool_error":
                    tool_name = event.get("name", "unknown")
                    error = event.get("data", {}).get("error", "")
                    error_str = str(error) if error else "Unknown tool error"
                    self.renderer.flush_thinking()
                    # Show as a clean error, not a crash
                    console.print(f"     [red]✗ {error_str[:200]}[/red]", highlight=False)
                
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
