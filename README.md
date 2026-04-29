<div align="center">

```
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   ██████╗ ██████╗ ██████╗ ███████╗                           ║
║  ██╔════╝██╔═══██╗██╔══██╗██╔════╝                           ║
║  ██║     ██║   ██║██║  ██║█████╗                             ║
║  ██║     ██║   ██║██║  ██║██╔══╝                             ║
║  ╚██████╗╚██████╔╝██████╔╝███████╗                           ║
║   ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝                           ║
║                                                               ║
║   ███████╗██╗██╗      ██████╗ ████████╗                      ║
║   ██╔══██╗██║██║     ██╔═══██╗╚══██╔══╝                      ║
║   ██████╔╝██║██║     ██║   ██║   ██║                         ║
║   ██╔═══╝ ██║██║     ██║   ██║   ██║                         ║
║   ██║     ██║███████╗╚██████╔╝   ██║                         ║
║   ╚═╝     ╚═╝╚══════╝ ╚═════╝    ╚═╝                         ║
║                                                               ║
║              Autonomous AI Software Engineer                  ║
╚═══════════════════════════════════════════════════════════════╝
```

**A multi-agent autonomous coding system powered by Google ADK. It plans, writes code, runs services, tests in a real visible browser, debugs, and delivers working software — end to end.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Google ADK](https://img.shields.io/badge/Google%20ADK-1.0+-orange.svg)](https://google.github.io/adk-docs/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## What is CodePilot?

CodePilot is a **terminal-based autonomous AI software engineer**. Give it a task in plain English — it will plan it, build it, run it, test it in a real browser, fix any failures, and deliver the finished project with a git commit and README. No babysitting required.

It uses a **team of specialized agents** in a deterministic pipeline, not a single monolithic loop:

```
Plan → Develop → Run → Browser Test → Debug → Finalize
```

Each phase is handled by a dedicated agent with access to only the tools it needs. Agents share state through key-value fields — no context window bloat from shared conversation history.

---

## Architecture

```
CodePilotPipeline (SequentialAgent)
  │
  ├── PlannerAgent
  │     Reads the request, checks prior session memory, explores the workspace,
  │     and creates a numbered task plan. Creates a Notion project page.
  │
  ├── DevelopmentLoop (LoopAgent — up to N iterations)
  │   │
  │   ├── DeveloperAgent
  │   │     Writes code, installs dependencies, makes conventional git commits,
  │   │     marks tasks complete in the plan, logs to Notion.
  │   │
  │   ├── RuntimeAgent
  │   │     Builds and starts the project. Detects type (web / API / CLI / library).
  │   │     Waits for ports. Health-checks with HTTP. Notifies Slack on failure.
  │   │
  │   ├── TestAgent
  │   │     Opens a VISIBLE browser via Playwright MCP. Navigates to the running app.
  │   │     Clicks, fills forms, verifies UI. Captures screenshots at each step.
  │   │     HTTP tests for APIs. Skips for CLI/library (already tested by Runtime).
  │   │
  │   └── DebugAgent
  │         Reads errors from state, diagnoses root cause, applies targeted fixes.
  │         Checks exit conditions. Calls exit_loop on success. Uses Slack HITL
  │         when stuck after 3+ failed attempts.
  │
  └── FinalizerAgent
        Stops servers, writes README.md, creates a final git commit, pushes to
        GitHub, opens a PR, marks project COMPLETED in Notion, notifies Slack.
```

### Guardrails

- **Identical-call detection**: soft nudge at 3+ identical consecutive tool calls; hard escalate at 8+.
- **Absolute safety net**: force-escalate after 200 total tool calls per agent.
- **No-op cycle detection**: tracks a fingerprint of critical state keys (app_ready, test_result, etc.) across iterations. Logs a warning when two consecutive iterations produce no progress.
- **Destructive op confirmation**: opt-in flag (`CODEPILOT_CONFIRM_DESTRUCTIVE=true`) to prompt before file deletes, force-pushes, or repo creation.

### State shared across agents

```
project_dir         workspace path (locked for the session lifetime)
plan_summary        plan output from PlannerAgent
iteration_count     current loop iteration (1-indexed)
app_type            "web" | "api" | "cli" | "library" | "script"
app_url             URL to test (set by RuntimeAgent)
app_ready           "true" | "false"
runtime_error       error details from RuntimeAgent (empty = OK)
test_result         "PASS" | "FAIL: ..." | "SKIP: ..."
test_errors         detailed test failure info
debug_log           fix applied this iteration
final_status        "SUCCESS" | "PARTIAL" | "FAILED"
notion_project_id   Notion page ID for this project
github_repo_url     GitHub PR URL
hitl_decision       last Slack HITL decision
screenshot_paths    comma-separated screenshot paths
```

---

## Installation

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Required |
| Node.js | 18+ | Required for Playwright browser testing |
| Ollama | Any | Optional — for local LLM |
| npx | Bundled with Node.js | Launches Playwright and GitHub MCP servers |

### Install

```bash
git clone https://github.com/bhanuprakash1212/CodePilot.git
cd CodePilot

python -m venv venv
source venv/bin/activate      # Linux/macOS
# venv\Scripts\activate       # Windows

pip install -e .
```

### First Run

```bash
# Interactive setup — choose provider, model, API keys
codepilot config init

# Create your first project session
codepilot create my-project

# Resume an existing session
codepilot open my-project
```

---

## CLI Reference

### Session Management

| Command | Description |
|---|---|
| `codepilot create <name>` | Create a new project session and start the REPL |
| `codepilot open <name>` | Resume a session (shows conversation history) |
| `codepilot list` | List all sessions with workspace path and last-active time |
| `codepilot delete <name>` | Delete a session and all its memory |

**Options:**
```bash
codepilot create my-api --priority high   # high / medium / low
codepilot create my-app --debug           # verbose logging
codepilot open my-app --no-history        # skip history display on open
```

### Configuration

| Command | Description |
|---|---|
| `codepilot config init` | Interactive configuration setup |
| `codepilot config show` | Display current configuration (tokens masked) |
| `codepilot config set-key <key>` | Set a single API key |
| `codepilot config add-key <key>` | Add a key to the rotation pool |
| `codepilot config list-keys` | List all configured keys (masked) |
| `codepilot config reset` | Reset configuration to defaults |

### Memory

| Command | Description |
|---|---|
| `codepilot memory` | Show global cross-session memory |
| `codepilot memory --set KEY --value VAL` | Set a global preference |

### REPL Commands (inside a session)

| Command | Description |
|---|---|
| `<task>` | Run a development task |
| `workspace` | Show project name and locked workspace path |
| `memory` | Show memory from previous sessions |
| `history` | Show command history |
| `clear` | Clear the terminal |
| `help` | Show help |
| `exit` | End the session |

---

## LLM Providers

CodePilot routes through Google ADK + LiteLLM and supports:

### OpenRouter (Recommended for production)

Access 100+ models — GPT-4, Claude, Gemini, Llama, Mistral — via a single API key.

```bash
codepilot config init
# Choose OpenRouter, paste your API key from https://openrouter.ai/keys
```

**Key rotation:** Add multiple keys and CodePilot round-robins across them, automatically cooling down failed keys for 60 seconds:
```bash
codepilot config add-key sk-or-v1-abc... --label primary
codepilot config add-key sk-or-v1-xyz... --label backup
```

### Ollama (Local, free)

Run models locally with zero API costs. Requires [Ollama](https://ollama.ai) installed and running.

```bash
ollama pull mistral        # Recommended — solid tool-calling support
ollama pull qwen2.5-coder  # Good for code tasks
```

```bash
codepilot config init
# Choose Ollama, select model
```

---

## Integrations

All integrations are optional — CodePilot works fully without them. Configure via `codepilot config init`.

### Notion — Project Tracking

Automatically creates a Notion page for each project with:
- Project info (workspace, status, goal)
- Task list with status updates (TODO → IN_PROGRESS → DONE / BLOCKED)
- Execution log (timestamped events for every major step)

**Required env vars:**
```
NOTION_TOKEN=secret_xxx
NOTION_PARENT_PAGE_ID=<32-char page ID>
```

Each write is verified by inspecting the API response and retried once on failure.

### Slack — Notifications + Human-in-the-Loop

Sends real-time messages for:
- 🚀 Pipeline started
- ⚠️ Build/test failure detected
- 🔧 Fix applied
- ✅ Pipeline completed (or ⚠️ partial/failed)

HITL workflow: When the Debug Agent is stuck after 3+ failed fix attempts, it posts a numbered question to Slack and waits for a human reply. Defaults to option 1 on timeout.

**Required env vars:**
```
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=#codepilot
```

**Bot scopes required:** `chat:write`, `channels:history`, `channels:read`

CodePilot verifies the bot is a member of the channel before posting and logs clearly if it isn't (rather than crashing).

### GitHub — Version Control + PR

Creates a remote repository, pushes code, and opens a Pull Request with:
- What was built
- Tasks completed
- Final status
- How to run the project
- Screenshot evidence

**Required env var:**
```
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
```

Also requires `npx` to launch the GitHub MCP server.

---

## Browser Testing

The TestAgent uses the `@playwright/mcp` server to run **real browser tests** against your running application.

**Headed mode (default):** A visible browser window opens on screen. You can watch every action in real time — navigation, clicks, form fills, and screenshots.

**Headless mode:** Set `CODEPILOT_BROWSER_HEADLESS=true` to run without a visible window (useful for CI).

### What gets tested

For web/fullstack apps:
1. Navigate to the running application
2. Verify page structure (title, key elements)
3. Simulate real user flows (create, edit, delete, drag-and-drop)
4. Capture screenshots at each step

### Screenshots

All screenshots are saved under:
```
<workspace>/tests/screenshots/<timestamp>_<action>.png
```

Examples:
```
tests/screenshots/20240115_143022_initial.png
tests/screenshots/20240115_143045_after_create.png
tests/screenshots/20240115_143102_final.png
```

Screenshot paths are stored in ADK state (`screenshot_paths`) and logged to Notion.

---

## Memory System

CodePilot maintains four layers of memory:

### 1. Per-project session store (`~/.codepilot/sessions/<project>/`)

| File | Contents |
|---|---|
| `metadata.json` | Project name, workspace path, created_at, last_active, priority |
| `messages.json` | Conversation history with role, content, timestamp, priority |
| `memory.json` | Structured long-term memory: episodic, semantic, procedural |
| `summary.json` | Rolling summary (auto-generated when message count > 40) |

**Priority tagging:** Messages are tagged HIGH / MEDIUM / LOW based on content keywords. High-priority messages (completions, errors, decisions) are retained preferentially during summarization.

**Context assembly** for each LLM call:
1. Rolling session summary (if any)
2. High-priority messages (last 3)
3. Recent messages (last 6)
4. Relevant long-term memory entries matching the current task

### 2. ADK session events (`~/.codepilot/session_memory.db`)

All session events are persisted automatically by ADK. Keyword search is scoped per project.

### 3. Structured agent memory (`~/.codepilot/memory.db`)

Agents explicitly store: `conversation`, `project`, `error_fix`, `preference` entries. The Debug Agent saves non-obvious fixes here so future sessions can apply them directly.

### 4. Global cross-session memory (`~/.codepilot/global_memory.json`)

User preferences, preferred stack, recurring patterns. Prepended to every LLM call.

---

## Session Lifecycle

### Create
```bash
codepilot create kanban-board
# → selects workspace interactively
# → initializes all memory files
# → starts REPL
```

### Open (restore)
```bash
codepilot open kanban-board
# → shows last 8 messages from conversation history
# → loads workspace path
# → restores memory context
# → starts REPL
```

### Delete
```bash
codepilot delete kanban-board
# → deletes sessions/<slug>/ (messages, memory, summaries, metadata)
# → confirmation prompt unless --force
```

---

## Configuration Reference

Config file: `~/.codepilot/config.json` (atomic writes via `.tmp` rename)

```json
{
  "llm": {
    "provider": "openrouter",
    "openrouter_model": "anthropic/claude-3.5-sonnet",
    "api_key": "sk-or-v1-...",
    "temperature": 0.7,
    "max_tokens": 8192
  },
  "github": {
    "token": "ghp_...",
    "auto_commit": true
  },
  "notion": {
    "token": "secret_...",
    "parent_page_id": "abc123..."
  },
  "slack": {
    "bot_token": "xoxb-...",
    "channel": "#codepilot"
  }
}
```

### Environment variable overrides

| Variable | Purpose |
|---|---|
| `CODEPILOT_BROWSER_HEADLESS` | Set to `true` to run Playwright without a visible window |
| `CODEPILOT_CONFIRM_DESTRUCTIVE` | Set to `true` to prompt before deletes, pushes, and repo creation |
| `OPENROUTER_API_KEY` | Override OpenRouter key (read by LiteLLM) |
| `OLLAMA_API_BASE` | Override Ollama base URL (default: `http://localhost:11434`) |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub token for MCP |
| `NOTION_TOKEN` | Notion integration token |
| `NOTION_PARENT_PAGE_ID` | Parent page for project pages |
| `SLACK_BOT_TOKEN` | Slack bot token |
| `SLACK_CHANNEL` | Default Slack channel |

---

## Observability

Every significant event is logged:

| Event | Where logged |
|---|---|
| Agent step start/end | Terminal (Rich renderer) + logger |
| Tool call (name + args) | Terminal (icon + label + arg summary) |
| Tool result | Terminal (✓ / ✗ + key output) |
| Phase transitions | Terminal phase headers (Planning / Executing / Verifying / Fixing) |
| Iteration start/end | Logger with elapsed time |
| Total tool calls per run | Logger at pipeline completion |
| No-op iterations detected | Logger warning |
| Slack HITL decisions | Logger + Slack |
| Notion write failures | Logger error (with retry info) |
| Transient API errors | Terminal (yellow warning) + Logger |

**Debug mode:** `codepilot create <name> --debug` enables full debug logging to file.

---

## Project Structure

```
codepilot/
├── __init__.py                     # Package root, v1.0.0
├── __main__.py                     # python -m codepilot entry
├── cli.py                          # Typer CLI (create, open, list, delete, config, memory)
├── agents/
│   ├── builder.py                  # Pipeline assembly (SequentialAgent + LoopAgent)
│   ├── runner.py                   # ADK Runner — streaming, retry, profiling
│   ├── prompts.py                  # Agent instruction strings (6 agents)
│   ├── patches.py                  # ADK + LiteLLM compatibility patches
│   ├── mcp_config.py               # Playwright (headed) + GitHub MCP toolsets
│   ├── callbacks/
│   │   ├── guardrails.py           # Loop guard + no-op cycle detection
│   │   ├── lifecycle.py            # Iteration counter + timing profiling
│   │   └── human_in_loop.py        # Destructive op confirmation (opt-in)
│   └── tools/
│       ├── fs.py                   # File read/write/edit/search
│       ├── exec.py                 # Shell commands + background processes
│       ├── git.py                  # Git operations (injection-safe commit)
│       ├── workspace.py            # Project detection, tree, search
│       ├── planning.py             # Task plan (ADK state-backed)
│       ├── testing.py              # Test runner + HTTP requests + syntax check
│       ├── environment.py          # Runtime detection + venv management
│       ├── debug_tools.py          # Error parsing + log tailing
│       ├── validation.py           # Exit condition checks
│       ├── state.py                # set_state + exit_loop
│       ├── memory_tools.py         # SQLite-backed structured memory tools
│       ├── notion_tools.py         # Notion API (retry + write verification)
│       └── slack_hitl.py           # Slack notify + HITL (channel membership check)
├── config/
│   ├── models.py                   # Pydantic config models
│   ├── manager.py                  # Config persistence (atomic write)
│   └── keys.py                     # API key rotation (round-robin + cooldown)
├── core/
│   ├── exceptions.py               # Exception hierarchy
│   ├── renderer.py                 # Rich terminal renderer (phase-aware)
│   ├── session.py                  # Per-project session store + history display
│   ├── workspace.py                # Interactive workspace selection
│   └── global_memory.py            # Cross-session user preferences
├── memory/
│   └── service.py                  # ADK SqliteMemoryService (session events)
└── utils/
    ├── constants.py                # Path constants, defaults, banner
    └── logger.py                   # Rich-handler logger factory
```

---

## Usage Examples

```bash
# Build a full-stack web application
codepilot create todo-app
> Build a React + FastAPI todo app with drag-and-drop tasks

# Work on an existing project
codepilot create auth-feature
> Add JWT authentication to the existing Flask app in this workspace

# Debug a failing project
codepilot open todo-app
> Fix the failing end-to-end tests

# Ask about what was previously built
codepilot open todo-app
> What's the current status of the project?

# Deploy to GitHub
codepilot open todo-app
> Push the project to GitHub and create a PR
```

---

## Troubleshooting

### Browser window does not appear

The Playwright browser opens in headed mode by default. If it doesn't appear:
1. Make sure `npx` is available: `npx --version`
2. Install Playwright browsers: `npx playwright install chromium`
3. Check `CODEPILOT_BROWSER_HEADLESS` is not set to `true`

### Notion writes not appearing

1. Verify `NOTION_TOKEN` is set: `codepilot config show`
2. Verify `NOTION_PARENT_PAGE_ID` is set and is a valid 32-char page ID
3. Ensure your Notion integration has access to the parent page
4. Check logs for retry attempts: run with `--debug`

### Slack notifications not delivered

1. Verify `SLACK_BOT_TOKEN` starts with `xoxb-`
2. Ensure the bot is invited to the channel: `/invite @<bot-name>`
3. Verify bot scopes: `chat:write`, `channels:history`, `channels:read`
4. CodePilot logs a clear error if the bot is not in the channel — check terminal output

### Pipeline loops without progress

If you see "No-op iteration detected" warnings, the agents are stuck. Options:
1. End the session and restart with a more specific task description
2. Set `CODEPILOT_CONFIRM_DESTRUCTIVE=true` to review risky decisions manually
3. Check `--debug` logs for the specific blocker

### API rate limits

CodePilot automatically retries on rate-limit errors (waits 65 seconds). For sustained use, configure multiple API keys:
```bash
codepilot config add-key sk-or-v1-... --label key1
codepilot config add-key sk-or-v1-... --label key2
```

---

## Contributing

```bash
git clone https://github.com/bhanuprakash1212/CodePilot.git
cd CodePilot
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built by [Bhanu Prakash](https://github.com/bhanuprakash1212)**

*CodePilot plans, builds, runs, and tests software — autonomously.*

</div>
