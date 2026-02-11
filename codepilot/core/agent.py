"""Core agent implementation - clean, no global state."""

import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from langchain_core.messages import SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from .exceptions import ConfigurationError, LLMError, MCPError
from .session import SessionManager
from ..config import ConfigManager
from ..llm import OllamaProvider, OpenRouterProvider, LLMProvider
from ..utils.constants import PROVIDER_OLLAMA, PROVIDER_OPENROUTER
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Suppress FastMCP banner
logging.getLogger("fastmcp").setLevel(logging.WARNING)

class CodePilotAgent:
    """Main autonomous coding agent."""
    
    def __init__(
        self,
        config_manager: ConfigManager,
        project_dir: str = ".",
        session_manager: Optional[SessionManager] = None,
    ):
        """Initialize agent.
        
        Args:
            config_manager: Configuration manager instance.
            project_dir: Project directory to work in.
            session_manager: Optional session manager.
        """
        self.config_manager = config_manager
        self.project_dir = project_dir
        self.session_manager = session_manager or SessionManager(project_dir)
        
        # Initialize components
        self.llm_provider: Optional[LLMProvider] = None
        self.mcp_client: Optional[MultiServerMCPClient] = None
        self.tools: List = []
        self.agent = None
        self.messages: List = []  # Message history for conversation
        
        # Initialize LLM
        self._initialize_llm()
    
    def _select_provider(self, llm_config) -> str:
        """Smart provider selection based on preference and availability.
        
        Priority:
        1. Use provider_preference if set
        2. Use OpenRouter if API key is available
        3. Fall back to Ollama
        
        Args:
            llm_config: LLM configuration
            
        Returns:
            Selected provider name
        """
        # If user has set a preference, use it
        if llm_config.provider_preference:
            logger.debug(f"Using preferred provider: {llm_config.provider_preference}")
            return llm_config.provider_preference
        
        # Smart auto-selection: OpenRouter if key available, else Ollama
        if llm_config.has_any_key:
            logger.debug("Auto-selecting OpenRouter (API key available)")
            return PROVIDER_OPENROUTER
        else:
            logger.debug("Auto-selecting Ollama (no API key found)")
            return PROVIDER_OLLAMA
    
    def _initialize_llm(self) -> None:
        """Initialize LLM provider based on configuration."""
        try:
            config = self.config_manager.config
            llm_config = config.llm
            
            # Smart provider selection based on preference and availability
            provider_type = self._select_provider(llm_config)
            
            logger.debug(f"Initializing LLM: {provider_type}/{llm_config.model}")
            
            if provider_type == PROVIDER_OPENROUTER:
                # Get API key (with rotation support)
                try:
                    api_key = self.config_manager.get_api_key()
                except ConfigurationError:
                    # Try environment variable as fallback
                    api_key = os.getenv("OPENROUTER_API_KEY")
                    if not api_key:
                        raise ConfigurationError(
                            "No OpenRouter API key found. "
                            "Set with: codepilot config set-key YOUR_KEY"
                        )
                
                self.llm_provider = OpenRouterProvider(
                    api_key=api_key,
                    model=llm_config.model,
                    temperature=llm_config.temperature,
                    max_tokens=llm_config.max_tokens,
                )
            
            elif provider_type == PROVIDER_OLLAMA:
                self.llm_provider = OllamaProvider(
                    model=llm_config.model,
                    base_url=llm_config.base_url or "http://localhost:11434",
                    temperature=llm_config.temperature,
                    max_tokens=llm_config.max_tokens,
                )
            
            else:
                raise ConfigurationError(f"Unknown provider: {provider_type}")
            
            logger.info(f"✅ LLM initialized: {self.llm_provider.get_model_name()}")
        
        except Exception as e:
            logger.error(f"Failed to initialize LLM: {e}")
            raise LLMError(f"LLM initialization failed: {e}")
    
    async def _initialize_mcp_async(self) -> None:
        """Initialize MCP servers and tools."""
        try:
            mcp_config = self._get_mcp_config()
            
            if not mcp_config:
                logger.warning("No MCP servers configured")
                return
            
            logger.debug(f"Initializing MCP servers: {list(mcp_config.keys())}")
            
            self.mcp_client = MultiServerMCPClient(mcp_config)
            self.tools = await self.mcp_client.get_tools()
            
            logger.info(f"✅ Loaded {len(self.tools)} MCP tools")
            
            # Log tool names for debugging
            if self.tools:
                tool_names = [tool.name for tool in self.tools if hasattr(tool, 'name')]
                logger.debug(f"Available tools: {', '.join(tool_names[:10])}...")
        
        except Exception as e:
            logger.warning(f"MCP initialization failed: {e}")
            # Continue without MCP tools
            self.tools = []
    
    def _get_mcp_config(self) -> Dict[str, Any]:
        """Get MCP server configuration.
        
        Returns:
            MCP configuration dictionary.
        """
        
        config = {
            "filesystem": {
                "command": sys.executable,
                "args": ["-m", "codepilot.mcp.servers.filesystem_server"],
                "transport": "stdio"
            },
            "bash": {
                "command": sys.executable,
                "args": ["-m", "codepilot.mcp.servers.bash_server"],
                "transport": "stdio"
            },
            "code_analysis": {
                "command": sys.executable,
                "args": ["-m", "codepilot.mcp.servers.code_analysis_server"],
                "transport": "stdio"
            },
            "webdev": {
                "command": sys.executable,
                "args": ["-m", "codepilot.mcp.servers.webdev_server"],
                "transport": "stdio"
            },
            "todo": {
                "command": sys.executable,
                "args": ["-m", "codepilot.mcp.servers.todo_server"],
                "transport": "stdio"
            },
        }
        
        # Optional servers
        try:
            config["git"] = {
                "command": sys.executable,
                "args": ["-m", "codepilot.mcp.servers.git_server"],
                "transport": "stdio"
            }
        except ImportError:
            logger.debug("GitPython not available")
        
        if self.config_manager.config.github.token:
            config["github"] = {
                "command": sys.executable,
                "args": ["-m", "codepilot.mcp.servers.github_server"],
                "transport": "stdio"
            }
        
        return config
    
    def _initialize_agent(self) -> None:
        """Initialize LangGraph ReAct agent."""
        if not self.llm_provider:
            raise LLMError("LLM provider not initialized")
        
        # Check tool support
        if not self.llm_provider.supports_tools():
            logger.warning(
                f"⚠️  Model {self.llm_provider.get_model_name()} may not support tools properly"
            )
            logger.warning("Consider using a model that supports function calling for best results")
        
        system_prompt = """You are CodePilot, an autonomous coding assistant that EXECUTES actions using tools.

CRITICAL TOOL USAGE RULES:
1. To create files with content, use: write_file(path="file.py", content="code here")
2. To create empty files, use: create_file(path="file.txt")  
3. To run commands, use: execute_command(command="npm install")
4. To create directories, use: create_directory(path="folder/")
5. To read files, use: read_file(path="file.py")

WORKFLOW FOR BUILDING PROJECTS:
1. Create project structure (create_directory for folders)
2. Write all code files (write_file with full content)
3. Create config files (package.json, requirements.txt, etc.)
4. Run setup commands (npm install, pip install, etc.)
5. Test by running the application
6. Debug and iterate if errors occur

EXAMPLE: Creating a React app with FastAPI backend:
1. create_directory(path="calculator-app")
2. create_directory(path="calculator-app/frontend")
3. create_directory(path="calculator-app/backend")
4. write_file(path="calculator-app/frontend/package.json", content="{...}")
5. write_file(path="calculator-app/frontend/src/App.js", content="React code...")
6. write_file(path="calculator-app/backend/main.py", content="FastAPI code...")
7. write_file(path="calculator-app/backend/requirements.txt", content="fastapi\\nuvicorn")
8. execute_command(command="cd calculator-app/backend && pip install -r requirements.txt")

Work systematically. Test. Iterate. Build complete, working projects."""
        
        # Create agent with LLM and tools
        self.agent = create_react_agent(
            self.llm_provider.get_llm(),
            self.tools,
            prompt=system_prompt,
        )
        
        logger.info(f"✅ Agent ready with {len(self.tools)} tools")
    
    def run(self, task: str) -> str:
        """Execute a single task.
        
        Args:
            task: Task description to execute.
            
        Returns:
            Task result as string.
        """
        # Lazy initialization of MCP and agent
        if self.agent is None:
            asyncio.run(self._initialize_mcp_async())
            self._initialize_agent()
        
        try:
            # Start session if not started
            if not self.session_manager.session:
                self.session_manager.start_session()
            
            # Add task to session
            session_task = self.session_manager.add_task(task)
            session_task.status = "in_progress"
            session_task.started_at = __import__('datetime').datetime.now()
            self.session_manager._save()
            
            # Execute task
            result = asyncio.run(self._execute_task(task))
            
            # Mark task as completed
            session_task.status = "completed"
            session_task.completed_at = __import__('datetime').datetime.now()
            self.session_manager._save()
            
            return result
        
        except Exception as e:
            logger.error(f"Task execution failed: {e}")
            # Mark task as failed
            if 'session_task' in locals():
                session_task.status = "failed"
                session_task.error = str(e)
                self.session_manager._save()
            raise
    
    async def _execute_task(self, task: str) -> str:
        """Execute task with agent (async).
        
        Args:
            task: Task to execute.
            
        Returns:
            Result string.
        """
        try:
            from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
            
            # Add user message to history
            user_message = HumanMessage(content=task)
            self.messages.append(user_message)
            
            # Stream agent execution to show progress
            print()
            async for event in self.agent.astream_events(
                {"messages": self.messages},
                version="v2"
            ):
                kind = event.get("event")
                
                # Show tool calls being made
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "tool_calls") and chunk.tool_calls:
                        for tool_call in chunk.tool_calls:
                            if tool_call.get("name"):
                                print(f"🔧 Using tool: {tool_call['name']}")
                
                # Show tool execution
                if kind == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    print(f"⚙️  Executing: {tool_name}")
                
                # Show tool results
                if kind == "on_tool_end":
                    output = event.get("data", {}).get("output", "")
                    if output and len(str(output)) < 200:
                        print(f"✓ Result: {str(output)[:200]}")
            
            # Get final result
            response = await self.agent.ainvoke({"messages": self.messages})
            
            # Update message history with response
            if response and "messages" in response:
                self.messages = response["messages"]
                
                # Extract the final answer
                final_messages = response["messages"]
                for msg in reversed(final_messages):
                    if isinstance(msg, AIMessage) and msg.content:
                        return msg.content
                
                return "Task completed"
            
            return "Task completed"
        
        except Exception as e:
            logger.error(f"Agent execution error: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            raise LLMError(f"Execution failed: {e}")
    
    def run_interactive(self) -> None:
        """Run interactive session."""
        # Lazy initialization
        if self.agent is None:
            asyncio.run(self._initialize_mcp_async())
            self._initialize_agent()
        
        print("\nCodePilot Interactive Session")
        print("Type 'quit' to exit, 'help' for commands")
        print()
        
        while True:
            try:
                # Get user input
                user_input = input("codepilot> ").strip()
                
                if not user_input:
                    continue
                
                # Handle special commands
                if user_input.lower() in ["quit", "exit", "q"]:
                    print("Goodbye!")
                    break
                
                if user_input.lower() == "help":
                    self._show_help()
                    continue
                
                if user_input.lower() == "clear":
                    self.messages = []
                    print("Message history cleared")
                    continue
                
                if user_input.lower() == "history":
                    self._show_history()
                    continue
                
                # Execute task
                print()
                result = self.run(user_input)
                print(f"\n{result}\n")
            
            except KeyboardInterrupt:
                print("\n\nInterrupted. Type 'quit' to exit.")
                continue
            
            except Exception as e:
                logger.error(f"Error: {e}")
                print(f"\n[Error] {e}\n")
    
    def _show_help(self) -> None:
        """Show help message."""
        print("""
Available commands:
  quit, exit, q  - Exit interactive session
  help           - Show this help message
  clear          - Clear session history
  history        - Show conversation history
  
Or enter any task to execute.
""")
    
    def _show_history(self) -> None:
        """Show conversation history."""
        if not self.messages:
            print("No history yet")
            return
        
        print("\nConversation History:")
        print("-" * 50)
        
        for msg in self.messages:
            role = msg.type if hasattr(msg, 'type') else 'unknown'
            content = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
            print(f"[{role}] {content}")
        
        print("-" * 50)


def create_agent(
    config_manager: ConfigManager,
    project_dir: str = ".",
    session_manager: Optional[SessionManager] = None,
) -> CodePilotAgent:
    """Factory function to create agent instance.
    
    Args:
        config_manager: Configuration manager.
        project_dir: Project directory.
        session_manager: Optional session manager.
        
    Returns:
        Initialized CodePilotAgent instance.
    """
    return CodePilotAgent(
        config_manager=config_manager,
        project_dir=project_dir,
        session_manager=session_manager,
    )
