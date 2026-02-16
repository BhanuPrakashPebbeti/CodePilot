<div align="center">

```
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
                              ╚═══════════════════════════════════════════════════════════════╝
```

**Production-ready autonomous AI coding assistant that doesn't just write code — it builds, tests, and delivers working software.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## ✨ What is CodePilot?

CodePilot is a **terminal-based autonomous AI software engineer** that follows a disciplined workflow — **Understand → Plan → Execute → Verify → Fix** — to deliver working software, not just code snippets. It operates like a senior developer: it explores your project, creates a plan, writes code, installs dependencies, runs tests, and fixes issues — all autonomously.

Unlike simple code generators, CodePilot:
- 🔍 **Understands your project** before making changes (detects frameworks, dependencies, structure)
- 📋 **Plans before coding** with a structured, trackable todo system
- 🔨 **Executes the full lifecycle** — scaffolding, file creation, dependency management, configuration
- 🧪 **Verifies everything works** — runs tests, checks syntax, validates output
- 🔧 **Self-corrects on failure** — diagnoses errors and retries with fixes

## 🏗️ Architecture

CodePilot is built on a modular architecture with **LLM providers** for intelligence and **MCP (Model Context Protocol) servers** for capabilities:

```
┌─────────────────────────────────────────────────────────┐
│                    CodePilot CLI                        │
│                   (Rich Terminal UI)                    │
├─────────────────────────────────────────────────────────┤
│                  Core Agent Engine                      │
│          (LangGraph ReAct Agent Loop)                   │
│    Plan → Execute → Verify → Fix workflow               │
├──────────────┬──────────────────────────────────────────┤
│  LLM Layer   │           MCP Servers                    │
│              │                                          │
│ ┌──────────┐ │  ┌────────────┐  ┌───────────────────┐  │
│ │OpenRouter│ │  │ Filesystem │  │   Bash/Execution   │  │
│ └──────────┘ │  └────────────┘  └───────────────────┘  │
│ ┌──────────┐ │  ┌────────────┐  ┌───────────────────┐  │
│ │  Ollama  │ │  │    Git     │  │     Planning      │  │
│ └──────────┘ │  └────────────┘  └───────────────────┘  │
│              │  ┌────────────┐  ┌───────────────────┐  │
│              │  │   GitHub   │  │     Testing       │  │
│              │  └────────────┘  └───────────────────┘  │
│              │  ┌────────────┐  ┌───────────────────┐  │
│              │  │ Workspace  │  │   Environment     │  │
│              │  └────────────┘  └───────────────────┘  │
│              │  ┌────────────┐                         │
│              │  │   Debug    │                         │
│              │  └────────────┘                         │
├──────────────┴──────────────────────────────────────────┤
│              Security & Permissions                     │
│     (SAFE / NEEDS_PERMISSION / BLOCKED tiers)          │
├─────────────────────────────────────────────────────────┤
│              Memory & Context Management                │
│    (Token estimation, sliding window, summarization)    │
└─────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Ollama** (for local LLM) *or* an **OpenRouter API key** (for cloud models)

### Installation

```bash
# Clone the repository
git clone https://github.com/bhanuprakash1212/CodePilot.git
cd CodePilot

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install in development mode
pip install -e .
```

### First Run

```bash
# Interactive configuration (choose provider, model, API keys)
codepilot config

# Start an interactive coding session
codepilot run

# Or give it a direct task
codepilot run "create a REST API with Flask that manages a todo list"
```

On first run, CodePilot will walk you through an interactive setup to configure your LLM provider and model.

## 📖 Usage

### Commands

| Command | Description |
|---------|-------------|
| `codepilot run` | Start an interactive coding session |
| `codepilot run "task"` | Execute a specific task autonomously |
| `codepilot run -p ./myproject` | Work in a specific project directory |
| `codepilot config` | View or modify configuration |
| `codepilot config --edit` | Edit configuration interactively |
| `codepilot config --show` | Display current configuration |
| `codepilot config --reset` | Reset configuration to defaults |
| `codepilot sessions` | List past coding sessions |
| `codepilot --help` | Show help and available commands |

### Examples

```bash
# Build a full-stack application
codepilot run "build a React + Express todo app with MongoDB"

# Work on an existing project
codepilot run "add authentication to this Flask app" -p ./my-flask-app

# Debug and fix issues
codepilot run "fix the failing tests in this project"

# Scaffold a new project
codepilot run "create a Python CLI tool with Click that converts CSV to JSON"

# Interactive mode — chat back and forth
codepilot run
> Build me a REST API for a bookstore
> Add unit tests for the endpoints
> Deploy it with Docker
```

## 🔧 LLM Providers

### Ollama (Local & Free)

Run models locally with zero API costs. Requires [Ollama](https://ollama.ai) installed and running.

```bash
# Install Ollama, then pull a model
ollama pull mistral    # Recommended — good tool-calling support
ollama pull llama2
ollama pull codellama
```

### OpenRouter (Cloud)

Access 100+ models (GPT-4, Claude, Gemini, etc.) via a single API key from [OpenRouter](https://openrouter.ai).

CodePilot supports **multiple API keys** with automatic rotation for load balancing.

## 🛡️ Security & Permissions

CodePilot has a built-in **3-tier permission system** that classifies every command before execution:

| Tier | Description | Examples |
|------|-------------|----------|
| ✅ **SAFE** | Project-level operations — auto-approved | `pip install`, `npm run build`, `python`, `pytest` |
| ⚠️ **NEEDS_PERMISSION** | System-level changes — asks for approval | `sudo apt install`, `docker run`, port binding |
| 🚫 **BLOCKED** | Destructive operations — always rejected | `rm -rf /`, disk formatting, credential exfiltration |

When a command needs permission, CodePilot shows a clear prompt with the exact command and lets you approve, deny, or allow for the rest of the session.

## 🧩 MCP Servers

CodePilot's capabilities come from specialized **Model Context Protocol (MCP)** servers, each providing focused tools:

| Server | Capabilities |
|--------|-------------|
| **Filesystem** | Read, write, search, move, and manage files and directories |
| **Bash** | Execute shell commands, run Python scripts, manage background processes |
| **Git** | Commit, branch, diff, log, stash — full local git workflow |
| **GitHub** | Create repos, manage issues/PRs, push code via GitHub API |
| **Planning** | Todo-driven task management — create plans, track progress |
| **Testing** | Auto-detect test frameworks, run tests, lint code, validate APIs |
| **Workspace** | Detect project type, frameworks, dependencies, structure analysis |
| **Environment** | Detect runtimes, manage versions, resolve dependencies |
| **Debug** | Parse error messages, read logs, diagnose failures |

## 🧠 Memory & Context Management

CodePilot intelligently manages its context window to handle long coding sessions:

- **Token estimation** — Tracks approximate token usage per message
- **Sliding window** — Prunes old messages while preserving system context
- **Smart summarization** — Uses the LLM to summarize tool outputs and old conversation turns instead of blindly truncating
- **Output truncation** — Large file reads and command outputs are intelligently trimmed

## 📁 Project Structure

```
codepilot/
├── __init__.py              # Package metadata and exports
├── __main__.py              # Entry point for `python -m codepilot`
├── cli.py                   # Typer-based CLI with Rich terminal UI
├── config/
│   ├── keys.py              # API key rotation and management
│   ├── manager.py           # Configuration persistence (JSON)
│   └── models.py            # Pydantic config models
├── core/
│   ├── agent.py             # Main ReAct agent loop (LangGraph)
│   ├── exceptions.py        # Custom exception hierarchy
│   ├── memory.py            # Token counting & context window management
│   ├── permissions.py       # 3-tier command security gate
│   ├── renderer.py          # Rich terminal output (Claude Code style)
│   ├── session.py           # Session tracking and persistence
│   └── summarizer.py        # LLM-based intelligent summarization
├── llm/
│   ├── provider.py          # Abstract LLM provider interface
│   ├── ollama.py            # Ollama local LLM provider
│   └── openrouter.py        # OpenRouter cloud LLM provider
├── mcp/
│   └── servers/
│       ├── bash_server.py         # Shell execution & process management
│       ├── debug_server.py        # Error analysis & log reading
│       ├── environment_server.py  # Runtime detection & version management
│       ├── filesystem_server.py   # File operations (CRUD, search, tree)
│       ├── git_server.py          # Local git operations
│       ├── github_server.py       # GitHub API integration
│       ├── planning_server.py     # Todo-driven task planning
│       ├── testing_server.py      # Test running & code validation
│       └── workspace_server.py    # Project detection & analysis
└── utils/
    ├── constants.py          # App-wide constants and defaults
    └── logger.py             # Structured logging setup
```

## ⚙️ Configuration

Configuration is stored at `~/.codepilot/config.json` and managed through the CLI:

```bash
# Interactive setup
codepilot config

# View current config
codepilot config --show

# Reset everything
codepilot config --reset
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `provider` | LLM provider (`ollama` or `openrouter`) | `ollama` |
| `model` | Model name | `mistral` |
| `temperature` | Sampling temperature (0.0 – 2.0) | `0.7` |
| `max_tokens` | Maximum response tokens | `8192` |
| `api_key` | OpenRouter API key | — |
| `github.token` | GitHub personal access token | — |

## 🤝 Contributing

Contributions are welcome! Here's how to get started:

```bash
# Clone and set up
git clone https://github.com/bhanuprakash1212/CodePilot.git
cd CodePilot
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black codepilot/

# Lint
ruff check codepilot/
```

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [LangChain](https://github.com/langchain-ai/langchain) & [LangGraph](https://github.com/langchain-ai/langgraph) — Agent framework
- [FastMCP](https://github.com/jlowin/fastmcp) — Model Context Protocol server framework
- [Rich](https://github.com/Textualize/rich) — Beautiful terminal rendering
- [Typer](https://github.com/tiangolo/typer) — CLI framework
- [Ollama](https://ollama.ai) — Local LLM runtime
- [OpenRouter](https://openrouter.ai) — Multi-model API gateway

---

<div align="center">

**Built with ❤️ by [Bhanu Prakash](https://github.com/bhanuprakash1212)**

*CodePilot doesn't explain how to build software. It builds it.*

</div>
