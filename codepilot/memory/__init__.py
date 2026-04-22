"""Memory package — persistent cross-session context for CodePilot agents.

Provides two complementary memory systems:

SqliteMemoryService
    ADK ``BaseMemoryService`` implementation.  The ADK Runner calls this
    automatically at the end of each session to persist all conversation
    events.  Enables keyword-based search across past sessions.

memory_server (MCP)
    FastMCP server that agents call *explicitly* to store and retrieve
    structured, typed memories: conversation summaries, project notes,
    error→fix patterns, and user preferences.
"""

from .service import SqliteMemoryService

__all__ = ["SqliteMemoryService"]
