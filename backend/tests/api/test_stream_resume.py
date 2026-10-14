"""The `Last-Event-ID` durable-resume boundary, tested at the router's composition seam
(`_resume_stream` + `_parse_last_event_id`). Proves the exact no-gap / no-duplicate contract:
replay covers `seq > after_seq`, live follows without re-emitting anything replay already sent,
and an event both persisted and captured live is emitted exactly once.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from api.routers.threads_actions import _parse_last_event_id, _resumable_turn_id, _resume_stream
from conversation.models import Thread, Turn, TurnRole, TurnStatus
from events.bus import Event
from events.turn_control import RemoteTurn


def _event(seq: int, kind: str = "message") -> Event:
    return Event(kind=kind, seq=seq, ts=datetime.now(UTC), turn_id="t1", data={"n": seq})


class _StubReader:
    """A `StreamReader` stub: `replay_since` returns a fixed persisted log slice; `live` yields a
    scripted sequence of live frames (with a small delay so replay runs first)."""

    def __init__(self, persisted: list[Event], live: list[Event]) -> None:
        self._persisted = persisted
        self._live = live

    async def replay_since(self, turn_id: str, after_seq: int) -> list[Event]:
        del turn_id
        return [e for e in self._persisted if e.seq > after_seq]

    async def live(self, turn_id: str) -> AsyncIterator[Event]:
        del turn_id
        await asyncio.sleep(0.02)  # ensure replay drains before live frames arrive
        for event in self._live:
            yield event


def _seqs(frames: list[str]) -> list[int]:
    return [json.loads(f.split("data: ", 1)[1].split("\n", 1)[0])["seq"] for f in frames]


async def _collect(reader: _StubReader, *, after_seq: int) -> list[int]:
    frames = [frame async for frame in _resume_stream(reader, turn_id="t1", after_seq=after_seq)]
    return _seqs(frames)


def test_parse_last_event_id() -> None:
    assert _parse_last_event_id(None) == -1  # fresh connect → everything from seq 0
    assert _parse_last_event_id("3") == 3
    assert _parse_last_event_id("garbage") == -1  # malformed → from the beginning, never a crash


async def test_fresh_connect_replays_all_then_terminates() -> None:
    reader = _StubReader(
        persisted=[_event(0, "turn.start"), _event(1), _event(2, "turn.end")],
        live=[],
    )
    assert await _collect(reader, after_seq=-1) == [0, 1, 2]


async def test_resume_skips_already_seen_no_duplicate_no_gap() -> None:
    """Client last saw seq=1. It must get 2,3,4 exactly — never 1 again (no duplicate), never a
    skip past 2 (no gap). Here 2,3 are in the durable log and 4 arrives live."""
    reader = _StubReader(
        persisted=[_event(0), _event(1), _event(2), _event(3)],
        live=[_event(4, "turn.end")],
    )
    assert await _collect(reader, after_seq=1) == [2, 3, 4]


async def test_overlap_between_replay_and_live_is_deduplicated() -> None:
    """An event present in BOTH the durable log (replayed) and the live buffer (captured during
    replay) must be emitted once. seq=2 is in both; the upper-boundary dedup drops the live copy."""
    reader = _StubReader(
        persisted=[_event(0), _event(1), _event(2)],
        live=[_event(2), _event(3, "turn.end")],  # seq=2 overlaps replay
    )
    assert await _collect(reader, after_seq=-1) == [0, 1, 2, 3]  # not [0,1,2,2,3]


async def test_terminal_in_replay_stops_before_live() -> None:
    """If the durable log already contains turn.end, the stream terminates on it — a reconnect
    after the turn finished replays the tail and closes, never hanging on a dead live channel."""
    reader = _StubReader(
        persisted=[_event(0), _event(1, "turn.end")],
        live=[_event(2)],  # must never be emitted — terminal already reached in replay
    )
    assert await _collect(reader, after_seq=-1) == [0, 1]


def _thread(*turn_ids: str) -> Thread:
    now = datetime.now(UTC)
    turns = tuple(
        Turn(
            id=turn_id,
            thread_id="th1",
            user_id="u1",
            role=TurnRole.USER,
            status=TurnStatus.DONE,
            created_at=now,
        )
        for turn_id in turn_ids
    )
    return Thread(
        id="th1",
        user_id="u1",
        title="t",
        last_message_at=now,
        created_at=now,
        updated_at=now,
        turns=turns,
    )


def test_a_reconnect_follows_the_turn_another_worker_is_running() -> None:
    """A regenerated answer streams under an id with no saved turn yet; the thread's latest saved
    turn is the question, whose events are the previous answer's."""
    remote = RemoteTurn(turn_id="regen-1", user_id="u1", thread_id="th1")

    chosen = _resumable_turn_id(
        running=None, remote=remote, user_id="u1", thread=_thread("q1", "a1", "q2")
    )

    assert chosen == "regen-1"


def test_a_reconnect_with_nothing_running_uses_the_latest_saved_turn() -> None:
    chosen = _resumable_turn_id(running=None, remote=None, user_id="u1", thread=_thread("q1", "a1"))
    assert chosen == "a1"
    assert _resumable_turn_id(running=None, remote=None, user_id="u1", thread=_thread()) is None


def test_another_users_running_turn_is_never_followed() -> None:
    remote = RemoteTurn(turn_id="regen-1", user_id="someone-else", thread_id="th1")

    chosen = _resumable_turn_id(running=None, remote=remote, user_id="u1", thread=_thread("q1"))

    assert chosen == "q1"
