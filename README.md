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

**Autonomous AI coding assistant powered by Google ADK multi-agent architecture — it plans, codes, runs, tests in a real browser, debugs, and delivers working software.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Google ADK](https://img.shields.io/badge/Google%20ADK-1.0+-orange.svg)](https://google.github.io/adk-docs/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## ✨ What is CodePilot?

CodePilot is a **terminal-based autonomous AI software engineer** that uses a team of specialized agents — not a single monolithic loop — to deliver working software. It follows a deterministic pipeline: **Plan → Develop → Run → Browser Test → Debug → Finalize**, powered by [Google ADK](https://google.github.io/adk-docs/) (Agent Development Kit).

Unlike simple code generators, CodePilot:
- 🧠 **Plans before coding** with a dedicated Planner Agent that creates structured task lists
- 🔨 **Writes production code** via a Developer Agent with filesystem, bash, and git tools
- 🚀 **Starts and health-checks services** through a Runtime Agent
- 🌐 **Tests in a real browser** using Playwright MCP — verifies actual user-facing behavior
- 🔧 **Self-corrects in a loop** — the Debug Agent analyzes failures and applies targeted fixes
- 📦 **Finalizes delivery** — cleanup, README generation, git commits

## 🏗️ Architecture

CodePilot v2 is built on a **multi-agent pipeline** using Google ADK's `SequentialAgent` and `LoopAgent`:

```
┌─────────────────────────────────────────────────────────────────┐
│                     CodePilot Pipeline                          │
│                   (ADK SequentialAgent)                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐                                               │
│   │   Planner    │  Decomposes task → structured plan            │
│   │   Agent      │  Tools: planning, workspace, environment     │
│   └──────┬──────┘                                               │
│          ▼                                                      │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │            Development Loop (LoopAgent)                  │   │
│   │                max_iterations = 8                        │   │
│   │                                                         │   │
│   │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐│   │
│   │   │Developer │→ │ Runtime  │→ │ Browser  │→ │ Debug  ││   │
│   │   │  Agent   │  │  Agent   │  │  Test    │  │ Agent  ││   │
│   │   │          │  │          │  │  Agent   │  │        ││   │
│   │   │Write code│  │Start svc │  │Playwright│  │Fix or  ││   │
│   │   │Install   │  │Health    │  │real test │  │exit    ││   │
│   │   │deps      │  │check     │  │          │  │loop    ││   │
│   │   └──────────┘  └──────────┘  └──────────┘  └────────┘│   │
│   │                      ↻ repeat until pass                │   │
│   └─────────────────────────────────────────────────────────┘   │
│          ▼                                                      │
│   ┌─────────────┐                                               │
│   │  Finalizer   │  Cleanup, README, git commit, summary        │
│   │   Agent      │  Tools: bash, filesystem, git                │
│   └─────────────┘                                               │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                     MCP Servers (Tools)                         │
│                                                                 │
│  ┌──────────┐ ┌──────┐ ┌─────┐ ┌────────┐ ┌──────────────────┐│
│  │Filesystem│ │ Bash │ │ Git │ │Planning│ │   Playwright     ││
│  └──────────┘ └──────┘ └─────┘ └────────┘ │  (Browser MCP)   ││
│  ┌──────────┐ ┌──────┐ ┌──────┐┌────────┐ └──────────────────┘│
│  │Workspace │ │Debug │ │GitHub││Testing │                      │
│  └──────────┘ └──────┘ └──────┘└────────┘                      │
│  ┌───────────┐                                                  │
│  │Environment│                                                  │
│  └───────────┘                                                  │
├─────────────────────────────────────────────────────────────────┤
│                      LLM Providers                              │
│          Gemini (native) │ Ollama │ OpenRouter                  │
│               (via ADK + LiteLLM routing)                       │
└─────────────────────────────────────────────────────────────────┘
```

### How it works

1. **PlannerAgent** reads your request, explores the project with workspace tools, and creates a numbered task plan
2. **DevelopmentLoop** runs up to 8 iterations of:
   - **DeveloperAgent** writes code, installs dependencies, marks tasks complete
   - **RuntimeAgent** starts servers, waits for ports, does health checks
   - **BrowserTestAgent** opens a real browser via Playwright MCP and tests actual behavior
   - **DebugAgent** analyzes any failures and applies targeted fixes — or calls `exit_loop` on success
3. **FinalizerAgent** stops servers, writes README, makes a git commit, and prints a summary

State flows between agents via `output_key` — no shared conversation history, no context window bloat.

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for Playwright MCP browser automation)
- **Ollama** (for local LLM) *or* **OpenRouter API key** *or* **Google Gemini API key**

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
codepilot config init

# Start an interactive coding session
codepilot run

# Or give it a direct task
codepilot run "create a REST API with Flask that manages a todo list"
```

## 📖 Usage

### Commands

| Command | Description |
|---------|-------------|
| `codepilot run` | Start an interactive coding session |
| `codepilot run "task"` | Execute a specific task autonomously |
| `codepilot run -p ./myproject` | Work in a specific project directory |
| `codepilot config init` | Initialize or update configuration |
| `codepilot config show` | Display current configuration |
| `codepilot config reset` | Reset configuration to defaults |
| `codepilot sessions` | List past coding sessions |
| `codepilot version` | Show version |

### Examples

```bash
# Build a full-stack application
codepilot run "build a React + Express todo app with MongoDB"

# Work on an existing project
codepilot run "add authentication to this Flask app" -p ./my-flask-app

# Debug and fix issues
codepilot run "fix the failing tests in this project"

# Interactive mode — chat back and forth
codepilot run
> Build me a REST API for a bookstore
> Add unit tests for the endpoints
> Deploy it with Docker
```

## 🔧 LLM Providers

CodePilot supports three LLM providers via Google ADK + LiteLLM:

### Gemini (Native ADK)

Best integration — uses ADK's native Google Gemini support.

```bash
# Set GOOGLE_API_KEY environment variable
export GOOGLE_API_KEY=your-key-here
```

### Ollama (Local & Free)

Run models locally with zero API costs. Requires [Ollama](https://ollama.ai) installed and running.

```bash
ollama pull mistral    # Recommended — good tool-calling support
```

### OpenRouter (Cloud)

Access 100+ models (GPT-4, Claude, Gemini, etc.) via a single API key from [OpenRouter](https://openrouter.ai).

## 🧩 MCP Servers

CodePilot's capabilities come from specialized **Model Context Protocol (MCP)** servers. Each agent only gets the tools it needs — no agent has access to everything.

| Server | Tools | Used By |
|--------|-------|---------|
| **Planning** | `create_plan`, `get_current_task`, `complete_task`, etc. | Planner, Developer |
| **Filesystem** | `read_file`, `write_file`, `edit_lines`, `find_files`, etc. | Developer, Debug, Finalizer |
| **Bash** | `run_command`, `start_background_process`, `wait_for_port`, etc. | Developer, Runtime, Debug |
| **Workspace** | `detect_project`, `get_project_tree`, `search_codebase`, etc. | Planner, Developer |
| **Testing** | `run_tests`, `http_request`, `check_syntax`, `lint_code`, etc. | Runtime, Browser |
| **Debug** | `parse_error`, `find_errors_in_output`, `read_log_tail` | Debug |
| **Git** | `git_init`, `git_commit`, `git_branch`, `git_diff`, etc. | Developer, Finalizer |
| **GitHub** | `create_repo`, `push_to_github`, `open_pull_request`, etc. | Developer (optional) |
| **Environment** | `detect_runtimes`, `create_venv`, `check_runtime`, etc. | Planner, Developer |
| **Playwright** | `playwright_navigate`, `playwright_click`, `playwright_fill`, etc. | Browser Test |

## 🌐 Browser Testing with Playwright

The BrowserTestAgent uses the `@playwright/mcp` server to perform **real behavioral testing**:

- Navigates to running applications
- Clicks buttons, fills forms, submits data
- Verifies UI elements are visible and functional
- Catches JavaScript console errors
- Tests end-to-end user flows

This ensures CodePilot never declares success without proof — every web application is tested in a real browser before delivery.

## 📁 Project Structure

```
codepilot/
├── __init__.py              # Package metadata (v2.0.0)
├── __main__.py              # Entry point for `python -m codepilot`
├── cli.py                   # Typer-based CLI with Rich terminal UI
├── agents/                  # Google ADK multi-agent pipeline
│   ├── __init__.py          # Package exports
│   ├── builder.py           # Builds the SequentialAgent/LoopAgent hierarchy
│   ├── runner.py            # ADK Runner wrapper with Rich rendering
│   ├── prompts.py           # Agent instruction prompts (6 agents)
│   ├── tools.py             # exit_loop tool + increment_iteration callback
│   └── mcp_config.py        # MCP toolset factories + agent-specific bundles
├── config/
│   ├── keys.py              # API key rotation and management
│   ├── manager.py           # Configuration persistence (JSON)
│   └── models.py            # Pydantic config models
├── core/
│   ├── exceptions.py        # Custom exception hierarchy
│   ├── renderer.py          # Rich terminal output (Claude Code style)
│   └── session.py           # Session tracking and persistence
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

Configuration is stored at `~/.codepilot/config.json`:

```bash
codepilot config init     # Interactive setup
codepilot config show     # View current config
codepilot config reset    # Reset everything
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `provider` | LLM provider (`ollama`, `openrouter`, `gemini`) | `ollama` |
| `model` | Model name | `mistral` |
| `temperature` | Sampling temperature (0.0 – 2.0) | `0.7` |
| `max_tokens` | Maximum response tokens | `8192` |
| `api_key` | OpenRouter / Gemini API key | — |
| `github.token` | GitHub personal access token (optional) | — |

## 🤝 Contributing

```bash
git clone https://github.com/bhanuprakash1212/CodePilot.git
cd CodePilot
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

pytest                    # Run tests
black codepilot/          # Format
ruff check codepilot/     # Lint
```

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Google ADK](https://google.github.io/adk-docs/) — Multi-agent orchestration framework
- [FastMCP](https://github.com/jlowin/fastmcp) — Model Context Protocol server framework
- [Playwright MCP](https://github.com/nicklitvin/playwright-mcp) — Browser automation via MCP
- [LiteLLM](https://github.com/BerriAI/litellm) — Universal LLM API routing
- [Rich](https://github.com/Textualize/rich) — Beautiful terminal rendering
- [Typer](https://github.com/tiangolo/typer) — CLI framework
- [Ollama](https://ollama.ai) — Local LLM runtime
- [OpenRouter](https://openrouter.ai) — Multi-model API gateway

---

<div align="center">

**Built with ❤️ by [Bhanu Prakash](https://github.com/bhanuprakash1212)**

*CodePilot doesn't explain how to build software. It builds it — and tests it in a real browser.*

</div>
