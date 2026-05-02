"""Configuration manager - handles all config operations."""

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .models import APIKey, AppConfig, GitHubConfig, LLMConfig, NotionConfig, SlackConfig
from .keys import APIKeyRotator
from ..core.exceptions import ConfigurationError
from ..utils.constants import CONFIG_DIR, CONFIG_FILE, PROVIDER_OPENROUTER
from ..utils.logger import get_logger

logger = get_logger(__name__)


class ConfigManager:
    """Manages persistent configuration with validation."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize config manager.
        
        Args:
            config_path: Optional custom config file path.
        """
        self.config_path = config_path or CONFIG_FILE
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config: Optional[AppConfig] = None
        self._key_rotator: Optional[APIKeyRotator] = None
        self._load()
    
    def _load(self) -> None:
        """Load configuration from disk."""
        if not self.config_path.exists():
            logger.debug("No configuration file found")
            return
        
        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)
            
            self._config = AppConfig.model_validate(data)
            
            # Initialize key rotator if keys configured
            if self._config.llm.has_any_key:
                self._key_rotator = APIKeyRotator(self._config.llm)
            
            logger.debug(f"Loaded configuration from {self.config_path}")
        
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error(f"Invalid configuration file: {e}")
            raise ConfigurationError(f"Failed to load config: {e}")
    
    def _save(self) -> None:
        """Save configuration to disk (atomic write)."""
        if not self._config:
            raise ConfigurationError("No configuration to save")
        
        try:
            # Atomic write using temp file
            temp_path = self.config_path.with_suffix(".tmp")
            
            with open(temp_path, "w") as f:
                json.dump(
                    self._config.model_dump(mode="json"),
                    f,
                    indent=2,
                    default=str
                )
            
            # Atomic rename
            temp_path.replace(self.config_path)
            logger.debug(f"Saved configuration to {self.config_path}")
        
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise ConfigurationError(f"Failed to save config: {e}")
    
    @property
    def exists(self) -> bool:
        """Check if configuration exists."""
        return self._config is not None
    
    @property
    def config(self) -> AppConfig:
        """Get current configuration.
        
        Returns:
            Application configuration.
            
        Raises:
            ConfigurationError: If no config exists.
        """
        if not self._config:
            raise ConfigurationError("No configuration found. Run: codepilot config init")
        return self._config
    
    def create(
        self,
        provider: str,
        model: str,
        api_key: Optional[str] = None,
        **kwargs
    ) -> AppConfig:
        """Create new configuration.
        
        Args:
            provider: LLM provider (openrouter or ollama).
            model: Model name.
            api_key: Optional API key for provider.
            **kwargs: Additional config options.
        
        Returns:
            Created configuration.
        """
        llm_config = LLMConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            **kwargs
        )
        
        self._config = AppConfig(llm=llm_config)
        
        # Initialize key rotator
        if llm_config.has_any_key:
            self._key_rotator = APIKeyRotator(llm_config)
        
        self._save()
        logger.info("✅ Configuration created")
        return self._config
    
    def update_llm(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs
    ) -> None:
        """Update LLM configuration.
        
        Args:
            provider: Optional new provider.
            model: Optional new model.
            api_key: Optional new API key.
            **kwargs: Additional options.
        """
        if not self._config:
            raise ConfigurationError("No configuration exists")
        
        if provider:
            self._config.llm.provider = provider
        if model:
            self._config.llm.model = model
        if api_key:
            self._config.llm.api_key = api_key
        
        for key, value in kwargs.items():
            if hasattr(self._config.llm, key):
                setattr(self._config.llm, key, value)
        
        # Reinitialize key rotator
        if self._config.llm.has_any_key:
            self._key_rotator = APIKeyRotator(self._config.llm)
        
        self._save()
        logger.info("✅ LLM configuration updated")
    
    def add_api_key(self, key: str, label: Optional[str] = None) -> None:
        """Add API key for rotation.
        
        Args:
            key: API key value.
            label: Optional label for the key.
        """
        if not self._config:
            raise ConfigurationError("No configuration exists")
        
        # If single key mode, convert to multi-key
        if self._config.llm.has_single_key and not self._config.llm.has_multiple_keys:
            existing_key = APIKey(key=self._config.llm.api_key, label="Primary")
            self._config.llm.api_keys.append(existing_key)
            self._config.llm.api_key = None
        
        # Add new key
        new_key = APIKey(key=key, label=label)
        self._config.llm.api_keys.append(new_key)
        
        # Reinitialize rotator
        self._key_rotator = APIKeyRotator(self._config.llm)
        
        self._save()
        logger.info(f"✅ Added API key: {label or key[:8]}...")
    
    def remove_api_key(self, identifier: str) -> None:
        """Remove API key by label or key prefix.
        
        Args:
            identifier: Label or key prefix to remove.
        """
        if not self._config:
            raise ConfigurationError("No configuration exists")
        
        if not self._config.llm.has_multiple_keys:
            raise ConfigurationError("No multiple keys configured")
        
        # Find and remove key
        original_count = len(self._config.llm.api_keys)
        self._config.llm.api_keys = [
            k for k in self._config.llm.api_keys
            if not (k.label == identifier or k.key.startswith(identifier))
        ]
        
        removed = original_count - len(self._config.llm.api_keys)
        if removed == 0:
            raise ConfigurationError(f"No key found matching: {identifier}")
        
        # Reinitialize rotator
        if self._config.llm.api_keys:
            self._key_rotator = APIKeyRotator(self._config.llm)
        else:
            self._key_rotator = None
        
        self._save()
        logger.info(f"✅ Removed {removed} API key(s)")
    
    def list_api_keys(self) -> list:
        """List all configured API keys (masked).
        
        Returns:
            List of key info dicts.
        """
        if not self._config:
            return []
        
        # Single key mode
        if self._config.llm.has_single_key and not self._config.llm.has_multiple_keys:
            return [{
                "label": "Primary",
                "key": self._config.llm.api_key[:8] + "...",
                "mode": "single"
            }]
        
        # Multi-key mode
        return [
            {
                "label": key.label or f"Key {i+1}",
                "key": key.key[:8] + "...",
                "active": key.is_active,
                "usage": key.usage_count,
                "last_used": key.last_used.isoformat() if key.last_used else "Never"
            }
            for i, key in enumerate(self._config.llm.api_keys)
        ]
    
    def get_api_key(self) -> str:
        """Get current API key (with rotation if enabled).
        
        Returns:
            API key string.
            
        Raises:
            ConfigurationError: If no keys configured.
        """
        if not self._config:
            raise ConfigurationError("No configuration exists")
        
        if not self._key_rotator:
            # Try environment variable
            env_key = os.getenv("OPENROUTER_API_KEY")
            if env_key:
                return env_key
            raise ConfigurationError("No API key configured")
        
        return self._key_rotator.get_key()
    
    def get_rotator_status(self) -> dict:
        """Get API key rotator status.
        
        Returns:
            Status dictionary.
        """
        if not self._key_rotator:
            return {"mode": "none", "status": "No keys configured"}
        
        return self._key_rotator.get_status()
    
    def update_github(
        self,
        token: Optional[str] = None,
        username: Optional[str] = None,
        auto_commit: bool = False,
    ) -> None:
        """Update GitHub integration config."""
        if not self._config:
            raise ConfigurationError("No configuration exists")
        self._config.github = GitHubConfig(
            token=token,
            username=username,
            auto_commit=auto_commit,
        )
        self._save()
        logger.info("✅ GitHub configuration updated")

    def update_notion(
        self,
        token: Optional[str] = None,
        parent_page_id: Optional[str] = None,
    ) -> None:
        """Update Notion integration config.

        Databases are created per-project by notion_setup_project() at runtime.
        Only the token and parent page ID are stored in config.

        Args:
            token: Notion integration token (secret_xxx).
            parent_page_id: Page to create project root pages under.
        """
        if not self._config:
            raise ConfigurationError("No configuration exists")
        self._config.notion = NotionConfig(
            token=token,
            parent_page_id=parent_page_id,
        )
        self._save()
        logger.info("✅ Notion configuration updated")

    def update_slack(
        self,
        bot_token: Optional[str] = None,
        channel: Optional[str] = "#codepilot",
    ) -> None:
        """Update Slack integration config.

        Args:
            bot_token: Slack bot token (xoxb-...).
            channel: Default channel for notifications and HITL.
        """
        if not self._config:
            raise ConfigurationError("No configuration exists")
        self._config.slack = SlackConfig(
            bot_token=bot_token,
            channel=channel,
        )
        self._save()
        logger.info("✅ Slack configuration updated")

    def reset(self) -> None:
        """Reset configuration (delete config file)."""
        if self.config_path.exists():
            self.config_path.unlink()
        self._config = None
        self._key_rotator = None
        logger.info("✅ Configuration reset")
