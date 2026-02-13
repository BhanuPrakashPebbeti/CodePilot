"""Permission & security model for CodePilot.

Classifies commands as SAFE, NEEDS_PERMISSION, or BLOCKED.
Provides interactive permission prompts for dangerous operations.

Safety tiers:
  SAFE              — pip/npm install project deps, build commands, linters
  NEEDS_PERMISSION  — system packages, runtimes, sudo, port opening, docker
  BLOCKED           — rm -rf /, format disk, etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Set

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()


class PermissionLevel(str, Enum):
    SAFE = "safe"
    NEEDS_PERMISSION = "needs_permission"
    BLOCKED = "blocked"


@dataclass
class PermissionDecision:
    level: PermissionLevel
    reason: str
    command: str
    approved: bool = False


class PermissionGate:
    """Interactive permission system for command execution.
    
    Usage:
        gate = PermissionGate()
        decision = gate.check("sudo apt install nodejs")
        if decision.level == PermissionLevel.NEEDS_PERMISSION:
            decision = gate.prompt(decision)
        if decision.approved:
            # execute
    """

    def __init__(self) -> None:
        self._session_allows: Set[str] = set()  # "always allow" patterns for session
        self._session_denies: Set[str] = set()

    # ------------------------------------------------------------------
    # CLASSIFICATION RULES
    # ------------------------------------------------------------------

    # Patterns that are always safe (project-level operations)
    _SAFE_PATTERNS = [
        r"^pip\s+install\b",
        r"^pip3\s+install\b",
        r"^python\s+-m\s+pip\s+install\b",
        r"^npm\s+install\b",
        r"^npm\s+ci\b",
        r"^npm\s+run\b",
        r"^npm\s+start\b",
        r"^npm\s+test\b",
        r"^npm\s+init\b",
        r"^npx\s+",
        r"^yarn\s+(add|install|run|build|test|start)\b",
        r"^pnpm\s+(add|install|run|build|test|start)\b",
        r"^cargo\s+(build|run|test|check|clippy|fmt)\b",
        r"^go\s+(build|run|test|get|mod)\b",
        r"^mvn\s+(compile|test|package|install)\b",
        r"^gradle\s+(build|test|run)\b",
        r"^make\b",
        r"^cmake\b",
        r"^python\b",
        r"^python3\b",
        r"^node\b",
        r"^tsc\b",
        r"^gcc\b",
        r"^g\+\+\b",
        r"^javac\b",
        r"^java\b",
        r"^rustc\b",
        r"^pytest\b",
        r"^python\s+-m\s+pytest\b",
        r"^ruff\b",
        r"^flake8\b",
        r"^black\b",
        r"^isort\b",
        r"^mypy\b",
        r"^eslint\b",
        r"^prettier\b",
        r"^cat\b",
        r"^echo\b",
        r"^ls\b",
        r"^find\b",
        r"^grep\b",
        r"^head\b",
        r"^tail\b",
        r"^wc\b",
        r"^diff\b",
        r"^which\b",
        r"^whoami\b",
        r"^pwd\b",
        r"^env\b",
        r"^printenv\b",
        r"^uname\b",
        r"^date\b",
        r"^lsof\b",
        r"^ps\b",
        r"^git\b",
        r"^mkdir\b",
        r"^touch\b",
        r"^cp\b",
        r"^mv\b",
        r"^curl\s+.*--head\b",  # curl HEAD only (safe)
        r"^wget\s+.*--spider\b",  # wget check only
    ]

    # Patterns that ALWAYS require permission
    _PERMISSION_PATTERNS = [
        (r"\bsudo\b", "Requires superuser privileges"),
        (r"\bapt\s+(install|remove|purge|update|upgrade)\b", "System package manager"),
        (r"\bapt-get\s+(install|remove|purge|update|upgrade)\b", "System package manager"),
        (r"\bdpkg\s+-i\b", "System package installation"),
        (r"\bpacman\s+(-S|--sync|-R|--remove)\b", "System package manager"),
        (r"\byum\s+(install|remove|update)\b", "System package manager"),
        (r"\bdnf\s+(install|remove|update)\b", "System package manager"),
        (r"\bbrew\s+(install|uninstall|upgrade)\b", "Homebrew package manager"),
        (r"\bsnap\s+install\b", "Snap package installation"),
        (r"\bflatpak\s+install\b", "Flatpak package installation"),
        (r"\bnvm\s+install\b", "Node.js version manager"),
        (r"\bpyenv\s+install\b", "Python version manager"),
        (r"\bsdkman\s+install\b", "SDK manager"),
        (r"\brustup\b", "Rust toolchain manager"),
        (r"\bcurl\s+.*\|\s*(sudo\s+)?bash\b", "Piped remote script execution"),
        (r"\bcurl\s+.*\|\s*(sudo\s+)?sh\b", "Piped remote script execution"),
        (r"\bwget\s+.*\|\s*(sudo\s+)?bash\b", "Piped remote script execution"),
        (r"\bdocker\s+(run|build|pull|push|exec|compose)\b", "Docker operation"),
        (r"\bsystemctl\b", "System service management"),
        (r"\bservice\b", "System service management"),
        (r"\bchmod\s+[0-7]*[7]\b", "Setting executable/world permissions"),
        (r"\bchown\b", "Changing file ownership"),
        (r"\bufw\b", "Firewall configuration"),
        (r"\biptables\b", "Firewall configuration"),
        (r"\bnetstat\b", "Network inspection"),
        (r"\bss\s+-", "Network socket inspection"),
        (r"\bkill\b", "Killing processes"),
        (r"\bpkill\b", "Killing processes"),
        (r"\bkillall\b", "Killing processes"),
        (r"\bshutdown\b", "System shutdown"),
        (r"\breboot\b", "System reboot"),
        (r"\bmount\b", "Filesystem mount"),
        (r"\bumount\b", "Filesystem unmount"),
        (r"\bdd\s+", "Raw disk write"),
        (r"\bmkfs\b", "Filesystem creation"),
        (r"\bfdisk\b", "Disk partitioning"),
        (r"\bparted\b", "Disk partitioning"),
    ]

    # Patterns that are BLOCKED (never allowed)
    _BLOCKED_PATTERNS = [
        (r"rm\s+-rf\s+/\s*$", "Destructive: recursive delete of root filesystem"),
        (r"rm\s+-rf\s+/\*", "Destructive: recursive delete of root filesystem"),
        (r"rm\s+-rf\s+~\s*$", "Destructive: recursive delete of home directory"),
        (r":\(\)\{\s*:\|:&\s*\};:", "Fork bomb"),
        (r"mkfs\s+/dev/[sh]d[a-z]", "Destructive: formatting disk"),
        (r"dd\s+.*of=/dev/[sh]d[a-z]", "Destructive: raw disk overwrite"),
        (r">\s*/dev/[sh]d[a-z]", "Destructive: overwriting disk device"),
        (r"mv\s+/\s+/dev/null", "Destructive: moving root to null"),
    ]

    def check(self, command: str) -> PermissionDecision:
        """Classify a command and return a permission decision."""
        cmd = command.strip()

        # Check blocked first
        for pattern, reason in self._BLOCKED_PATTERNS:
            if re.search(pattern, cmd):
                return PermissionDecision(
                    level=PermissionLevel.BLOCKED,
                    reason=f"🚫 {reason}",
                    command=cmd,
                    approved=False,
                )

        # Check session allows
        for allowed_pattern in self._session_allows:
            if re.search(allowed_pattern, cmd):
                return PermissionDecision(
                    level=PermissionLevel.SAFE,
                    reason="Session-allowed",
                    command=cmd,
                    approved=True,
                )

        # Check session denies
        for denied_pattern in self._session_denies:
            if re.search(denied_pattern, cmd):
                return PermissionDecision(
                    level=PermissionLevel.NEEDS_PERMISSION,
                    reason="Previously denied",
                    command=cmd,
                    approved=False,
                )

        # Check needs-permission
        for pattern, reason in self._PERMISSION_PATTERNS:
            if re.search(pattern, cmd):
                return PermissionDecision(
                    level=PermissionLevel.NEEDS_PERMISSION,
                    reason=reason,
                    command=cmd,
                    approved=False,
                )

        # Check safe patterns
        for pattern in self._SAFE_PATTERNS:
            if re.search(pattern, cmd):
                return PermissionDecision(
                    level=PermissionLevel.SAFE,
                    reason="Safe operation",
                    command=cmd,
                    approved=True,
                )

        # Unknown commands default to safe (they're in a sandboxed shell)
        return PermissionDecision(
            level=PermissionLevel.SAFE,
            reason="Default allow",
            command=cmd,
            approved=True,
        )

    def prompt(self, decision: PermissionDecision) -> PermissionDecision:
        """Interactively prompt user for permission."""
        console.print()
        console.print(
            Panel(
                f"[bold yellow]⚠  Permission Required[/bold yellow]\n\n"
                f"[bold]Command:[/bold]  [cyan]{decision.command}[/cyan]\n"
                f"[bold]Reason:[/bold]   {decision.reason}\n\n"
                f"[dim]The agent wants to run a command that modifies your system.[/dim]",
                border_style="yellow",
                padding=(1, 2),
            )
        )

        console.print("  [bold]Options:[/bold]")
        console.print("    [green]y[/green]  — Allow this once")
        console.print("    [cyan]a[/cyan]  — Always allow this type for session")
        console.print("    [red]n[/red]  — Deny")
        console.print("    [dim]s[/dim]  — Show full command")
        console.print()

        while True:
            choice = Prompt.ask(
                "  Allow?",
                choices=["y", "a", "n", "s"],
                default="n",
            ).lower()

            if choice == "s":
                console.print(f"\n  [cyan]Full command:[/cyan]\n  {decision.command}\n")
                continue
            elif choice == "y":
                decision.approved = True
                console.print("  [green]✓ Allowed[/green]\n")
                return decision
            elif choice == "a":
                decision.approved = True
                # Build a pattern from the command's first word(s)
                first_words = decision.command.split()[:2]
                pattern = r"\b" + r"\s+".join(re.escape(w) for w in first_words) + r"\b"
                self._session_allows.add(pattern)
                console.print("  [green]✓ Allowed for session[/green]\n")
                return decision
            elif choice == "n":
                decision.approved = False
                first_words = decision.command.split()[:2]
                pattern = r"\b" + r"\s+".join(re.escape(w) for w in first_words) + r"\b"
                self._session_denies.add(pattern)
                console.print("  [red]✗ Denied[/red]\n")
                return decision

    def check_and_prompt(self, command: str) -> PermissionDecision:
        """Convenience: check + prompt if needed."""
        decision = self.check(command)

        if decision.level == PermissionLevel.BLOCKED:
            console.print(f"\n  [red bold]🚫 BLOCKED:[/red bold] {decision.reason}")
            console.print(f"  [dim]{decision.command}[/dim]\n")
            return decision

        if decision.level == PermissionLevel.NEEDS_PERMISSION:
            return self.prompt(decision)

        decision.approved = True
        return decision
