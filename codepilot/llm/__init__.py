"""LLM module — model resolution is handled by ADK + LiteLLM.

The legacy LLMProvider / OllamaProvider / OpenRouterProvider classes
have been removed. Model routing is now done by:
  - ADK's native Gemini support
  - LiteLLM for ollama/ and openrouter/ prefixed models
  - codepilot.agents.builder._resolve_model_string()
"""
