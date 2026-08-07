"""Server-sent-events framing for the streaming endpoints.

One helper turns an `events.bus.Event` into a wire SSE frame: the event `kind` becomes the SSE
`event:` field, the full correlation envelope (`seq, ts, turn_id, agent_id, parent_agent_id,
node_id, data`) becomes the JSON `data:` payload, and the monotonic `seq` doubles as the SSE
`id:` so a reconnecting client can resume with `Last-Event-ID`. Keeping the framing here (not
inline in a router) means every stream — agent turns, quote feeds, cache-invalidation — emits an
identical frame shape.
"""

from __future__ import annotations

import json

from events.bus import Event


def format_sse_event(event: Event) -> str:
    """Render one `Event` as a complete SSE frame (trailing blank line included)."""
    payload = {
        "seq": event.seq,
        "ts": event.ts.isoformat(),
        "turn_id": event.turn_id,
        "agent_id": event.agent_id,
        "parent_agent_id": event.parent_agent_id,
        "node_id": event.node_id,
        "data": event.data,
    }
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {body}\n\n"


__all__ = ["format_sse_event"]
