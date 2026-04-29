"""Clean CLI interface for CodePilot."""

import sys
from typing import Optional

import typer
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .config import ConfigManager
from .agents import create_codepilot_runner
from .core.exceptions import CodePilotError, ConfigurationError
from .core.global_memory import GlobalMemory
from .core.session import SessionStore
from .core.workspace import select_workspace
from .utils import console, enable_debug_mode, get_logger
from .utils.constants import (
    APP_NAME,
    APP_TAGLINE,
    BANNER,
    CONFIG_FILE,
    OLLAMA_DEFAULT_MODEL,
    PROVIDER_OLLAMA,
    PROVIDER_OPENROUTER,
)

logger = get_logger(__name__)

app = typer.Typer(
    name="codepilot",
    help=f"{APP_NAME} - {APP_TAGLINE}",
    no_args_is_help=True,
    add_completion=False,
)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def show_banner() -> None:
    """Display CodePilot banner."""
    console.print(BANNER)


def interactive_config_setup() -> bool:
    """Interactive configuration setup.
    
    Returns:
        True if config was created successfully, False otherwise.
    """
    console.print("\n[yellow]⚠️  No configuration found![/yellow]\n")
    
    if not Confirm.ask("Would you like to configure CodePilot now?", default=True):
        console.print("[red]Configuration required to run CodePilot.[/red]")
        return False
    
    config_mgr = ConfigManager()
    
    # Choose provider
    console.print("\n[bold cyan]Choose LLM Provider:[/bold cyan]")
    console.print("  1. OpenRouter (requires API key, more models)")
    console.print("  2. Ollama (local, free, requires Ollama running)")
    
    provider_choice = Prompt.ask(
        "Select provider",
        choices=["1", "2"],
        default="2"
    )
    
    provider = PROVIDER_OLLAMA if provider_choice == "2" else PROVIDER_OPENROUTER
    model = None
    api_key = None
    github_token = None
    
    if provider == PROVIDER_OPENROUTER:
        console.print("\n[cyan]OpenRouter Configuration:[/cyan]")
        console.print("[dim]Get your API key at: https://openrouter.io/keys[/dim]\n")
        api_key = Prompt.ask("Enter your OpenRouter API key", password=True)
        
        # Ask user to configure models
        console.print("\n[bold]Configure Models:[/bold]")
        console.print("[dim]Add models you want to use (optional)[/dim]\n")
        
        openrouter_models = []
        if Confirm.ask("Add models now?", default=False):
            console.print("[dim]Enter model names (one per prompt, empty to finish)[/dim]")
            while True:
                model_name = Prompt.ask("Model name (or press Enter to finish)", default="")
                if not model_name:
                    break
                if model_name not in openrouter_models:
                    openrouter_models.append(model_name)
                    console.print(f"[green]✓ Added: {model_name}[/green]")
        
        # Select model to use
        console.print("\n[bold]Select model to use:[/bold]")
        if openrouter_models:
            for idx, model_name in enumerate(openrouter_models, start=1):
                console.print(f"  {idx}. {model_name}")
            console.print(f"  {len(openrouter_models) + 1}. Enter custom model")
            
            model_choice = Prompt.ask(
                "Select model",
                choices=[str(i) for i in range(1, len(openrouter_models) + 2)],
                default="1" if openrouter_models else str(len(openrouter_models) + 1)
            )
            
            if int(model_choice) <= len(openrouter_models):
                model = openrouter_models[int(model_choice) - 1]
            else:
                model = Prompt.ask("Enter model name")
        else:
            model = Prompt.ask("Enter model name")
        
        # Store models in kwargs for config creation
        api_key_kwargs = {"openrouter_models": openrouter_models}
    else:
        console.print("\n[cyan]Ollama Configuration:[/cyan]")
        console.print("[dim]Make sure Ollama is running on http://localhost:11434[/dim]\n")
        
        console.print("[bold]Available models:[/bold]")
        console.print("  1. mistral (Recommended, supports tools)")
        console.print("  2. llama2")
        console.print("  3. codellama")
        console.print("  4. Custom model")
        
        model_choice = Prompt.ask(
            "Select model",
            choices=["1", "2", "3", "4"],
            default="1"
        )
        
        models = {
            "1": "mistral",
            "2": "llama2",
            "3": "codellama",
        }
        
        if model_choice == "4":
            model = Prompt.ask("Enter model name (e.g., 'mistral')")
        else:
            model = models[model_choice]
    
    # Optional integrations
    console.print("\n[cyan]Optional Integrations:[/cyan]")
    console.print("[dim]These enable GitHub push, Notion planning, and Slack notifications.[/dim]")
    console.print("[dim]All are optional — CodePilot works fully without them.[/dim]\n")

    if Confirm.ask("Configure GitHub token?", default=False):
        console.print("[dim]Needed for: push code to GitHub, create repos, open PRs[/dim]")
        github_token = Prompt.ask("Enter your GitHub personal access token", password=True)

    notion_token = None
    notion_parent_page_id = None
    if Confirm.ask("Configure Notion token?", default=False):
        console.print("[dim]Needed for: project pages, task tracking, execution logs in Notion[/dim]")
        console.print("[dim]Get integration token at: https://www.notion.so/profile/integrations[/dim]")
        notion_token = Prompt.ask("Enter your Notion integration token (secret_xxx)", password=True)
        console.print("[dim]Parent page ID: open a Notion page → Copy link → extract the ID (32-char hex)[/dim]")
        notion_parent_page_id = Prompt.ask(
            "Enter parent Notion page ID (leave blank to configure later)",
            default=""
        ) or None

    slack_token = None
    slack_channel = None
    if Confirm.ask("Configure Slack notifications?", default=False):
        console.print("[dim]Needed for: failure alerts, human-in-the-loop decisions[/dim]")
        console.print("[dim]Required bot scopes: chat:write, channels:history, channels:read[/dim]")
        slack_token = Prompt.ask("Enter your Slack bot token (xoxb-...)", password=True)
        slack_channel = Prompt.ask("Default channel for notifications", default="#codepilot")

    # Create configuration
    try:
        create_kwargs = {}
        if provider == PROVIDER_OPENROUTER and 'api_key_kwargs' in locals():
            create_kwargs.update(api_key_kwargs)

        config_mgr.create(
            provider=provider,
            model=model,
            api_key=api_key,
            **create_kwargs
        )

        if github_token:
            config_mgr.update_github(github_token)
        if notion_token:
            config_mgr.update_notion(notion_token, notion_parent_page_id)
        if slack_token:
            config_mgr.update_slack(slack_token, slack_channel)

        console.print("\n[green]✓ Configuration created successfully![/green]")
        return True
    except Exception as e:
        console.print(f"\n[red]✗ Failed to create configuration: {e}[/red]")
        return False


# ============================================================================
# SESSION COMMANDS  (create / open / list / delete)
# ============================================================================

def _require_config(debug: bool = False) -> ConfigManager:
    """Load config, triggering interactive setup if missing."""
    config_manager = ConfigManager()
    if not config_manager.exists:
        if not interactive_config_setup():
            raise typer.Exit(1)
    return config_manager


def _start_session(
    store: SessionStore,
    config_manager: ConfigManager,
    debug: bool,
) -> None:
    """Common entry point: build runner and start the interactive REPL."""
    cfg = config_manager.config
    console.print(f"[dim]Provider: {cfg.llm.active_provider} | Model: {cfg.llm.active_model}[/dim]")
    console.print(f"[dim]Project:  {store.project_name}[/dim]")
    console.print(f"[dim]Workspace: {store.workspace_path}[/dim]\n")

    try:
        runner = create_codepilot_runner(config_manager, store)
        runner.run_interactive()
    except ConfigurationError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise typer.Exit(1)
    except CodePilotError as e:
        console.print(f"[red]Error:[/red] {e}")
        if debug:
            raise
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Session paused — resume with: codepilot open {store.project_name}[/yellow]")
        raise typer.Exit(0)
    except SystemExit:
        raise typer.Exit(0)
    except Exception as e:
        console.print(f"[red]Unexpected error:[/red] {e}")
        if debug:
            raise
        raise typer.Exit(1)


@app.command("create")
def create_command(
    project_name: str = typer.Argument(..., help="Name for the new project session"),
    priority: str = typer.Option("medium", "--priority", "-p", help="high / medium / low"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug mode"),
) -> None:
    """Create a new isolated project session and start the REPL.

    A new workspace directory is selected interactively.
    Each project gets its own memory, messages, and conversation history.

    Examples:
        codepilot create kanban-board
        codepilot create my-api --priority high
    """
    if debug:
        enable_debug_mode()
    show_banner()

    config_manager = _require_config(debug)
    store = SessionStore(project_name)

    if store.exists():
        console.print(
            f"[yellow]Session '{store.project_name}' already exists.[/yellow]\n"
            f"To resume it run: [cyan]codepilot open {store.project_name}[/cyan]"
        )
        raise typer.Exit(1)

    workspace = select_workspace()
    store.create(workspace_path=str(workspace), priority=priority)
    console.print(f"[green]✓ Session created:[/green] [bold]{store.project_name}[/bold]")

    _start_session(store, config_manager, debug)


def _show_session_history(store: "SessionStore") -> None:
    """Print recent conversation history to the terminal when resuming a session.

    Shows the last 8 messages so the user has context before issuing the next
    task. Truncated to 300 chars per message so the terminal doesn't flood.
    """
    from rich.panel import Panel
    history = store.get_recent_history_display(max_messages=8)
    if not history:
        return

    lines: list[str] = []
    for msg in history:
        role = msg["role"].upper()
        ts = msg["timestamp"]
        content = msg["content"].replace("\n", " ")
        style = "cyan" if role == "USER" else "green"
        lines.append(f"[{style}][{ts}] {role}:[/{style}] {content}")

    console.print(
        Panel(
            "\n".join(lines),
            title=f"[dim]Recent history — {store.project_name}[/dim]",
            border_style="dim",
            padding=(0, 1),
        )
    )
    console.print()


@app.command("open")
def open_command(
    project_name: str = typer.Argument(..., help="Name of the project session to resume"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug mode"),
    history: bool = typer.Option(True, "--history/--no-history", help="Show recent conversation history on open"),
) -> None:
    """Resume an existing project session.

    Loads the project's memory, conversation history, and workspace path.
    Context from previous interactions is automatically injected into the LLM.
    Recent conversation history is displayed on open so you have full context.

    Examples:
        codepilot open kanban-board
        codepilot open kanban-board --no-history   # skip history display
    """
    if debug:
        enable_debug_mode()
    show_banner()

    config_manager = _require_config(debug)
    store = SessionStore(project_name)

    if not store.exists():
        console.print(
            f"[red]Session '{store.project_name}' not found.[/red]\n"
            f"Create it with: [cyan]codepilot create {project_name}[/cyan]\n"
            f"Or list existing sessions: [cyan]codepilot list[/cyan]"
        )
        raise typer.Exit(1)

    store.load_metadata()

    # Restore conversation context visually so the user knows where they left off
    if history:
        _show_session_history(store)

    _start_session(store, config_manager, debug)


@app.command("list")
def list_command() -> None:
    """List all project sessions with status and last-active time.

    Example:
        codepilot list
    """
    sessions = SessionStore.list_all()

    if not sessions:
        console.print(
            "[yellow]No sessions found.[/yellow]\n"
            "Create one with: [cyan]codepilot create <project-name>[/cyan]"
        )
        return

    table = Table(title="CodePilot Sessions", show_lines=False)
    table.add_column("Project", style="cyan", no_wrap=True)
    table.add_column("Priority", style="dim", justify="center")
    table.add_column("Workspace", style="dim", overflow="fold")
    table.add_column("Last Active", style="green", no_wrap=True)
    table.add_column("Created", style="dim", no_wrap=True)

    for s in sessions:
        last = s.get("last_active", "")[:19].replace("T", " ")
        created = s.get("created_at", "")[:10]
        table.add_row(
            s.get("project_name", "?"),
            s.get("priority", "medium"),
            s.get("workspace_path", ""),
            last,
            created,
        )

    console.print(table)
    console.print(f"\n[dim]{len(sessions)} session(s). Resume with: codepilot open <project>[/dim]")


@app.command("delete")
def delete_command(
    project_name: str = typer.Argument(..., help="Project session to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete a project session and all its memory.

    This permanently removes messages, memory, and summaries for the project.

    Example:
        codepilot delete kanban-board
        codepilot delete kanban-board --force
    """
    store = SessionStore(project_name)
    if not store.exists():
        console.print(f"[red]Session '{store.project_name}' not found.[/red]")
        raise typer.Exit(1)

    if not force:
        if not Confirm.ask(f"Delete session '{store.project_name}' and all its memory?"):
            raise typer.Exit(0)

    if SessionStore.delete(store.project_name):
        console.print(f"[green]✓ Session '{store.project_name}' deleted.[/green]")
    else:
        console.print("[red]Failed to delete session.[/red]")
        raise typer.Exit(1)


@app.command(name="run", hidden=True)
def run_command(
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug mode"),
) -> None:
    """[Deprecated] Prompts for a project name then creates or opens it.

    Prefer: codepilot create <name>  or  codepilot open <name>
    """
    if debug:
        enable_debug_mode()
    show_banner()

    config_manager = _require_config(debug)
    sessions = SessionStore.list_all()

    if sessions:
        console.print("[bold]Existing sessions:[/bold]")
        for s in sessions[:5]:
            console.print(f"  • [cyan]{s['project_name']}[/cyan]  [dim]{s.get('last_active','')[:10]}[/dim]")
        if len(sessions) > 5:
            console.print(f"  [dim]... and {len(sessions) - 5} more (codepilot list)[/dim]")

    project_name = Prompt.ask(
        "\nProject name (new name to create, existing name to open)"
    ).strip()

    if not project_name:
        raise typer.Exit(0)

    store = SessionStore(project_name)
    if store.exists():
        store.load_metadata()
        console.print(f"[dim]Resuming existing session…[/dim]")
    else:
        workspace = select_workspace()
        store.create(workspace_path=str(workspace))
        console.print(f"[green]✓ Session created:[/green] [bold]{store.project_name}[/bold]")

    _start_session(store, config_manager, debug)


# Make run the default command
app.command(name="")(run_command)


# ============================================================================
# CONFIG COMMANDS
# ============================================================================

config_app = typer.Typer(help="Manage configuration")
app.add_typer(config_app, name="config")


@config_app.command("init")
def config_init() -> None:
    """Initialize or update configuration interactively.
    
    Examples:
        codepilot config init     # Interactive menu
    """
    try:
        config_manager = ConfigManager()
        
        if config_manager.exists:
            console.print(
                f"[yellow]Configuration already exists at: {CONFIG_FILE}[/yellow]\n"
            )
            
            # Interactive loop for multiple configurations
            while True:
                console.print("[bold cyan]What would you like to do?[/bold cyan]")
                console.print("  1. Update OpenRouter configuration")
                console.print("  2. Update Ollama configuration")
                console.print("  3. Update GitHub token")
                console.print("  4. Update Notion token")
                console.print("  5. Update Slack token")
                console.print("  6. Set provider preference")
                console.print("  7. Manage OpenRouter models")
                console.print("  8. Reset to defaults")
                console.print("  9. Delete configuration")
                console.print("  10. Exit")

                choice = Prompt.ask(
                    "Select option",
                    choices=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
                    default="10"
                )

                if choice == "10":
                    console.print("\n[green]Configuration saved![/green]")
                    return
                elif choice == "9":
                    if Confirm.ask("Are you sure you want to delete the configuration?", default=False):
                        config_manager.config_path.unlink()
                        console.print("[green]✓ Configuration deleted[/green]")
                    return
                elif choice == "8":
                    if Confirm.ask("Reset configuration to defaults?", default=False):
                        config_manager.config_path.unlink()
                        config_manager.create(provider=PROVIDER_OLLAMA, model=OLLAMA_DEFAULT_MODEL)
                        console.print("[green]✓ Configuration reset to defaults (Ollama/mistral)[/green]")
                    return
                elif choice == "7":
                    _manage_models(config_manager)
                    console.print()
                elif choice == "6":
                    _update_provider_preference(config_manager)
                    console.print()
                elif choice == "5":
                    _update_slack_token(config_manager)
                    console.print()
                elif choice == "4":
                    _update_notion_token(config_manager)
                    console.print()
                elif choice == "3":
                    _update_github_token(config_manager)
                    console.print()
                elif choice in ["1", "2"]:
                    provider = PROVIDER_OPENROUTER if choice == "1" else PROVIDER_OLLAMA
                    _update_provider_config(config_manager, provider)
                    console.print()
        else:
            # New configuration
            interactive_config_setup()
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


def _update_provider_config(config_manager: ConfigManager, provider: str) -> None:
    """Update provider configuration (OpenRouter or Ollama).
    
    Writes the selected model to the per-provider field (openrouter_model
    or ollama_model) so switching providers doesn't clobber the other
    provider's model selection.
    """
    config = config_manager.config
    
    if provider == PROVIDER_OPENROUTER:
        console.print("\n[cyan]OpenRouter Configuration:[/cyan]")
        if Confirm.ask("Update API key?", default=True):
            api_key = Prompt.ask("Enter OpenRouter API key", password=True)
            config.llm.api_key = api_key
        
        # Load models from config
        configured_models = config.llm.openrouter_models or []
        model_options = []
        models = {}
        start_index = 1
        
        # Display configured models
        if configured_models:
            console.print("\n[bold]Configured models:[/bold]")
            for idx, model in enumerate(configured_models, start=1):
                console.print(f"  {idx}. {model}")
                models[str(idx)] = model
                model_options.append(str(idx))
            start_index = len(configured_models) + 1
        
        console.print(f"  {start_index}. Enter custom model name")
        model_options.append(str(start_index))
        
        model_choice = Prompt.ask(
            "Select model",
            choices=model_options,
            default="1" if configured_models else str(start_index)
        )
        
        if model_choice == str(start_index):
            selected_model = Prompt.ask("Enter model name")
        else:
            selected_model = models[model_choice]
        
        # Store in per-provider field (doesn't touch ollama_model)
        config.llm.openrouter_model = selected_model
    else:
        console.print("\n[cyan]Ollama Configuration:[/cyan]")
        console.print("[dim]Make sure Ollama is running on http://localhost:11434[/dim]\n")
        
        console.print("[bold]Available models:[/bold]")
        console.print("  1. mistral (Recommended, supports tools)")
        console.print("  2. llama2")
        console.print("  3. codellama")
        console.print("  4. Custom model")
        
        model_choice = Prompt.ask(
            "Select model",
            choices=["1", "2", "3", "4"],
            default="1"
        )
        
        models = {
            "1": "mistral",
            "2": "llama2",
            "3": "codellama",
        }
        
        if model_choice == "4":
            selected_model = Prompt.ask("Enter model name")
        else:
            selected_model = models[model_choice]
        
        # Store in per-provider field (doesn't touch openrouter_model)
        config.llm.ollama_model = selected_model
    
    config_manager._save()
    console.print(f"[green]✓ {provider.title()} configuration updated[/green]")


def _update_github_token(config_manager: ConfigManager) -> None:
    """Update GitHub token configuration."""
    current = config_manager.config.github.token
    status = "● set" if current else "not set"
    console.print(f"\n[cyan]GitHub Token[/cyan] [dim]({status})[/dim]")
    console.print("[dim]Used for: push code, create repos, open PRs[/dim]")
    console.print("[dim]Get it at: https://github.com/settings/tokens[/dim]\n")
    if Confirm.ask("Set a new GitHub token?", default=not bool(current)):
        token = Prompt.ask("Enter GitHub personal access token", password=True)
        config_manager.update_github(token)
        console.print("[green]✓ GitHub token updated[/green]")
    elif current and Confirm.ask("Clear existing GitHub token?", default=False):
        config_manager.update_github(None)
        console.print("[green]✓ GitHub token cleared[/green]")


def _update_notion_token(config_manager: ConfigManager) -> None:
    """Update Notion integration token and parent page ID."""
    current = config_manager.config.notion.token
    current_page = config_manager.config.notion.parent_page_id
    status = "● set" if current else "not set"
    console.print(f"\n[cyan]Notion Integration[/cyan] [dim]({status})[/dim]")
    console.print("[dim]Used for: project pages, task tracking, execution logs[/dim]")
    console.print("[dim]Token: https://www.notion.so/profile/integrations[/dim]\n")
    if Confirm.ask("Set a new Notion token?", default=not bool(current)):
        token = Prompt.ask("Enter Notion integration token (secret_xxx)", password=True)
        console.print("[dim]Parent page ID: open a Notion page → Copy link → extract 32-char hex ID[/dim]")
        page_id = Prompt.ask(
            "Enter parent Notion page ID (where project pages will be created)",
            default=current_page or ""
        )
        config_manager.update_notion(token, page_id or None)
        console.print("[green]✓ Notion token updated[/green]")
    elif current and Confirm.ask("Clear existing Notion token?", default=False):
        config_manager.update_notion(None)
        console.print("[green]✓ Notion token cleared[/green]")


def _update_slack_token(config_manager: ConfigManager) -> None:
    """Update Slack bot token and channel."""
    current = config_manager.config.slack.bot_token
    current_ch = config_manager.config.slack.channel or "#codepilot"
    status = "● set" if current else "not set"
    console.print(f"\n[cyan]Slack Bot Token[/cyan] [dim]({status})[/dim]")
    console.print("[dim]Used for: completion notifications, failure alerts, user input[/dim]")
    console.print("[dim]Create a bot at: https://api.slack.com/apps[/dim]")
    console.print("[dim]Required scopes: chat:write, channels:read[/dim]\n")
    if Confirm.ask("Set a new Slack token?", default=not bool(current)):
        token = Prompt.ask("Enter Slack bot token (xoxb-...)", password=True)
        channel = Prompt.ask("Default channel", default=current_ch)
        config_manager.update_slack(token, channel)
        console.print("[green]✓ Slack token updated[/green]")
    elif current and Confirm.ask("Clear existing Slack token?", default=False):
        config_manager.update_slack(None)
        console.print("[green]✓ Slack token cleared[/green]")


def _manage_models(config_manager: ConfigManager) -> None:
    """Manage OpenRouter models."""
    config = config_manager.config
    
    while True:
        console.print("\n[cyan]OpenRouter Models:[/cyan]")
        
        if not config.llm.openrouter_models:
            console.print("[dim]No models configured[/dim]\n")
        else:
            console.print("\n[bold]Configured models:[/bold]")
            for idx, model in enumerate(config.llm.openrouter_models, start=1):
                console.print(f"  {idx}. {model}")
            console.print()
        
        console.print("[bold]Options:[/bold]")
        console.print("  1. Add new model")
        console.print("  2. Remove model")
        console.print("  3. Clear all models")
        console.print("  4. Back to main menu")
        
        choice = Prompt.ask(
            "Select option",
            choices=["1", "2", "3", "4"],
            default="4"
        )
        
        if choice == "4":
            break
        elif choice == "1":
            model_name = Prompt.ask(
                "Enter model identifier (e.g., 'provider/model-name')"
            )
            if model_name and model_name not in config.llm.openrouter_models:
                config.llm.openrouter_models.append(model_name)
                config_manager._save()
                console.print(f"[green]✓ Added model: {model_name}[/green]")
            elif model_name in config.llm.openrouter_models:
                console.print(f"[yellow]Model already exists: {model_name}[/yellow]")
        elif choice == "2":
            if not config.llm.openrouter_models:
                console.print("[yellow]No models to remove[/yellow]")
                continue
            
            console.print("\n[bold]Select model to remove:[/bold]")
            for idx, model in enumerate(config.llm.openrouter_models, start=1):
                console.print(f"  {idx}. {model}")
            
            try:
                remove_choice = Prompt.ask(
                    "Enter number to remove (or 0 to cancel)",
                    default="0"
                )
                remove_idx = int(remove_choice)
                if remove_idx > 0 and remove_idx <= len(config.llm.openrouter_models):
                    removed = config.llm.openrouter_models.pop(remove_idx - 1)
                    config_manager._save()
                    console.print(f"[green]✓ Removed model: {removed}[/green]")
            except (ValueError, IndexError):
                console.print("[yellow]Invalid selection[/yellow]")
        elif choice == "3":
            if config.llm.openrouter_models:
                if Confirm.ask("Clear all models?", default=False):
                    config.llm.openrouter_models.clear()
                    config_manager._save()
                    console.print("[green]✓ All models cleared[/green]")
            else:
                console.print("[yellow]No models to clear[/yellow]")


def _update_provider_preference(config_manager: ConfigManager) -> None:
    """Update provider preference (auto, openrouter, or ollama)."""
    console.print("\n[cyan]Provider Preference:[/cyan]")
    console.print("Choose which provider to prefer when both are available:")
    console.print("  1. Auto (use OpenRouter if API key available, else Ollama)")
    console.print("  2. Always prefer OpenRouter")
    console.print("  3. Always prefer Ollama")
    
    choice = Prompt.ask(
        "Select preference",
        choices=["1", "2", "3"],
        default="1"
    )
    
    config = config_manager.config
    if choice == "1":
        config.llm.provider_preference = None
        console.print("[green]✓ Provider preference set to auto[/green]")
    elif choice == "2":
        config.llm.provider_preference = PROVIDER_OPENROUTER
        console.print("[green]✓ Provider preference set to OpenRouter[/green]")
    else:
        config.llm.provider_preference = PROVIDER_OLLAMA
        console.print("[green]✓ Provider preference set to Ollama[/green]")
    
    config_manager._save()





@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    try:
        config_manager = ConfigManager()
        
        if not config_manager.exists:
            console.print("[yellow]No configuration found[/yellow]")
            raise typer.Exit(1)
        
        config = config_manager.config
        
        table = Table(title="CodePilot Configuration")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        
        # Show the ACTIVE provider/model (determined by preference)
        active_prov = config.llm.active_provider
        active_model = config.llm.active_model
        pref = config.llm.provider_preference or "auto"
        
        table.add_row("Provider", active_prov)
        table.add_row("Model", active_model)
        table.add_row("Preference", pref)
        table.add_row("Temperature", str(config.llm.temperature))
        table.add_row("Max Tokens", str(config.llm.max_tokens))
        
        # Show per-provider models if configured
        or_model = config.llm.openrouter_model
        ol_model = config.llm.ollama_model
        if or_model:
            table.add_row("OpenRouter Model", or_model)
        if ol_model:
            table.add_row("Ollama Model", ol_model)
        
        # API key status
        if config.llm.has_single_key:
            table.add_row("API Key", "● ● ● ● (single key)")
        elif config.llm.has_multiple_keys:
            table.add_row("API Keys", f"{len(config.llm.api_keys)} keys configured")
        else:
            table.add_row("API Key", "[dim]Not set[/dim]")
        
        table.add_row("Work Directory", str(config.work_dir))
        table.add_row("Config File", str(CONFIG_FILE))

        # Integration tokens (show status only, never the raw value)
        gh_status = "[green]✓ Configured[/green]" if config.github.token else "[dim]Not set[/dim]"
        notion_status = "[green]✓ Configured[/green]" if config.notion.token else "[dim]Not set[/dim]"
        slack_status = (
            f"[green]✓ Configured[/green] → {config.slack.channel or '#codepilot'}"
            if config.slack.bot_token else "[dim]Not set[/dim]"
        )
        table.add_row("GitHub Token", gh_status)
        table.add_row("Notion Token", notion_status)
        table.add_row("Slack Token", slack_status)

        console.print(table)

        # Show rotator status if multiple keys
        if config.llm.has_multiple_keys:
            status = config_manager.get_rotator_status()
            console.print(f"\n[cyan]Key Rotation:[/cyan]")
            console.print(f"  Active keys: {status['active_keys']}/{status['total_keys']}")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("set-key")
def config_set_key(
    api_key: str = typer.Argument(..., help="API key to set"),
) -> None:
    """Set single API key (replaces existing).
    
    Example:
        codepilot config set-key sk-or-v1-xxx
    """
    try:
        config_manager = ConfigManager()
        
        if not config_manager.exists:
            console.print("[red]No configuration found. Run: codepilot config init[/red]")
            raise typer.Exit(1)
        
        config_manager.update_llm(api_key=api_key)
        console.print("[green]✅ API key updated[/green]")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("add-key")
def config_add_key(
    api_key: str = typer.Argument(..., help="API key to add"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Key label"),
) -> None:
    """Add API key for rotation (multi-key mode).
    
    Example:
        codepilot config add-key sk-or-v1-xxx --label "free-tier-1"
    """
    try:
        config_manager = ConfigManager()
        
        if not config_manager.exists:
            console.print("[red]No configuration found. Run: codepilot config init[/red]")
            raise typer.Exit(1)
        
        config_manager.add_api_key(api_key, label)
        console.print(f"[green]✅ Added API key:[/green] {label or api_key[:8]}...")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("remove-key")
def config_remove_key(
    identifier: str = typer.Argument(..., help="Key label or prefix to remove"),
) -> None:
    """Remove API key from rotation.
    
    Example:
        codepilot config remove-key "free-tier-1"
        codepilot config remove-key sk-or-v1-abc
    """
    try:
        config_manager = ConfigManager()
        
        if not config_manager.exists:
            console.print("[red]No configuration found[/red]")
            raise typer.Exit(1)
        
        config_manager.remove_api_key(identifier)
        console.print(f"[green]✅ Removed API key:[/green] {identifier}")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("list-keys")
def config_list_keys() -> None:
    """List all configured API keys (masked)."""
    try:
        config_manager = ConfigManager()
        
        if not config_manager.exists:
            console.print("[red]No configuration found[/red]")
            raise typer.Exit(1)
        
        keys = config_manager.list_api_keys()
        
        if not keys:
            console.print("[yellow]No API keys configured[/yellow]")
            return
        
        table = Table(title="API Keys")
        table.add_column("Label", style="cyan")
        table.add_column("Key", style="dim")
        
        if keys[0].get("mode") == "single":
            table.add_column("Mode", style="green")
            table.add_row(keys[0]["label"], keys[0]["key"], "Single")
        else:
            table.add_column("Status", style="green")
            table.add_column("Usage", style="yellow")
            
            for key in keys:
                status = "✅ Active" if key["active"] else "❌ Inactive"
                table.add_row(
                    key["label"],
                    key["key"],
                    status,
                    str(key["usage"])
                )
        
        console.print(table)
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("reset")
def config_reset(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Reset configuration (delete config file)."""
    try:
        if not force:
            if not typer.confirm("⚠️  Delete configuration?"):
                raise typer.Exit(0)
        
        config_manager = ConfigManager()
        config_manager.reset()
        console.print("[green]✅ Configuration reset[/green]")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


# ============================================================================
# GLOBAL MEMORY COMMAND
# ============================================================================

@app.command("memory")
def memory_command(
    set_key: Optional[str] = typer.Option(None, "--set", help="Key to set (e.g. preferred_stack)"),
    value: Optional[str] = typer.Option(None, "--value", help="Value to store"),
    show: bool = typer.Option(False, "--show", help="Print all global memory"),
) -> None:
    """View or update cross-session global memory (user preferences).

    Examples:
        codepilot memory --show
        codepilot memory --set preferred_stack --value "React + FastAPI"
        codepilot memory --set coding_style --value modular
    """
    if set_key and value is not None:
        GlobalMemory.set(set_key, value)
        console.print(f"[green]✓ Set[/green] [cyan]{set_key}[/cyan] = {value}")
        return

    data = GlobalMemory.load()
    if not any(v for v in data.values()):
        console.print(
            "[yellow]No global memory set.[/yellow]\n"
            "Example: [cyan]codepilot memory --set preferred_stack --value 'React + FastAPI'[/cyan]"
        )
        return

    table = Table(title="Global Memory", show_lines=False)
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")
    for k, v in data.items():
        if v:
            table.add_row(k, str(v)[:80])
    console.print(table)


# ============================================================================
# INFO COMMANDS
# ============================================================================

@app.command("version")
def version_command() -> None:
    """Show version information."""
    from . import __version__
    
    console.print(f"[cyan]{APP_NAME}[/cyan] v{__version__}")


@app.command("info")
def info_command() -> None:
    """Show installation and paths."""
    from . import __version__
    from .utils.constants import LOGS_DIR, SESSIONS_DIR
    
    console.print(Panel(f"[bold cyan]{APP_NAME} v{__version__}[/bold cyan]\n{APP_TAGLINE}"))
    
    console.print("\n[bold]Paths:[/bold]")
    console.print(f"  Config: {CONFIG_FILE}")
    console.print(f"  Sessions: {SESSIONS_DIR}")
    console.print(f"  Logs: {LOGS_DIR}")
    
    # Check config
    config_manager = ConfigManager()
    if config_manager.exists:
        console.print("\n[green]✅ Configuration found[/green]")
        config = config_manager.config
        console.print(f"  Provider: {config.llm.provider}")
        console.print(f"  Model: {config.llm.model}")
        
        # Show GitHub integration status
        if config.github.token:
            console.print("  GitHub: [green]✓ Configured[/green] (MCP server enabled)")
        else:
            console.print("  GitHub: [dim]Not configured[/dim] (local git commands available)")
    else:
        console.print("\n[yellow]⚠️  No configuration - run: codepilot config init[/yellow]")


def main() -> None:
    """Main CLI entry point."""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]Fatal error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
