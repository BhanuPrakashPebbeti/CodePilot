"""Ollama LLM provider."""

from typing import Any

from .provider import LLMProvider
from ..core.exceptions import LLMError
from ..utils.constants import OLLAMA_BASE_URL
from ..utils.logger import get_logger

logger = get_logger(__name__)


class OllamaProvider(LLMProvider):
    """Ollama local LLM provider implementation."""
    
    def __init__(
        self,
        model: str,
        base_url: str = OLLAMA_BASE_URL,
        temperature: float = 0.7,
        max_tokens: int = 32000,
    ):
        """Initialize Ollama provider.
        
        Args:
            model: Model name.
            base_url: Ollama server URL.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens.
        """
        self.model_name = model
        self.base_url = base_url
        
        try:
            from langchain_ollama import ChatOllama
            
            self.client = ChatOllama(
                model=model,
                base_url=base_url,
                temperature=temperature,
                num_predict=max_tokens,
            )
            logger.debug(f"Initialized Ollama provider: {model} at {base_url}")
        except ImportError:
            raise LLMError(
                "langchain-ollama not installed. "
                "Install with: pip install langchain-ollama"
            )
        except Exception as e:
            logger.error(f"Failed to initialize Ollama: {e}")
            raise LLMError(f"Ollama initialization failed: {e}")
    
    def invoke(self, message: str) -> Any:
        """Invoke the LLM.
        
        Args:
            message: Input message.
        
        Returns:
            LLM response.
        
        Raises:
            LLMError: If invocation fails.
        """
        try:
            response = self.client.invoke(message)
            return response
        except Exception as e:
            logger.error(f"Ollama invocation failed: {e}")
            raise LLMError(f"LLM invocation failed: {e}")
    
    def get_model_name(self) -> str:
        """Get model name."""
        return self.model_name
    
    def supports_tools(self) -> bool:
        """Check if supports tools.
        
        Note: Not all Ollama models support tools.
        Models with tool support: mistral, neural-chat, qwen, etc.
        """
        tool_supported_models = {
            "mistral", "neural-chat", "qwen", "dolphin-mixtral",
            "openchat", "solar", "starling-lm"
        }
        return any(model in self.model_name.lower() for model in tool_supported_models)
    
    def get_llm(self) -> Any:
        """Get the underlying LangChain LLM client.
        
        Returns:
            The ChatOllama client instance.
        """
        return self.client
