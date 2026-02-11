"""CodePilot - Production-ready AI coding assistant."""

__version__ = "1.0.0"
__author__ = "Bhanu"

from .core.agent import CodePilotAgent
from .core.exceptions import CodePilotError, ConfigurationError, LLMError

__all__ = [
    "CodePilotAgent",
    "CodePilotError",
    "ConfigurationError",
    "LLMError",
    "__version__",
]
