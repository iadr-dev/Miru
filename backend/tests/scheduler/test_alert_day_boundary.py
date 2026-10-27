"""The Taipei midnight is 16:00 UTC. A Taiwan alert must treat the Taipei calendar day as "today"
(the market's own day), never the UTC date, and the suite must not depend on which side of that
boundary it runs on. Regression for CI runs that straddled 16:00 UTC: the module's "today" was
taken before the boundary and the rule created after it."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from scheduler.templates import ClosePrice
from schemas.enums import Capability, Tier
from tests.scheduler import clock
from tests.scheduler.conftest import insert_user
from tests.scheduler.test_alert_engine import (
    PRICE,
    FakeReader,
    bar,
    create_rule,
    make_worker,
    notifications,
    signal,
)

BEFORE_MIDNIGHT = datetime(2026, 10, 26, 15, 59, tzinfo=UTC)  # Taipei Mon 23:59
JUST_AFTER = datetime(2026, 10, 26, 16, 5, tzinfo=UTC)  # Taipei Tue 00:05, UTC still Mon
MONDAY, TUESDAY = date(2026, 10, 26), date(2026, 10, 27)


async def test_a_rule_created_after_taipei_midnight_fires_on_the_taipei_day(session_factory):
    clock.set_now(JUST_AFTER)
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, ClosePrice(comparator="above", value=1))
    reader = FakeReader()
    reader.put(PRICE, TUESDAY, [bar("2330", 612.0)])  # the UTC date would say Monday
    worker = make_worker(session_factory, reader)
    assert await signal(worker, Capability.PRICE_EOD, TUESDAY) == [rule.id]


async def test_data_from_the_taipei_day_before_the_rule_never_fires_it(session_factory):
    clock.set_now(JUST_AFTER)
    user = await insert_user(session_factory, tier=Tier.FREE)
    await create_rule(session_factory, user, ClosePrice(comparator="above", value=1))
    reader = FakeReader()
    reader.put(PRICE, MONDAY, [bar("2330", 612.0)])  # Monday's close, rule born Tuesday Taipei
    worker = make_worker(session_factory, reader)
    assert await signal(worker, Capability.PRICE_EOD, MONDAY) == []
    assert await notifications(session_factory, user) == []


async def test_a_rule_created_before_taipei_midnight_still_fires_on_the_next_days_data(
    session_factory,
):
    clock.set_now(BEFORE_MIDNIGHT)
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, ClosePrice(comparator="above", value=1))
    clock.set_now(JUST_AFTER)  # the run crosses the boundary between creating and evaluating
    reader = FakeReader()
    reader.put(PRICE, TUESDAY, [bar("2330", 612.0)])
    worker = make_worker(session_factory, reader)
    assert await signal(worker, Capability.PRICE_EOD, TUESDAY) == [rule.id]


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 10, 26, 16, 5, tzinfo=UTC),
        datetime(2026, 10, 26, 23, 59, tzinfo=UTC),
        datetime(2026, 10, 27, 0, 1, tzinfo=UTC),
        datetime(2026, 10, 27, 8, 1, tzinfo=UTC),
    ],
)
async def test_the_sweep_reads_the_taipei_day_at_any_hour(session_factory, instant):
    """`reconcile_alerts` asks for the latest partition on or before the market's local date; at
    these instants (Taipei 00:05 to 16:01 on Tuesday) that is Tuesday, while the UTC date is
    Monday until 00:00Z."""
    clock.set_now(instant)
    user = await insert_user(session_factory, tier=Tier.FREE)
    rule = await create_rule(session_factory, user, ClosePrice(comparator="above", value=1))
    reader = FakeReader()
    reader.put(PRICE, TUESDAY, [bar("2330", 612.0)])
    worker = make_worker(session_factory, reader)
    assert await worker.reconcile_alerts(now=instant) == [rule.id]
