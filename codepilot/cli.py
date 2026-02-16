"""Clean CLI interface for CodePilot."""

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .config import ConfigManager
from .core import CodePilotError, ConfigurationError, create_codepilot_agent
from .core.session import SessionManager
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
    
    # Optional GitHub token
    console.print("\n[cyan]GitHub Integration (Optional):[/cyan]")
    console.print("[dim]Only needed if you want CodePilot to push code to GitHub[/dim]")
    console.print("[dim]Local git commands will work without this[/dim]\n")
    
    if Confirm.ask("Configure GitHub token?", default=False):
        github_token = Prompt.ask("Enter your GitHub token", password=True)
    
    # Create configuration
    try:
        # Prepare kwargs
        create_kwargs = {}
        if provider == PROVIDER_OPENROUTER and 'api_key_kwargs' in locals():
            create_kwargs.update(api_key_kwargs)
        
        config_mgr.create(
            provider=provider,
            model=model,
            api_key=api_key,
            **create_kwargs
        )
        
        # Set GitHub token if provided
        if github_token:
            config = config_mgr.config
            config.github.token = github_token
            config_mgr._save()
        
        console.print("\n[green]✓ Configuration created successfully![/green]")
        return True
    except Exception as e:
        console.print(f"\n[red]✗ Failed to create configuration: {e}[/red]")
        return False


# ============================================================================
# MAIN COMMAND
# ============================================================================

@app.command(name="run")
def run_command(
    task: Optional[str] = typer.Argument(None, help="Task to execute (omit for interactive mode)"),
    project: str = typer.Option(".", "--project", "-p", help="Project directory"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug mode"),
) -> None:
    """Execute a task or start interactive session.
    
    Examples:
        codepilot run                              # Interactive mode
        codepilot run "create a hello world app"   # Single task
        codepilot run "build API" --project ./api  # Specific project
    """
    if debug:
        enable_debug_mode()
    
    try:
        # Show banner
        show_banner()
        
        # Load configuration or create if missing
        config_manager = ConfigManager()
        if not config_manager.exists:
            if not interactive_config_setup():
                raise typer.Exit(1)
        
        # Display provider/model info
        config = config_manager.config
        provider = config.llm.active_provider
        model = config.llm.active_model
        console.print(f"[dim]Provider: {provider} | Model: {model}[/dim]\n")
        
        # Create agent
        agent = create_codepilot_agent(config_manager, project)
        
        # Execute task or start interactive
        if task:
            console.print(f"\n[cyan]Executing:[/cyan] {task}\n")
            agent.run(task)
            console.print("\n[green]✅ Done[/green]")
        else:
            console.print("\n[dim]Type 'exit' or 'quit' to end session[/dim]\n")
            agent.run_interactive()
    
    except ConfigurationError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise typer.Exit(1)
    
    except CodePilotError as e:
        console.print(f"[red]Error:[/red] {e}")
        if debug:
            raise
        raise typer.Exit(1)
    
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        raise typer.Exit(0)
    
    except Exception as e:
        console.print(f"[red]Unexpected error:[/red] {e}")
        if debug:
            raise
        raise typer.Exit(1)


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
                console.print("  3. Update GitHub token (optional)")
                console.print("  4. Set provider preference")
                console.print("  5. Manage OpenRouter models")
                console.print("  6. Reset to defaults")
                console.print("  7. Delete configuration")
                console.print("  8. Exit")
                
                choice = Prompt.ask(
                    "Select option",
                    choices=["1", "2", "3", "4", "5", "6", "7", "8"],
                    default="8"
                )
                
                if choice == "8":
                    console.print("\n[green]Configuration saved![/green]")
                    return
                elif choice == "7":
                    if Confirm.ask("Are you sure you want to delete the configuration?", default=False):
                        config_manager.config_path.unlink()
                        console.print("[green]✓ Configuration deleted[/green]")
                    return
                elif choice == "6":
                    if Confirm.ask("Reset configuration to defaults?", default=False):
                        config_manager.config_path.unlink()
                        config_manager.create(provider=PROVIDER_OLLAMA, model=OLLAMA_DEFAULT_MODEL)
                        console.print("[green]✓ Configuration reset to defaults (Ollama/mistral)[/green]")
                    return
                elif choice == "5":
                    # Manage OpenRouter models
                    _manage_models(config_manager)
                    console.print()  # Empty line for spacing
                elif choice == "4":
                    # Set provider preference
                    _update_provider_preference(config_manager)
                    console.print()  # Empty line for spacing
                elif choice == "3":
                    # Update GitHub token
                    _update_github_token(config_manager)
                    console.print()  # Empty line for spacing
                elif choice in ["1", "2"]:
                    # Update provider configuration
                    provider = PROVIDER_OPENROUTER if choice == "1" else PROVIDER_OLLAMA
                    _update_provider_config(config_manager, provider)
                    console.print()  # Empty line for spacing
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
    config = config_manager.config
    if Confirm.ask("Set GitHub token?", default=True):
        token = Prompt.ask("Enter GitHub token", password=True)
        config.github.token = token
        config_manager._save()
        console.print("[green]✓ GitHub token updated[/green]")
    else:
        config.github.token = None
        config_manager._save()
        console.print("[green]✓ GitHub token removed[/green]")


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
# SESSION COMMANDS
# ============================================================================

@app.command("sessions")
def sessions_command(
    delete: Optional[str] = typer.Option(None, "--delete", "-d", help="Delete session by ID"),
    clear: bool = typer.Option(False, "--clear", help="Clear all sessions"),
) -> None:
    """View and manage sessions.
    
    Examples:
        codepilot sessions                  # List all sessions
        codepilot sessions --delete abc123  # Delete specific session
        codepilot sessions --clear          # Clear all sessions
    """
    try:
        if clear:
            if typer.confirm("⚠️  Delete all sessions?"):
                count = SessionManager.clear_all_sessions()
                console.print(f"[green]✅ Deleted {count} sessions[/green]")
            return
        
        if delete:
            if SessionManager.delete_session(delete):
                console.print(f"[green]✅ Deleted session {delete[:8]}[/green]")
            else:
                console.print(f"[red]Session not found[/red]")
            return
        
        # List sessions
        sessions = SessionManager.list_sessions()
        
        if not sessions:
            console.print("[yellow]No sessions found[/yellow]")
            return
        
        table = Table(title="Sessions")
        table.add_column("ID", style="cyan")
        table.add_column("Tasks", style="green")
        table.add_column("Date", style="dim")
        
        for session in sessions[:10]:  # Show last 10
            session_id = session["session_id"][:8]
            task_count = len(session.get("tasks", []))
            created = session["created_at"][:10]
            
            table.add_row(session_id, str(task_count), created)
        
        console.print(table)
        console.print(f"\n[dim]Showing {min(10, len(sessions))} of {len(sessions)} sessions[/dim]")
    
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


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
    from .utils.constants import CONFIG_DIR, LOGS_DIR, SESSIONS_DIR
    
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
