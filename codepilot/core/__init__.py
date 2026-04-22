"""Package marker for core module.

Shared utilities used by both the new ADK agents and legacy code.
The legacy CodePilotAgent (LangGraph) lives in agent.py but is NOT
imported here to avoid circular imports.
"""

from .exceptions import CodePilotError, ConfigurationError, LLMError, SessionError
from .renderer import Renderer
from .session import SessionManager

__all__ = [
    "CodePilotError",
    "ConfigurationError",
    "LLMError",
    "SessionError",
    "Renderer",
    "SessionManager",
]
