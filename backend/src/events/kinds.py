"""Every event kind published on the event bus, defined once.

Event kinds are a wire contract: the web client switches on them (`web/src/types/sse.ts`'s
`SSE_EVENT_KINDS`), and a misspelt literal at one publish site would silently drop a UI feature.
So `EventBus.publish` takes an `EventKind`, never a bare string, and
`tests/events/test_event_kinds.py` fails if `TURN_STREAM_KINDS` and the web client's list ever
disagree.

The values are the existing wire names; `EventKind` is a `StrEnum`, so a member compares equal to
its string and serializes as it.
"""

from __future__ import annotations

from enum import StrEnum


class EventKind(StrEnum):
    # -- per-turn chat stream (SSE to the web client) -----------------------------------------
    TURN_START = "turn.start"
    ROUTE_DECIDED = "route.decided"
    AGENT_SPAWNED = "agent.spawned"
    AGENT_PROGRESS = "agent.progress"
    AGENT_DONE = "agent.done"
    TOOL_START = "tool.start"
    TOOL_RESULT = "tool.result"
    THINKING_DELTA = "thinking.delta"
    WEB_SEARCH_RESULT = "web_search.result"
    RICH_CARD = "rich_card"
    SYNTHESIS_START = "synthesis.start"
    USAGE = "usage"
    MEMORY_USED = "memory.used"
    PERSONALITY_USED = "personality.used"
    PET_LEVELED = "pet.leveled"
    PET_PULL_EARNED = "pet.pull_earned"
    STEERING_ACCEPTED = "steering.accepted"
    MESSAGE = "message"
    MESSAGE_DELTA = "message.delta"
    TURN_END = "turn.end"

    # -- data freshness (the /updates/stream SSE) ----------------------------------------------
    DATA_UPDATED = "data.updated"

    # -- scheduler (internal; not streamed to the chat client) --------------------------------
    ALERT_FIRED = "alert.fired"
    SCHEDULE_RUN = "schedule.run"
    SCHEDULE_DISABLED = "schedule.disabled"
    SCHEDULE_SKIPPED = "schedule.skipped"
    SCHEDULE_SKIPPED_LIMIT = "schedule.skipped_limit"
    SCHEDULE_SKIPPED_QUOTA = "schedule.skipped_quota"


#: The kinds a chat turn streams to the web client; must equal the web's `SSE_EVENT_KINDS`.
TURN_STREAM_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.TURN_START,
        EventKind.ROUTE_DECIDED,
        EventKind.AGENT_SPAWNED,
        EventKind.AGENT_PROGRESS,
        EventKind.AGENT_DONE,
        EventKind.TOOL_START,
        EventKind.TOOL_RESULT,
        EventKind.THINKING_DELTA,
        EventKind.WEB_SEARCH_RESULT,
        EventKind.RICH_CARD,
        EventKind.SYNTHESIS_START,
        EventKind.USAGE,
        EventKind.MEMORY_USED,
        EventKind.PERSONALITY_USED,
        EventKind.PET_LEVELED,
        EventKind.PET_PULL_EARNED,
        EventKind.STEERING_ACCEPTED,
        EventKind.MESSAGE,
        EventKind.MESSAGE_DELTA,
        EventKind.TURN_END,
    }
)

__all__ = ["TURN_STREAM_KINDS", "EventKind"]
