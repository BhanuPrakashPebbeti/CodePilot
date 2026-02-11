"""Logging utilities for CodePilot."""

import logging
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

from .constants import LOGS_DIR

# Global console
console = Console()

# Logger cache
_loggers = {}


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Get or create a logger.
    
    Args:
        name: Logger name.
        level: Logging level.
    
    Returns:
        Configured logger.
    """
    if name in _loggers:
        return _loggers[name]
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Rich console handler (for user-facing output)
    console_handler = RichHandler(
        console=console,
        rich_tracebacks=False,  # Don't show tracebacks by default
        show_level=False,
        show_path=False,
        show_time=False,
    )
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    
    logger.addHandler(console_handler)
    
    # Cache logger
    _loggers[name] = logger
    
    return logger


def enable_debug_mode() -> None:
    """Enable debug mode with file logging and tracebacks."""
    # Ensure logs directory exists
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Update all loggers to DEBUG
    for logger in _loggers.values():
        logger.setLevel(logging.DEBUG)
        
        # Add file handler
        file_handler = logging.FileHandler(LOGS_DIR / "codepilot.log")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
        )
        logger.addHandler(file_handler)
        
        # Enable rich tracebacks
        for handler in logger.handlers:
            if isinstance(handler, RichHandler):
                handler.rich_tracebacks = True
                handler.show_level = True
                handler.show_path = True
    
    console.print("[yellow]🐛 Debug mode enabled[/yellow]")
    console.print(f"[dim]Logs: {LOGS_DIR / 'codepilot.log'}[/dim]")


def disable_debug_mode() -> None:
    """Disable debug mode."""
    for logger in _loggers.values():
        logger.setLevel(logging.INFO)
        
        # Remove file handlers
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)
        
        # Disable rich tracebacks
        for handler in logger.handlers:
            if isinstance(handler, RichHandler):
                handler.rich_tracebacks = False
                handler.show_level = False
                handler.show_path = False
