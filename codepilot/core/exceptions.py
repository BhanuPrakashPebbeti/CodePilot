"""Custom exceptions for CodePilot."""


class CodePilotError(Exception):
    """Base exception for all CodePilot errors."""
    pass


class ConfigurationError(CodePilotError):
    """Configuration-related errors."""
    pass


class LLMError(CodePilotError):
    """LLM provider errors."""
    pass


class APIKeyError(LLMError):
    """API key validation or rotation errors."""
    pass


class SessionError(CodePilotError):
    """Session management errors."""
    pass


class MCPError(CodePilotError):
    """MCP server errors."""
    pass
