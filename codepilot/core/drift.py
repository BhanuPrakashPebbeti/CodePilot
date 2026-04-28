"""Agentic context-drift detector.

Uses the configured LLM (Ollama / OpenRouter / Gemini) to decide whether
a new user request is related to the ongoing project.  This is intentionally
a small, focused LLM call — not a full ADK pipeline — so it completes in
under one second and does not interrupt the user's flow unless it needs to.

Why LLM instead of rules?
--------------------------
Rule-based keyword matching (e.g., "sudoku" vs "e-commerce") misses nuanced
shifts: "add dark mode" after "build a chess engine" is fine, but "build a
payroll system" after "build a chess engine" is a genuine context switch.
Only an LLM can reason about intent and domain similarity reliably.

Architecture
------------
DriftDetector.check(task_history, new_task) → DriftResult
  - Builds a compact prompt (<300 tokens) from the last 5 task descriptions
  - Calls litellm.completion() with the same provider/model as the main pipeline
  - Parses the JSON response: {"drift": bool, "reason": str, "confidence": float}
  - Falls back to no-drift on any error (safe default — don't interrupt unnecessarily)

The detector is stateless: the runner passes history and new_task each time.
"""

import json
import re
from dataclasses import dataclass
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class DriftResult:
    drift: bool
    reason: str
    confidence: float   # 0.0–1.0

    def __bool__(self) -> bool:
        return self.drift


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a project-context classifier for an AI coding assistant called CodePilot.

Your job: decide if a new user request belongs to the SAME project the user has been working on,
or if it represents a switch to an entirely different project.

Rules:
- Enhancements, fixes, or additions to the current project → NOT a drift (drift=false)
- A completely new product/domain/codebase → drift=true
- When in doubt → drift=false (do not interrupt unnecessarily)

Respond ONLY with valid JSON. No extra text, no markdown fences:
{"drift": <true|false>, "reason": "<one sentence>", "confidence": <0.0-1.0>}
"""

_USER_TEMPLATE = """\
PREVIOUS TASKS (recent work in this session):
{history}

NEW REQUEST:
{new_task}

Is the new request a context drift (a switch to a different project)?
"""


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class DriftDetector:
    """LLM-powered context-drift classifier.

    Args:
        provider:  "ollama", "openrouter", or "gemini"
        model:     Model name (e.g. "mistral", "deepseek/deepseek-coder")
        api_key:   API key for cloud providers (OpenRouter / Gemini)
    """

    def __init__(self, provider: str, model: str, api_key: Optional[str] = None) -> None:
        self.provider = provider
        self.model = _model_str(provider, model)
        self.api_key = api_key

    def check(self, task_history: list[str], new_task: str) -> DriftResult:
        """Check whether new_task is a context drift relative to task_history.

        Args:
            task_history: List of previous task descriptions (oldest first, max 5).
            new_task:     The new user request to evaluate.

        Returns:
            DriftResult with drift flag, reason, and confidence.
            On any error, returns DriftResult(drift=False, …) — safe default.
        """
        if not task_history:
            # No history yet — cannot drift
            return DriftResult(drift=False, reason="No prior tasks in session", confidence=0.0)

        history_text = "\n".join(
            f"{i+1}. {t}" for i, t in enumerate(task_history[-5:])
        )
        user_msg = _USER_TEMPLATE.format(history=history_text, new_task=new_task)

        try:
            import litellm

            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.0,       # deterministic classification
                max_tokens=80,         # JSON fits in 80 tokens
                timeout=15,            # don't block the REPL
                api_key=self.api_key,
            )

            raw = response.choices[0].message.content or ""
            return _parse(raw)

        except Exception as exc:
            logger.debug("Drift check failed (%s) — defaulting to no-drift", exc)
            return DriftResult(drift=False, reason=f"Check skipped: {exc}", confidence=0.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _model_str(provider: str, model: str) -> str:
    """Convert CodePilot provider+model to a LiteLLM model string."""
    if provider == "ollama":
        return f"ollama/{model}"
    if provider == "openrouter":
        return f"openrouter/{model}"
    # Gemini / others — pass through as-is
    return model


def _parse(raw: str) -> DriftResult:
    """Extract JSON from the LLM response and build a DriftResult."""
    # Strip markdown fences if the model adds them despite instructions
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()

    # Find the first {...} block in case the model adds leading text
    match = re.search(r"\{.*?\}", cleaned, re.DOTALL)
    if not match:
        logger.debug("No JSON found in drift response: %r", raw[:200])
        return DriftResult(drift=False, reason="Could not parse LLM response", confidence=0.0)

    try:
        data = json.loads(match.group())
        drift = bool(data.get("drift", False))
        reason = str(data.get("reason", ""))
        confidence = float(data.get("confidence", 0.8 if drift else 0.2))
        if drift:
            logger.info("Drift detected (confidence=%.2f): %s", confidence, reason)
        return DriftResult(drift=drift, reason=reason, confidence=confidence)
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug("Failed to parse drift JSON %r: %s", raw[:200], e)
        return DriftResult(drift=False, reason="Parse error", confidence=0.0)
