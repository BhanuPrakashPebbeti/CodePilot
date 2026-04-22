"""ADK compatibility patches for non-Gemini LLM providers.

These patches fix known bugs in ADK's LiteLLM integration when using
OpenRouter, Ollama, or other OpenAI-compatible providers:

1. JSON repair — smaller models sometimes emit slightly malformed JSON
   for function-call arguments (trailing commas, missing commas, etc.).
   ADK's parser does a raw ``json.loads()`` with no error handling, which
   kills the pipeline.  This pre-repairs the argument string before ADK
   sees it.

2. Assistant content — OpenRouter and several providers reject messages
   where ``role=assistant`` has ``tool_calls`` but ``content`` is ``None``
   or missing.  The OpenAI spec allows ``null`` but many providers enforce
   ``content`` as a required string.

Call ``apply_all_patches()`` once at startup, before any ADK usage.
Each patch is idempotent — safe to call multiple times.
"""

import json
import logging
import re

_log = logging.getLogger(__name__)
_applied: set[str] = set()


# ---------------------------------------------------------------------------
# Patch 1: JSON repair for malformed tool-call arguments
# ---------------------------------------------------------------------------

def _repair_json(s: str) -> str:
    """Best-effort repair of slightly malformed JSON strings."""
    t = s.strip()
    # Trailing commas before } or ]
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    # Missing comma between a value and the next key:
    #   "val" "key":  →  "val", "key":
    #   42 "key":     →  42, "key":
    #   true/false/null/} "key": → ..., "key":
    t = re.sub(
        r'(["}\]\d]|true|false|null)\s+("(?:[^"\\]|\\.)*"\s*:)',
        r"\1, \2",
        t,
    )
    return t


def _patch_tool_call_json() -> None:
    """Wrap ADK's LiteLLM response parser to repair broken JSON."""
    if "tool_call_json" in _applied:
        return
    try:
        import google.adk.models.lite_llm as _mod
    except ImportError:
        return

    _orig = _mod._message_to_generate_content_response

    def _patched(message, **kwargs):
        for tc in message.get("tool_calls") or []:
            if getattr(tc, "type", None) != "function":
                continue
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            args = getattr(fn, "arguments", None)
            if not args or not isinstance(args, str):
                continue
            try:
                json.loads(args)
            except json.JSONDecodeError:
                repaired = _repair_json(args)
                try:
                    json.loads(repaired)
                    fn.arguments = repaired
                    _log.warning(
                        "Repaired malformed tool-call JSON for '%s'",
                        getattr(fn, "name", "?"),
                    )
                except json.JSONDecodeError:
                    _log.warning(
                        "Unparseable tool-call JSON for '%s', using {}: %.200s",
                        getattr(fn, "name", "?"),
                        args,
                    )
                    fn.arguments = "{}"
        return _orig(message, **kwargs)

    _mod._message_to_generate_content_response = _patched
    _applied.add("tool_call_json")
    _log.debug("Applied patch: tool_call_json")


# ---------------------------------------------------------------------------
# Patch 2: Ensure assistant messages have content when tool_calls present
# ---------------------------------------------------------------------------

def _patch_assistant_content() -> None:
    """Set content='' on assistant messages that have tool_calls but no content."""
    if "assistant_content" in _applied:
        return
    try:
        import google.adk.models.lite_llm as _mod
    except ImportError:
        return

    _orig = _mod._get_completion_inputs

    async def _patched(llm_request, model):
        messages, tools, response_format, generation_params = await _orig(
            llm_request, model
        )
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            if msg.get("tool_calls") and not msg.get("content"):
                msg["content"] = ""
        return messages, tools, response_format, generation_params

    _mod._get_completion_inputs = _patched
    _applied.add("assistant_content")
    _log.debug("Applied patch: assistant_content")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_all_patches() -> None:
    """Apply all ADK compatibility patches.  Safe to call multiple times."""
    _patch_tool_call_json()
    _patch_assistant_content()
