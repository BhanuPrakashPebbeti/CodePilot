"""Configuration data models."""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class APIKey(BaseModel):
    """Single API key configuration."""
    key: str = Field(..., min_length=10)
    label: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    last_used: Optional[datetime] = None
    usage_count: int = 0
    is_active: bool = True

    def mark_used(self) -> None:
        """Mark key as used."""
        self.last_used = datetime.now()
        self.usage_count += 1


class LLMConfig(BaseModel):
    """LLM provider configuration."""
    provider: str = Field(..., pattern="^(openrouter|ollama)$")
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=100, le=100000)  # Increased for complex tasks
    base_url: Optional[str] = None
    provider_preference: Optional[str] = Field(default=None, pattern="^(openrouter|ollama)?$")  # User's preferred provider
    openrouter_models: List[str] = Field(default_factory=list)  # OpenRouter models list
    
    # Per-provider model storage (so editing one doesn't clobber the other)
    openrouter_model: Optional[str] = None   # last-selected OpenRouter model
    ollama_model: Optional[str] = None       # last-selected Ollama model
    
    # Single key mode
    api_key: Optional[str] = None
    
    # Multi-key mode
    api_keys: List[APIKey] = Field(default_factory=list)
    
    @property
    def active_provider(self) -> str:
        """The provider that should actually be used, based on preference."""
        if self.provider_preference:
            return self.provider_preference
        # Auto: prefer openrouter if key exists, else ollama
        if self.has_any_key:
            return "openrouter"
        return "ollama"
    
    @property
    def active_model(self) -> str:
        """The model for the active provider."""
        prov = self.active_provider
        if prov == "openrouter" and self.openrouter_model:
            return self.openrouter_model
        if prov == "ollama" and self.ollama_model:
            return self.ollama_model
        # Fallback to legacy 'model' field
        return self.model
    
    @property
    def has_single_key(self) -> bool:
        """Check if single key mode."""
        return bool(self.api_key)
    
    @property
    def has_multiple_keys(self) -> bool:
        """Check if multiple key mode."""
        return len(self.api_keys) > 0
    
    @property
    def has_any_key(self) -> bool:
        """Check if any key configured."""
        return self.has_single_key or self.has_multiple_keys


class GitHubConfig(BaseModel):
    """GitHub integration configuration."""
    token: Optional[str] = None
    username: Optional[str] = None
    auto_commit: bool = False


class NotionConfig(BaseModel):
    """Notion integration — per-project Jira-like databases.

    Databases are created fresh per project by notion_setup_project() at the
    start of each session. No global databases are needed in config.

    Required env vars (read directly by notion_tools.py):
      NOTION_TOKEN          — integration token from notion.so/profile/integrations
      NOTION_PARENT_PAGE_ID — page to create project root pages under
    """
    token: Optional[str] = None            # Integration token
    parent_page_id: Optional[str] = None   # Page to create project pages under


class SlackConfig(BaseModel):
    """Slack integration — notifications and HITL decisions.

    Required env vars (read directly by slack_hitl.py):
      SLACK_BOT_TOKEN — xoxb-... bot token
      SLACK_CHANNEL   — channel to post to (e.g. "#codepilot")
    """
    bot_token: Optional[str] = None        # xoxb-... bot token (also set as SLACK_BOT_TOKEN)
    channel: Optional[str] = "#codepilot"  # Default channel for notifications and HITL


class AppConfig(BaseModel):
    """Main application configuration."""
    llm: LLMConfig
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    notion: NotionConfig = Field(default_factory=NotionConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    work_dir: Path = Field(default_factory=lambda: Path.home() / "codepilot_projects")
    version: str = "2.0.0"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    
    @field_validator("work_dir", mode="before")
    @classmethod
    def convert_work_dir(cls, v):
        """Convert work_dir to Path."""
        if isinstance(v, str):
            return Path(v)
        return v
    
    class Config:
        """Pydantic config."""
        json_encoders = {
            Path: str,
            datetime: lambda v: v.isoformat(),
        }
