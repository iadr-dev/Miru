"""Layer 2 calendar composition tests for the generic `calendar_events`-registered-feed path
(`fetch_feed_events`) — stub router only, no real `sources/*`. `tests/tools/
test_calendar_tools.py` covers the real dividend/CB-put composition (`fetch_dividend_events`/
`fetch_cb_put_events`) end-to-end via `get_calendar_events` against a recorded FinMind
fixture; this file proves the *other* half: a real `calendar_events`-registered adapter (if
one is ever wired for TW beyond the current typhoon-closure-only `sources/ncdr_closure.py`)
merges cleanly, and the graceful-degradation path holds when none is configured."""

from __future__ import annotations

from calendar.events import get_calendar_events
from calendar.models import CalendarEvent, EventState, EventType
from datetime import UTC, date, datetime

import pytest

import sources.ncdr_closure as ncdr_module
from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.ncdr_closure import NcdrClosureAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _rule_engine_event(event_date: date) -> CalendarEvent:
    return CalendarEvent(
        id="futures-settlement-TW-2024-01",
        market=Market.TW,
        type=EventType.FUTURES_SETTLEMENT,
        title_zh="期貨結算日",
        title_en="Futures Settlement Day",
        event_date=event_date,
        state=EventState.SCHEDULED,
        source="rule_engine",
    )


async def test_get_calendar_events_degrades_gracefully_with_no_feed_adapter() -> None:
    """No `calendar_events` adapter configured — the router's own `unavailable` result must
    not crash this composition; rule-engine events still render."""
    symbol = Symbol(id="2330", market=Market.TW)
    router = DataAccessRouter({}, priority_config={"TW": {}})
    rule_event = _rule_engine_event(date(2024, 1, 17))

    events, result = await get_calendar_events(
        router,
        symbol,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        rule_engine_events=(rule_event,),
    )

    assert events == (rule_event,)
    assert result.source_id == "none"
    assert result.health.status is HealthStatus.UNREACHABLE


class _FeedStubAdapter:
    source_id = "STUB_FEED"
    timing_class = TimingClass.EVENT_DRIVEN

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.CALENDAR_EVENTS, Market.TW)}

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: object, **params: object
    ) -> SourceResult:
        return SourceResult(
            source_id=self.source_id,
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.EVENT_DRIVEN,
            fetched_at=datetime.now(UTC),
            currentness_caveat="feed caveat",
            payload={
                "events": [
                    {
                        "id": "agm-2330-2024",
                        "type": "agm",
                        "title_zh": "股東會",
                        "title_en": "AGM",
                        "event_date": "2024-06-20",
                        "state": "scheduled",
                    },
                    {
                        # malformed row — unrecognized state — must be skipped, not crash
                        "id": "bad-row",
                        "type": "agm",
                        "state": "not_a_real_state",
                    },
                ]
            },
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        return raw


async def test_get_calendar_events_merges_feed_rows_and_skips_malformed_ones() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router = DataAccessRouter(
        {"STUB_FEED": _FeedStubAdapter()},
        priority_config={"TW": {"calendar_events": ["STUB_FEED"]}},
    )

    events, result = await get_calendar_events(
        router, symbol, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
    )

    assert result.source_id == "STUB_FEED"
    assert len(events) == 1
    assert events[0].type is EventType.AGM
    assert events[0].event_date == date(2024, 6, 20)


async def test_get_calendar_events_surfaces_real_ncdr_closure_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real, now-registered `NcdrClosureAdapter` end to end — a typhoon closure alert must
    surface as an `EventType.MARKET_CLOSURE` `CalendarEvent`, not be silently dropped for
    lacking the generic feed-row shape `_feed_event_from_row` expects (the bug this test
    guards against: the adapter existed and was `supports()`-correct long before it was wired
    into `dataaccess/composition.py`/`source_priority.yaml` or emitted matching row keys)."""
    body = load_fixture("ncdr_closure", "alerts_typhoon.json")
    monkeypatch.setattr(ncdr_module, "build_async_client", make_json_client_factory(body))
    symbol = Symbol(id="2330", market=Market.TW)
    router = DataAccessRouter(
        {"NCDR_DGPA_CLOSURE_CAP": NcdrClosureAdapter()},
        priority_config={"TW": {"calendar_events": ["NCDR_DGPA_CLOSURE_CAP"]}},
    )

    events, result = await get_calendar_events(
        router, symbol, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
    )

    assert result.source_id == "NCDR_DGPA_CLOSURE_CAP"
    assert len(events) == 3
    assert all(event.type is EventType.MARKET_CLOSURE for event in events)
    assert all(event.state is EventState.CONFIRMED for event in events)
    assert {event.event_date for event in events} == {date(2024, 7, 24)}


async def test_get_calendar_events_filters_to_requested_window() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router = DataAccessRouter({}, priority_config={"TW": {}})
    in_range = _rule_engine_event(date(2024, 1, 17))
    out_of_range = _rule_engine_event(date(2024, 3, 20))

    events, _ = await get_calendar_events(
        router,
        symbol,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        rule_engine_events=(in_range, out_of_range),
    )

    assert events == (in_range,)
