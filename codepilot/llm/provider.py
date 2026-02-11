"""LLM provider interface."""

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    @abstractmethod
    def invoke(self, message: str) -> Any:
        """Invoke the LLM with a message.
        
        Args:
            message: Input message.
        
        Returns:
            LLM response.
        """
        pass
    
    @abstractmethod
    def get_model_name(self) -> str:
        """Get the model name.
        
        Returns:
            Model identifier.
        """
        pass
    
    @abstractmethod
    def supports_tools(self) -> bool:
        """Check if provider supports function calling/tools.
        
        Returns:
            True if tools supported.
        """
        pass
    
    @abstractmethod
    def get_llm(self) -> Any:
        """Get the underlying LangChain LLM client.
        
        Returns:
            The LangChain chat model instance.
        """
        pass
