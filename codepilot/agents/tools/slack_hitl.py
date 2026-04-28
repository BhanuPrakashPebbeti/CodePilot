"""Slack tools for notifications and human-in-the-loop (HITL) decisions.

Provides two tools:
  slack_notify()    — fire-and-forget message to a Slack channel
  slack_ask_human() — post a numbered-choice question, poll for reply,
                      fall back to safe default on timeout / no config

HITL flow:
  1. Posts the question + options to the configured channel.
  2. Polls for a reply message containing "1", "2", "3" … every 10 s.
  3. If no valid reply arrives within timeout_seconds, defaults to choice 1
     and posts a timeout notice to Slack.
  4. If Slack is not configured at all, returns choice 1 immediately so
     the pipeline continues deterministically.

Requires: slack-sdk>=3.0.0  (pip install slack-sdk)
Env vars: SLACK_BOT_TOKEN (xoxb-...), SLACK_CHANNEL (e.g. "#codepilot")
"""

import os
import time

from ...utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _client():
    """Return a slack_sdk.WebClient or None if not configured."""
    try:
        from slack_sdk import WebClient
    except ImportError:
        logger.debug("slack-sdk not installed — Slack tools are no-ops")
        return None
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        return None
    return WebClient(token=token)


def _channel() -> str:
    return os.environ.get("SLACK_CHANNEL", "#codepilot")


def _skipped() -> dict:
    return {"ok": True, "skipped": True, "reason": "Slack not configured"}


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

def slack_notify(
    message: str,
    channel: str = "",
) -> dict:
    """Post a notification message to a Slack channel.

    Supports Slack markdown (*bold*, `code`, _italic_).
    Safe to call even if Slack is not configured — returns skipped=True.

    Common use cases:
      - Task started / completed
      - Build failed / recovered
      - Pipeline completed or partially completed
      - GitHub PR created (include URL)

    Args:
        message: Notification text. Keep under 3000 chars.
        channel: Slack channel (e.g. "#codepilot"). Uses SLACK_CHANNEL
                 env var if not provided.

    Returns:
        {"ok": True, "ts": str, "channel": str}
        or {"ok": True, "skipped": True} if Slack is not configured.
    """
    client = _client()
    if not client:
        return _skipped()

    ch = channel or _channel()
    try:
        resp = client.chat_postMessage(channel=ch, text=message, mrkdwn=True)
        logger.info("Slack notification sent to %s", ch)
        return {"ok": True, "ts": resp.get("ts", ""), "channel": ch}
    except Exception as exc:
        logger.warning("slack_notify failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def slack_ask_human(
    question: str,
    options: list,
    timeout_seconds: int = 120,
    channel: str = "",
) -> dict:
    """Post a numbered-choice question to Slack and wait for a human reply.

    Used for human-in-the-loop decisions when:
      - The same error has persisted across multiple fix attempts.
      - An ambiguous architectural decision must be made.
      - A destructive or irreversible action needs confirmation.

    Workflow:
      1. Posts the question + numbered options to Slack.
      2. Polls for a reply containing "1", "2", etc. every 10 seconds.
      3. If no reply within timeout_seconds, defaults to choice 1 (safest)
         and posts a timeout notice.
      4. If Slack is not configured, returns choice 1 immediately.

    Agents should always resume after this call regardless of the source
    (slack / timeout_default / not_configured).

    Args:
        question: What you're asking the human (e.g. "Build failed 3 times. What next?").
        options: List of choices (e.g. ["Retry with different approach", "Simplify", "Stop"]).
        timeout_seconds: How long to wait (default 120 s). Capped at 300 s.
        channel: Slack channel. Uses SLACK_CHANNEL env var if not provided.

    Returns:
        {
            "choice": int,          # 1-based index of chosen option
            "option_text": str,     # Text of chosen option
            "source": str,          # "slack" | "timeout_default" | "not_configured" | "error_default"
        }
    """
    if not options:
        return {"choice": 1, "option_text": "", "source": "no_options"}

    timeout_seconds = min(timeout_seconds, 300)
    options_text = "\n".join(f"  {i + 1}. {opt}" for i, opt in enumerate(options))
    full_message = (
        f"🤔 *CodePilot needs your input*\n\n"
        f"{question}\n\n"
        f"{options_text}\n\n"
        f"_Reply with the number of your choice within {timeout_seconds}s. "
        f"Default if no reply: *1 — {options[0]}*_"
    )

    client = _client()
    if not client:
        logger.info("Slack not configured — HITL defaulting to choice 1")
        return {"choice": 1, "option_text": options[0], "source": "not_configured"}

    ch = channel or _channel()

    try:
        resp = client.chat_postMessage(channel=ch, text=full_message, mrkdwn=True)
        msg_ts = resp.get("ts", "")
        msg_channel = resp.get("channel", ch)
        logger.info("HITL question posted to %s, waiting %ds", ch, timeout_seconds)

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(10)
            try:
                history = client.conversations_history(
                    channel=msg_channel,
                    oldest=msg_ts,
                    limit=15,
                )
                for msg in history.get("messages", []):
                    if msg.get("ts") == msg_ts:
                        continue  # skip the original question message
                    text = msg.get("text", "").strip()
                    for idx, opt in enumerate(options, 1):
                        if text.startswith(str(idx)):
                            logger.info("HITL reply: %r → choice %d", text, idx)
                            return {"choice": idx, "option_text": opt, "source": "slack"}
            except Exception:
                pass  # network hiccup — keep polling

        # Timeout: default to choice 1 and notify
        logger.info("HITL timeout after %ds — defaulting to choice 1", timeout_seconds)
        try:
            client.chat_postMessage(
                channel=ch,
                text=(
                    f"⏱️ No reply received after {timeout_seconds}s — "
                    f"defaulting to option 1: *{options[0]}*"
                ),
                mrkdwn=True,
            )
        except Exception:
            pass
        return {"choice": 1, "option_text": options[0], "source": "timeout_default"}

    except Exception as exc:
        logger.warning("slack_ask_human failed: %s", exc)
        return {"choice": 1, "option_text": options[0], "source": "error_default"}
