"""The auditable per-Step journal: what was proposed, what tool ran, a redacted result
summary, and the guardrail verdict — never a raw sponsor payload or a secret.

Sink choice for this phase, matching the precedent already established for the ingest
journal: structured logs (structlog), not a dedicated DB table. A queryable `journal_entries`
store with per-user/per-thread lookup is an interaction-layer concern (not yet built); what
governance requires today is that *every Step's tool calls and output-guardrail verdicts are
journaled* with typed fields, not a free-text blob — `JournalEntry`/`Journal` below satisfy
that via one structured log line per event, shaped so a future DB-backed sink can replace the
logging call in `Journal.record_*` alone, with no call-site changes.

Result summaries never carry the tool's raw payload: `_summarize_result` (in `agent/loop.py`)
reduces every result to its type name plus its top-level field *names* (never values) before
it ever reaches this module — the same "provenance, not payload" convention already used by
the tool layer's own capability-summary types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class JournalEntry:
    """One journaled Step event.

    `agent_id`/`parent_agent_id` default to a single root lane — the fields exist now so a
    future parallel-lane fan-out can tag entries per lane without changing this shape.
    """

    step_index: int
    kind: str  # "tool_call" | "guardrail_output"
    tool_name: str | None = None
    argument_keys: tuple[str, ...] = ()
    result_summary: str | None = None
    guardrail_allowed: bool | None = None
    guardrail_reason: str | None = None
    agent_id: str = "root"
    parent_agent_id: str | None = None
    journaled_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class Journal:
    """In-memory + structured-log-backed record of one turn's Steps.

    A fresh `Journal` is constructed per turn by `agent.loop.AgentLoop` and handed to every
    tool call via `ToolContext.journal` — nothing in `tools/` reads it back, it is purely an
    audit sink the loop writes to before/after dispatch.
    """

    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def record_tool_call(  # noqa: PLR0913 — one typed field per journaled dimension, no
        # free-text blob substitute is acceptable for an audit trail
        self,
        *,
        step_index: int,
        tool_name: str,
        arguments_keys: tuple[str, ...],
        result_summary: str,
        guardrail_allowed: bool,
        guardrail_reason: str | None,
        agent_id: str = "root",
        parent_agent_id: str | None = None,
    ) -> JournalEntry:
        entry = JournalEntry(
            step_index=step_index,
            kind="tool_call",
            tool_name=tool_name,
            argument_keys=arguments_keys,
            result_summary=result_summary,
            guardrail_allowed=guardrail_allowed,
            guardrail_reason=guardrail_reason,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        self._entries.append(entry)
        logger.info(
            "agent_step_tool_call_journaled",
            step_index=entry.step_index,
            tool_name=entry.tool_name,
            argument_keys=entry.argument_keys,
            result_summary=entry.result_summary,
            guardrail_allowed=entry.guardrail_allowed,
            guardrail_reason=entry.guardrail_reason,
            agent_id=entry.agent_id,
            parent_agent_id=entry.parent_agent_id,
        )
        return entry

    def record_output_guardrail(
        self,
        *,
        step_index: int,
        allowed: bool,
        reason: str | None,
        agent_id: str = "root",
        parent_agent_id: str | None = None,
    ) -> JournalEntry:
        entry = JournalEntry(
            step_index=step_index,
            kind="guardrail_output",
            guardrail_allowed=allowed,
            guardrail_reason=reason,
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        self._entries.append(entry)
        logger.info(
            "agent_step_output_guardrail_journaled",
            step_index=entry.step_index,
            guardrail_allowed=entry.guardrail_allowed,
            guardrail_reason=entry.guardrail_reason,
            agent_id=entry.agent_id,
            parent_agent_id=entry.parent_agent_id,
        )
        return entry


__all__ = ["Journal", "JournalEntry"]
