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

3. Third-party warning suppression — authlib and google-adk emit noisy
   deprecation / experimental warnings on every import that are not
   actionable by us (they come from inside the libraries themselves).

Call ``apply_all_patches()`` once at startup, before any ADK usage.
Each patch is idempotent — safe to call multiple times.
"""

import json
import logging
import re
import warnings

_log = logging.getLogger(__name__)
_applied: set[str] = set()


# ---------------------------------------------------------------------------
# Patch 0: Suppress noisy third-party warnings
# ---------------------------------------------------------------------------

def _suppress_third_party_warnings() -> None:
    # This function is also called eagerly at module import time (bottom of
    # file) so that warnings are suppressed even if apply_all_patches() hasn't
    # been called yet.  The eager call handles the common case where
    # google.adk or authlib gets imported via builder.py/callbacks/__init__.py
    # before runner.py has had a chance to call apply_all_patches().
    """Filter deprecation/experimental warnings emitted by authlib and google-adk.

    These warnings originate inside third-party packages and are not
    actionable — we cannot fix them without modifying the libraries.
    Suppressing them keeps the terminal output clean for users.

    Suppressed:
    - AuthlibDeprecationWarning: authlib.jose is deprecated (use joserfc)
      → Triggered by google-adk importing authlib internally.
    - UserWarning [EXPERIMENTAL] PLUGGABLE_AUTH / BASE_AUTHENTICATED_TOOL
      → Triggered by google-adk feature flags on every agent import.
    """
    if "third_party_warnings" in _applied:
        return

    # authlib: "authlib.jose module is deprecated, please use joserfc instead"
    warnings.filterwarnings(
        "ignore",
        message=".*authlib\\.jose.*deprecated.*",
        category=DeprecationWarning,
    )
    # authlib uses a custom AuthlibDeprecationWarning (subclass of DeprecationWarning)
    # Catch it by module path in case the class isn't importable cleanly.
    warnings.filterwarnings(
        "ignore",
        module="authlib.*",
        category=DeprecationWarning,
    )

    # google-adk: "[EXPERIMENTAL] feature FeatureName.PLUGGABLE_AUTH is enabled."
    warnings.filterwarnings(
        "ignore",
        message=r".*\[EXPERIMENTAL\].*",
        category=UserWarning,
    )

    _applied.add("third_party_warnings")
    _log.debug("Applied patch: third_party_warnings")


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
    _suppress_third_party_warnings()
    _patch_tool_call_json()
    _patch_assistant_content()


# Apply warning suppression immediately so it takes effect even when only
# this module is imported (e.g. builder.py/callbacks triggering google.adk
# imports before runner.py calls apply_all_patches()).
_suppress_third_party_warnings()
