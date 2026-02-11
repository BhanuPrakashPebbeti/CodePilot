"""OpenRouter LLM provider."""

from typing import Any, Optional

from langchain_openai import ChatOpenAI
from pydantic import Field, SecretStr

from .provider import LLMProvider
from ..core.exceptions import LLMError
from ..utils.constants import OPENROUTER_BASE_URL, OPENROUTER_REFERER, OPENROUTER_TITLE
from ..utils.logger import get_logger

logger = get_logger(__name__)


class ChatOpenRouter(ChatOpenAI):
    """OpenRouter-compatible ChatOpenAI wrapper."""

    openai_api_key: Optional[SecretStr] = Field(alias="api_key", default=None)

    @property
    def lc_secrets(self) -> dict[str, str]:
        return {"openai_api_key": "OPENROUTER_API_KEY"}

    def __init__(self, api_key: str, **kwargs):
        """Initialize OpenRouter client.
        
        Args:
            api_key: API key for OpenRouter.
            **kwargs: Additional arguments.
        """
        headers = {
            "HTTP-Referer": OPENROUTER_REFERER,
            "X-Title": OPENROUTER_TITLE
        }
        super().__init__(
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
            default_headers=headers,
            **kwargs
        )


class OpenRouterProvider(LLMProvider):
    """OpenRouter LLM provider implementation."""
    
    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 32000,
    ):
        """Initialize OpenRouter provider.
        
        Args:
            api_key: API key.
            model: Model name.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens.
        """
        self.model_name = model
        
        try:
            self.client = ChatOpenRouter(
                api_key=api_key,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            logger.debug(f"Initialized OpenRouter provider: {model}")
        except Exception as e:
            logger.error(f"Failed to initialize OpenRouter: {e}")
            raise LLMError(f"OpenRouter initialization failed: {e}")
    
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
            logger.error(f"OpenRouter invocation failed: {e}")
            raise LLMError(f"LLM invocation failed: {e}")
    
    def get_model_name(self) -> str:
        """Get model name."""
        return self.model_name
    
    def supports_tools(self) -> bool:
        """Check if supports tools."""
        # OpenRouter models generally support tools
        return True
    
    def get_llm(self) -> Any:
        """Get the underlying LangChain LLM client.
        
        Returns:
            The ChatOpenRouter client instance.
        """
        return self.client
