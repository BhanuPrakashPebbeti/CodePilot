"""Workspace selection for new project sessions."""

import re
from pathlib import Path

from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from ..utils.logger import get_logger

logger = get_logger(__name__)


def select_workspace() -> Path:
    """Interactively select and lock the workspace directory.

    Shows the current directory and asks the user to confirm or choose a
    different path.  Returns an absolute resolved Path that will be the
    locked workspace for the entire session.

    Raises:
        SystemExit: if the user cancels during selection.
    """
    from ..core.renderer import console  # local import to avoid circular

    cwd = Path.cwd().resolve()

    console.print()
    console.print(
        Panel(
            f"[bold]📁 Workspace Selection[/bold]\n\n"
            f"[cyan]Current directory:[/cyan] {cwd}\n\n"
            "[dim]CodePilot will operate ONLY within the selected workspace.\n"
            "This cannot be changed mid-session without explicit confirmation.[/dim]",
            border_style="cyan",
            padding=(0, 1),
        )
    )

    use_cwd = Confirm.ask(
        f"Use [cyan]{cwd}[/cyan] as the project workspace?",
        default=True,
    )

    if use_cwd:
        workspace = cwd
    else:
        workspace = _prompt_alternate(cwd)

    workspace.mkdir(parents=True, exist_ok=True)

    console.print(
        f"\n[green]✓ Workspace locked:[/green] [bold]{workspace}[/bold]\n"
        "[dim]All file and execution operations are confined to this directory.[/dim]\n"
    )
    logger.info("Workspace locked: %s", workspace)
    return workspace


def _prompt_alternate(cwd: Path) -> Path:
    """Sub-menu: enter an existing path or create a new project folder."""
    from ..core.renderer import console

    console.print("\n[bold]Choose workspace:[/bold]")
    console.print("  [cyan]1.[/cyan] Enter an existing directory path")
    console.print("  [cyan]2.[/cyan] Create a new project folder here")

    choice = Prompt.ask("Select", choices=["1", "2"], default="2")

    if choice == "1":
        while True:
            raw = Prompt.ask("Directory path").strip()
            p = Path(raw).expanduser().resolve()
            if p.is_dir():
                return p
            console.print(f"[red]Directory not found:[/red] {p}")
            if not Confirm.ask("Try again?", default=True):
                raise SystemExit(0)
    else:
        name = Prompt.ask("New project folder name").strip()
        if not name:
            name = "my_project"
        # Sanitise to safe directory name
        name = re.sub(r"[^\w\-]", "_", name).strip("_") or "my_project"
        workspace = cwd / name
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace
