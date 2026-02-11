"""API key rotation manager."""

import random
import threading
import time
from typing import Optional

from .models import APIKey, LLMConfig
from ..core.exceptions import APIKeyError
from ..utils.logger import get_logger

logger = get_logger(__name__)


class APIKeyRotator:
    """Thread-safe API key rotation with automatic fallback."""
    
    def __init__(self, llm_config: LLMConfig):
        """Initialize key rotator.
        
        Args:
            llm_config: LLM configuration with keys.
        """
        self.llm_config = llm_config
        self._lock = threading.Lock()
        self._current_index = 0
        self._failed_keys = set()
    
    def get_key(self) -> str:
        """Get next available API key.
        
        Returns:
            API key string.
            
        Raises:
            APIKeyError: If no keys available.
        """
        # Single key mode
        if self.llm_config.has_single_key:
            return self.llm_config.api_key
        
        # Multi-key mode with rotation
        if not self.llm_config.has_multiple_keys:
            raise APIKeyError("No API keys configured")
        
        with self._lock:
            active_keys = [
                key for key in self.llm_config.api_keys
                if key.is_active and key.key not in self._failed_keys
            ]
            
            if not active_keys:
                # Reset failed keys after cooldown
                self._failed_keys.clear()
                active_keys = [key for key in self.llm_config.api_keys if key.is_active]
            
            if not active_keys:
                raise APIKeyError("All API keys are inactive or exhausted")
            
            # Round-robin selection
            key = active_keys[self._current_index % len(active_keys)]
            self._current_index = (self._current_index + 1) % len(active_keys)
            
            # Mark as used
            key.mark_used()
            
            logger.debug(f"Using API key: {key.label or key.key[:8]}... (usage: {key.usage_count})")
            return key.key
    
    def mark_key_failed(self, key_value: str, duration: int = 60) -> None:
        """Temporarily mark a key as failed.
        
        Args:
            key_value: The API key that failed.
            duration: How long to keep it in failed state (seconds).
        """
        with self._lock:
            self._failed_keys.add(key_value)
            logger.warning(f"API key {key_value[:8]}... marked as failed for {duration}s")
            
            # Schedule removal from failed set
            def remove_after_timeout():
                time.sleep(duration)
                with self._lock:
                    self._failed_keys.discard(key_value)
                    logger.debug(f"API key {key_value[:8]}... restored")
            
            thread = threading.Thread(target=remove_after_timeout, daemon=True)
            thread.start()
    
    def get_status(self) -> dict:
        """Get rotation status.
        
        Returns:
            Status dictionary with key info.
        """
        if self.llm_config.has_single_key:
            return {
                "mode": "single",
                "total_keys": 1,
                "active_keys": 1,
                "failed_keys": 0,
            }
        
        with self._lock:
            active = sum(1 for k in self.llm_config.api_keys if k.is_active)
            failed = len(self._failed_keys)
            
            return {
                "mode": "multi",
                "total_keys": len(self.llm_config.api_keys),
                "active_keys": active - failed,
                "failed_keys": failed,
                "current_index": self._current_index,
            }
