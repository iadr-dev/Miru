"""`EventKind` is the one definition of every event-bus kind, and `TURN_STREAM_KINDS` must match the
kinds the web client handles (`web/src/types/sse.ts`'s `SSE_EVENT_KINDS`). A kind added on one side
only fails here instead of being silently dropped by the other."""

from __future__ import annotations

import re
from pathlib import Path

from events.bus import InMemoryEventBus
from events.kinds import TURN_STREAM_KINDS, EventKind

_WEB_SSE_TYPES = Path(__file__).resolve().parents[3] / "web" / "src" / "types" / "sse.ts"


def _web_sse_event_kinds() -> set[str]:
    source = _WEB_SSE_TYPES.read_text(encoding="utf-8")
    match = re.search(r"SSE_EVENT_KINDS\s*=\s*\[(.*?)\]\s*as const", source, re.DOTALL)
    assert match, "SSE_EVENT_KINDS not found in web/src/types/sse.ts"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_turn_stream_kinds_match_the_web_client() -> None:
    backend = {kind.value for kind in TURN_STREAM_KINDS}
    web = _web_sse_event_kinds()
    assert backend - web == set(), f"emitted but not handled by the web client: {backend - web}"
    assert web - backend == set(), f"handled by the web client but never emitted: {web - backend}"


def test_event_kind_values_are_unique() -> None:
    values = [kind.value for kind in EventKind]
    assert len(values) == len(set(values))


def test_a_kind_publishes_as_its_wire_string() -> None:
    event = InMemoryEventBus().publish(EventKind.TURN_END, turn_id="t1")

    assert event.kind == "turn.end"
    assert event.model_dump(mode="json")["kind"] == "turn.end"
