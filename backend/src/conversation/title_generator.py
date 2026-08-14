"""Auto-titling a thread from its first turn — the same cheap-LLM-call shape as
`orchestrator.classifier.IntentClassifier`: resolve the CLASSIFIER role, ask for a short title,
and on *any* failure (provider unconfigured, transport error, empty/unusable response) fall back
to a deterministic truncation of the user's own message rather than leaving the thread
untitled. Never raises — a title is cosmetic, not load-bearing, so failure here must never
surface as a turn error.

Reuses `ModelRole.CLASSIFIER` rather than adding a dedicated role: title generation has the same
cost/latency/quality bar as intent classification, and `branch_summary.py` established this same
"reuse the cheap role for auxiliary structured calls" convention.
"""

from __future__ import annotations

import re
from typing import Final

import structlog

from llm.base import LLMClient, Message, MessageRole
from llm.registry import ModelRole, ProviderNotConfiguredError, resolve_role

logger = structlog.get_logger(__name__)

_MAX_TITLE_LENGTH: Final[int] = 60

_TITLE_SYSTEM_PROMPT: Final[str] = (
    "You write short titles for a Taiwan-stock/ETF analysis chat thread, summarizing the user's "
    "first message. Respond with ONLY the title text, no quotes, no punctuation at the end, no "
    f"prose. Keep it under {_MAX_TITLE_LENGTH} characters, in the same language as the message. "
    "Never include a buy/sell/hold recommendation or price target in the title, even if the "
    "message asks for one."
)


def _truncate_fallback(message: str) -> str:
    """Deterministic floor: the message's first line, hard-truncated. Never empty — an
    all-whitespace message falls back to a fixed placeholder rather than an empty title."""
    first_line = message.strip().splitlines()[0].strip() if message.strip() else ""
    if not first_line:
        return "New conversation"
    if len(first_line) <= _MAX_TITLE_LENGTH:
        return first_line
    return first_line[: _MAX_TITLE_LENGTH - 1].rstrip() + "…"


def _clean_title(raw: str) -> str:
    text = raw.strip().strip("\"'“”「」").splitlines()[0].strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > _MAX_TITLE_LENGTH:
        text = text[: _MAX_TITLE_LENGTH - 1].rstrip() + "…"
    return text


def _resolve_client(client: LLMClient | None) -> LLMClient | None:
    if client is not None:
        return client
    try:
        return resolve_role(ModelRole.CLASSIFIER)
    except ProviderNotConfiguredError:
        logger.warning("title_generator_provider_unconfigured_falling_back_to_truncation")
        return None


async def generate_title(message: str, *, client: LLMClient | None = None) -> str:
    """Generate a short thread title from the user's first message. Always returns a non-empty
    string — degrades to `_truncate_fallback` on any failure, never raises."""
    resolved = _resolve_client(client)
    if resolved is None:
        return _truncate_fallback(message)
    try:
        response = await resolved.chat(
            [
                Message(role=MessageRole.SYSTEM, content=_TITLE_SYSTEM_PROMPT),
                Message(role=MessageRole.USER, content=message),
            ]
        )
        title = _clean_title(response.text or "")
        return title or _truncate_fallback(message)
    except Exception as exc:  # a title is cosmetic — any failure degrades, never propagates
        logger.warning("title_generator_call_failed_falling_back", error=str(exc))
        return _truncate_fallback(message)


__all__ = ["generate_title"]
