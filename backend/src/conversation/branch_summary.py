"""`switch_branch` -> `BranchSummaryEntry`: summarizing the branch a user is leaving via a
cheap LLM role, so exploring an alternative stays token-cheap without losing *why* the
abandoned approach succeeded or failed.

The summary is a structured `{approach, outcome, cause, key_figures, source_ref}` record, never
free prose — a summary that drops *why* an approach failed would let the model re-propose it,
so the prompt below is a first-class, tested asset (per the shared quality bar every
compression layer in this codebase — `agent.context_window.CompactionEntry` included — has to
meet), not an afterthought wrapper around a raw LLM call.
"""

from __future__ import annotations

import json
import re
from typing import Final, cast

import structlog

from conversation.models import BranchSummaryBlock
from conversation.models import Message as ConversationMessage
from llm.base import LLMClient, Message, MessageRole

logger = structlog.get_logger(__name__)

_SUMMARY_SYSTEM_PROMPT: Final[str] = """\
You summarize an abandoned conversation branch for later recall. Respond with ONLY a JSON \
object with exactly these keys: "approach" (what was tried, one sentence), "outcome" (one of \
"tried", "failed", "confirmed"), "cause" (why it succeeded or failed, one sentence), \
"key_figures" (a list of short strings naming any specific numbers/dates/symbols mentioned — \
empty list if none). Never include a buy/sell/hold recommendation or price target in your \
summary, even if the branch discussed one. No prose outside the JSON object.
"""

_FALLBACK_OUTCOME: Final[str] = "tried"


def _render_branch_transcript(messages: list[ConversationMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        content = message.content
        text = getattr(content, "text", None)
        if text:
            lines.append(f"[{message.type.value}] {text}")
    return "\n".join(lines) if lines else "(no text content on this branch)"


def _parse_summary_json(raw_text: str) -> dict[str, object]:
    parsed: object
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}


async def summarize_branch(
    messages: list[ConversationMessage],
    llm: LLMClient,
    *,
    source_ref: str | None = None,
) -> BranchSummaryBlock:
    """Summarize the messages on a branch being left behind. `llm` is expected to be a
    cheap-role client (`llm.registry.ModelRole.CLASSIFIER` or similar) — the caller resolves
    that role, this function just uses whatever `LLMClient` it is given, so it is equally
    testable against a `StubLLMClient` script."""
    transcript = _render_branch_transcript(messages)
    response = await llm.chat(
        [
            Message(role=MessageRole.SYSTEM, content=_SUMMARY_SYSTEM_PROMPT),
            Message(role=MessageRole.USER, content=transcript),
        ]
    )
    parsed = _parse_summary_json(response.text or "")

    approach = str(parsed.get("approach") or "an approach was explored on this branch")
    outcome = str(parsed.get("outcome") or _FALLBACK_OUTCOME)
    if outcome not in {"tried", "failed", "confirmed"}:
        outcome = _FALLBACK_OUTCOME
    cause = str(parsed.get("cause") or "no cause was recorded")
    raw_figures = parsed.get("key_figures")
    key_figures: tuple[str, ...] = ()
    if isinstance(raw_figures, list):
        key_figures = tuple(str(figure) for figure in cast("list[object]", raw_figures))

    summary = BranchSummaryBlock(
        approach=approach,
        outcome=outcome,
        cause=cause,
        key_figures=key_figures,
        source_ref=source_ref,
    )
    logger.info("branch_summarized", outcome=summary.outcome, message_count=len(messages))
    return summary


__all__ = ["summarize_branch"]
