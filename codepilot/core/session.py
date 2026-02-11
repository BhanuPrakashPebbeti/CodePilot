"""Session management for tracking work."""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.constants import SESSIONS_DIR
from ..utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Task:
    """Individual task in a session."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    status: str = "pending"  # pending, in_progress, completed, failed
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }


@dataclass
class Session:
    """Session tracking."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    project_dir: str = "."
    tasks: List[Task] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "project_dir": self.project_dir,
            "tasks": [task.to_dict() for task in self.tasks],
        }


class SessionManager:
    """Manages coding sessions."""
    
    def __init__(self, project_dir: str = "."):
        """Initialize session manager.
        
        Args:
            project_dir: Project directory path.
        """
        self.project_dir = project_dir
        self.session: Optional[Session] = None
        
        # Ensure sessions directory exists
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    
    def start_session(self) -> Session:
        """Start a new session.
        
        Returns:
            New session object.
        """
        self.session = Session(project_dir=str(self.project_dir))
        self._save()
        logger.debug(f"Started session: {self.session.session_id[:8]}")
        return self.session
    
    def end_session(self) -> None:
        """End current session."""
        if self.session:
            self.session.ended_at = datetime.now()
            self._save()
            logger.debug(f"Ended session: {self.session.session_id[:8]}")
    
    def add_task(self, title: str) -> Task:
        """Add a task to current session.
        
        Args:
            title: Task title.
        
        Returns:
            Created task.
        """
        if not self.session:
            self.start_session()
        
        task = Task(title=title)
        self.session.tasks.append(task)
        self._save()
        return task
    
    def start_task(self, task_id: str) -> None:
        """Mark task as started.
        
        Args:
            task_id: Task ID to start.
        """
        task = self._find_task(task_id)
        if task:
            task.status = "in_progress"
            task.started_at = datetime.now()
            self._save()
    
    def complete_task(self, task_id: str) -> None:
        """Mark task as completed.
        
        Args:
            task_id: Task ID to complete.
        """
        task = self._find_task(task_id)
        if task:
            task.status = "completed"
            task.completed_at = datetime.now()
            self._save()
    
    def fail_task(self, task_id: str, error: str) -> None:
        """Mark task as failed.
        
        Args:
            task_id: Task ID to fail.
            error: Error message.
        """
        task = self._find_task(task_id)
        if task:
            task.status = "failed"
            task.completed_at = datetime.now()
            task.error = error
            self._save()
    
    def get_summary(self) -> dict:
        """Get session summary.
        
        Returns:
            Summary dictionary.
        """
        if not self.session:
            return {}
        
        completed = sum(1 for t in self.session.tasks if t.status == "completed")
        failed = sum(1 for t in self.session.tasks if t.status == "failed")
        
        return {
            "session_id": self.session.session_id,
            "total_tasks": len(self.session.tasks),
            "completed": completed,
            "failed": failed,
            "in_progress": len(self.session.tasks) - completed - failed,
        }
    
    def _find_task(self, task_id: str) -> Optional[Task]:
        """Find task by ID.
        
        Args:
            task_id: Task ID.
        
        Returns:
            Task if found, None otherwise.
        """
        if not self.session:
            return None
        
        for task in self.session.tasks:
            if task.task_id == task_id:
                return task
        
        return None
    
    def _save(self) -> None:
        """Save session to disk."""
        if not self.session:
            return
        
        try:
            session_file = SESSIONS_DIR / f"{self.session.session_id}.json"
            with open(session_file, "w") as f:
                json.dump(self.session.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
    
    @staticmethod
    def list_sessions() -> List[dict]:
        """List all sessions.
        
        Returns:
            List of session summaries.
        """
        sessions = []
        
        if not SESSIONS_DIR.exists():
            return sessions
        
        for session_file in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
            try:
                with open(session_file) as f:
                    data = json.load(f)
                    sessions.append(data)
            except Exception as e:
                logger.error(f"Failed to read session {session_file}: {e}")
        
        return sessions
    
    @staticmethod
    def delete_session(session_id: str) -> bool:
        """Delete a session.
        
        Args:
            session_id: Session ID to delete.
        
        Returns:
            True if deleted, False otherwise.
        """
        session_file = SESSIONS_DIR / f"{session_id}.json"
        
        if session_file.exists():
            try:
                session_file.unlink()
                logger.debug(f"Deleted session: {session_id[:8]}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete session: {e}")
        
        return False
    
    @staticmethod
    def clear_all_sessions() -> int:
        """Clear all sessions.
        
        Returns:
            Number of sessions deleted.
        """
        count = 0
        
        if not SESSIONS_DIR.exists():
            return count
        
        try:
            for session_file in SESSIONS_DIR.glob("*.json"):
                session_file.unlink()
                count += 1
            logger.debug(f"Cleared {count} sessions")
        except Exception as e:
            logger.error(f"Failed to clear sessions: {e}")
        
        return count
