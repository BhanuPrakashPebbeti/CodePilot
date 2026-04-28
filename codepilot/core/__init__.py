"""Core utilities — shared across CLI, agents, and config."""

from .exceptions import CodePilotError, ConfigurationError, LLMError, SessionError
from .global_memory import GlobalMemory
from .renderer import Renderer
from .session import SessionStore, SessionManager   # SessionManager is an alias for SessionStore

__all__ = [
    "CodePilotError",
    "ConfigurationError",
    "LLMError",
    "SessionError",
    "GlobalMemory",
    "Renderer",
    "SessionStore",
    "SessionManager",
]
