"""`SqlAlchemyUsageStore`: the atomic reserve-before-act daily-chat counter and the idempotent
per-turn rollup write-through."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from usage.store import SqlAlchemyUsageStore


def _store(session_factory) -> SqlAlchemyUsageStore:
    return SqlAlchemyUsageStore(session_factory)


async def test_reserve_allows_up_to_limit_then_refuses(session_factory) -> None:
    store = _store(session_factory)
    day = date(2026, 8, 5)

    v1 = await store.reserve_daily_chat(user_id="u", limit=2, today=day)
    v2 = await store.reserve_daily_chat(user_id="u", limit=2, today=day)
    v3 = await store.reserve_daily_chat(user_id="u", limit=2, today=day)

    assert (v1.allowed, v1.used) == (True, 1)
    assert (v2.allowed, v2.used) == (True, 2)
    assert v3.allowed is False


async def test_refused_reservation_is_refunded(session_factory) -> None:
    """A rejected turn must not burn the day's allowance: after an over-cap refusal, the counter
    stays at the cap (the over-claim was rolled back), so raising the cap later admits again."""
    store = _store(session_factory)
    day = date(2026, 8, 5)
    await store.reserve_daily_chat(user_id="u", limit=1, today=day)
    refused = await store.reserve_daily_chat(user_id="u", limit=1, today=day)
    assert refused.allowed is False
    # With the over-claim refunded, exactly one more slot exists under a cap of 2.
    again = await store.reserve_daily_chat(user_id="u", limit=2, today=day)
    assert again.allowed is True
    assert again.used == 2


async def test_unlimited_never_gated_and_no_row(session_factory) -> None:
    store = _store(session_factory)
    for _ in range(50):
        verdict = await store.reserve_daily_chat(user_id="pro", limit=None)
        assert verdict.allowed is True
        assert verdict.limit is None


async def test_separate_days_have_separate_counters(session_factory) -> None:
    store = _store(session_factory)
    await store.reserve_daily_chat(user_id="u", limit=1, today=date(2026, 8, 5))
    # A new day is a fresh window key — the cap resets.
    fresh = await store.reserve_daily_chat(user_id="u", limit=1, today=date(2026, 8, 6))
    assert fresh.allowed is True


async def test_record_turn_is_idempotent(session_factory) -> None:
    store = _store(session_factory)
    first = await store.record_turn(
        user_id="u",
        turn_id="t1",
        input_tokens=100,
        output_tokens=50,
        tool_calls=2,
        finmind_calls=1,
        cost_micros=1200,
    )
    second = await store.record_turn(
        user_id="u",
        turn_id="t1",
        input_tokens=100,
        output_tokens=50,
        tool_calls=2,
        finmind_calls=1,
        cost_micros=1200,
    )
    assert first is True
    assert second is False  # (user_id, turn_id) collision → no-op, never a double-count


@pytest.mark.concurrency
async def test_concurrent_reservations_never_exceed_cap(session_factory) -> None:
    """The atomic increment-and-check is what makes this safe: many concurrent reservations for
    one user in one window must admit at most `limit`, never more."""
    store = _store(session_factory)
    day = date(2026, 8, 5)
    verdicts = await asyncio.gather(
        *(store.reserve_daily_chat(user_id="u", limit=5, today=day) for _ in range(25))
    )
    assert sum(1 for v in verdicts if v.allowed) == 5


async def test_a_refunded_chat_gives_its_slot_back(session_factory) -> None:
    """A duplicate of a message already accepted claims a chat before it is found out; refunding it
    leaves the day's allowance exactly as it was."""
    store = _store(session_factory)
    day = date(2026, 8, 5)
    await store.reserve_daily_chat(user_id="u", limit=2, today=day)  # the real message
    await store.reserve_daily_chat(user_id="u", limit=2, today=day)  # its duplicate
    await store.refund_daily_chat(user_id="u", limit=2, today=day)

    again = await store.reserve_daily_chat(user_id="u", limit=2, today=day)

    assert again.allowed is True
    assert again.used == 2  # the message, and this one: the duplicate no longer counts


async def test_refunding_an_unlimited_chat_touches_nothing(session_factory) -> None:
    store = _store(session_factory)

    await store.refund_daily_chat(user_id="pro", limit=None)

    assert (await store.today_counts(user_id="pro")).get("chats", 0) == 0
