"""Token budgeting + transcript compaction over the internal `llm.base.Message` layer.

Trigger (computed from the newest message backward, but implemented here as a simple total
count against the budget): `contextTokens > contextWindow - reserveTokens`. When triggered,
the oldest messages (after the leading system prompt, which is never touched) are folded into
one `CompactionEntry` — summarized, but with every detected canonical fact and caveat carried
forward **verbatim** — while the most recent segment is kept untouched. The split point is
only ever chosen at an Assistant-message boundary; it can never land between a tool-call
message and the tool-result message answering it, or the provider would reject the payload.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from llm.base import LLMClient, Message, MessageRole

_CAVEAT_MARKERS: Final[tuple[str, ...]] = (
    "caveat",
    "currentness",
    "not real-time",
    "not guaranteed real-time",
    "non-trading",
)
_FACT_MARKERS: Final[tuple[str, ...]] = ("canonical", "fact:")

_DEFAULT_KEEP_RECENT: Final[int] = 4


class CompactionEntry(BaseModel):
    """The summarized-history snapshot inserted in place of the compacted messages.

    `preserved_facts`/`preserved_caveats` are carried verbatim from the compacted messages —
    compaction is never allowed to drop them to save tokens.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1)
    preserved_facts: tuple[str, ...] = Field(default_factory=tuple)
    preserved_caveats: tuple[str, ...] = Field(default_factory=tuple)


def needs_compaction(context_tokens: int, window: int, reserve: int) -> bool:
    return context_tokens > window - reserve


def _is_valid_boundary(messages: Sequence[Message], index: int) -> bool:
    """A split at `index` is invalid only if it would separate an assistant tool-call
    message from the tool-result message reporting its outcome."""
    if index <= 0 or index >= len(messages):
        return True
    previous = messages[index - 1]
    following = messages[index]
    return not (
        previous.role is MessageRole.ASSISTANT
        and previous.tool_calls
        and following.role is MessageRole.TOOL
    )


def _find_split_index(messages: Sequence[Message], keep_recent: int) -> int:
    """The first valid Assistant-message boundary at or after `len(messages) - keep_recent`.

    Searching forward-only means compaction may keep slightly more of the recent transcript
    verbatim than `keep_recent` asks for, never less — safe, since the failure mode of "kept a
    little extra" is not a governance concern the way "split a tool call from its result" is.
    """
    if len(messages) <= keep_recent:
        return 0
    candidate = len(messages) - keep_recent
    for index in range(candidate, len(messages) + 1):
        if _is_valid_boundary(messages, index):
            return index
    return len(messages)


def _iter_lines(messages: Sequence[Message]) -> list[str]:
    lines: list[str] = []
    for message in messages:
        if message.content:
            lines.extend(line.strip() for line in message.content.splitlines() if line.strip())
    return lines


def _extract_caveats_and_facts(
    messages: Sequence[Message],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    caveats: list[str] = []
    facts: list[str] = []
    for line in _iter_lines(messages):
        lowered = line.lower()
        if any(marker in lowered for marker in _CAVEAT_MARKERS):
            caveats.append(line)
        elif any(marker in lowered for marker in _FACT_MARKERS):
            facts.append(line)
    return tuple(caveats), tuple(facts)


def _cheap_summary(older: Sequence[Message]) -> str:
    return f"{len(older)} earlier message(s) summarized to stay within the context budget."


def _render_compaction_entry(entry: CompactionEntry) -> str:
    lines = [entry.summary]
    if entry.preserved_facts:
        lines.append("Preserved canonical facts:")
        lines.extend(f"- {fact}" for fact in entry.preserved_facts)
    if entry.preserved_caveats:
        lines.append("Preserved caveats:")
        lines.extend(f"- {caveat}" for caveat in entry.preserved_caveats)
    return "\n".join(lines)


async def compact(
    messages: list[Message],
    llm: LLMClient,
    *,
    window: int,
    reserve: int,
    keep_recent: int = _DEFAULT_KEEP_RECENT,
) -> list[Message]:
    """Compact `messages` if `count_tokens` exceeds `window - reserve`; otherwise return
    them unchanged. The leading system-prompt message (if present) is always preserved as-is
    and never counted as part of the compactable "older" segment."""
    tokens = await llm.count_tokens(messages)
    if not needs_compaction(tokens, window, reserve) or not messages:
        return messages

    system_prefix: list[Message] = []
    body: Sequence[Message] = messages
    if messages[0].role is MessageRole.SYSTEM:
        system_prefix = [messages[0]]
        body = messages[1:]

    split = _find_split_index(body, keep_recent)
    older, recent = body[:split], body[split:]
    if not older:
        return messages

    caveats, facts = _extract_caveats_and_facts(older)
    entry = CompactionEntry(
        summary=_cheap_summary(older),
        preserved_facts=facts,
        preserved_caveats=caveats,
    )
    compaction_message = Message(role=MessageRole.SYSTEM, content=_render_compaction_entry(entry))
    return [*system_prefix, compaction_message, *recent]


__all__ = ["CompactionEntry", "compact", "needs_compaction"]
