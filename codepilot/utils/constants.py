"""Constants for CodePilot."""

from pathlib import Path

# Directories
CONFIG_DIR = Path.home() / ".codepilot"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_DIR = CONFIG_DIR / "sessions"
LOGS_DIR = CONFIG_DIR / "logs"

# Default values
DEFAULT_WORK_DIR = Path.home() / "codepilot_projects"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_MAX_TOKENS = 8192  # Balanced for complex tasks

# Supported LLM providers
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OLLAMA = "ollama"

# OpenRouter
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "https://github.com/yourusername/codepilot"
OPENROUTER_TITLE = "CodePilot"

# Ollama
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "mistral"

# CLI
APP_NAME = "CodePilot"
APP_TAGLINE = "Production-ready AI coding assistant"

# Banner with ASCII art pacman
BANNER = """
[bold cyan]
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║     ⣀⣤⣤⣀          ██████╗ ██████╗ ██████╗ ███████╗           ║
║   ⣴⣿⣿⣿⣿⣦        ██╔════╝██╔═══██╗██╔══██╗██╔════╝           ║
║  ⣿⣿⣿⣿⣿⣿⣿       ██║     ██║   ██║██║  ██║█████╗             ║
║  ⣿⣿⣿⣿⣿⣿⣿       ██║     ██║   ██║██║  ██║██╔══╝             ║
║   ⠻⣿⣿⣿⠟        ╚██████╗╚██████╔╝██████╔╝███████╗           ║
║     ⠉⠉⠉           ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝           ║
║                                                               ║
║   ███████╗██╗██╗      ██████╗ ████████╗                      ║
║   ██╔══██╗██║██║     ██╔═══██╗╚══██╔══╝                      ║
║   ██████╔╝██║██║     ██║   ██║   ██║                         ║
║   ██╔═══╝ ██║██║     ██║   ██║   ██║                         ║
║   ██║     ██║███████╗╚██████╔╝   ██║                         ║
║   ╚═╝     ╚═╝╚══════╝ ╚═════╝    ╚═╝                         ║
║                                                               ║
║                 🤖 AI Software Architect                      ║
║                      Version 1.0.0                            ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝[/bold cyan]
"""
