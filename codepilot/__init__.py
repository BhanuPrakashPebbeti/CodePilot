"""CodePilot - Autonomous AI coding assistant powered by Google ADK."""

__version__ = "2.0.0"
__author__ = "Bhanu"

from .agents import CodePilotRunner, create_codepilot_runner
from .core.exceptions import CodePilotError, ConfigurationError, LLMError

__all__ = [
    "CodePilotRunner",
    "create_codepilot_runner",
    "CodePilotError",
    "ConfigurationError",
    "LLMError",
    "__version__",
]
