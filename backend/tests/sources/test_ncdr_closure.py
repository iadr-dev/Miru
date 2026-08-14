"""NCDR/DGPA closure adapter tests — recorded fixture, no live network."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date

import pytest

import sources.ncdr_closure as ncdr_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.ncdr_closure import NcdrClosureAdapter
from tests.sources.conftest import load_fixture, make_json_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(ncdr_module, "build_async_client", make_json_client_factory(body))


def test_supports_calendar_events_tw_only() -> None:
    adapter = NcdrClosureAdapter()
    assert adapter.supports() == {(Capability.CALENDAR_EVENTS, Market.TW)}


def test_validate_rejects_non_list_entry() -> None:
    adapter = NcdrClosureAdapter()
    with pytest.raises(ValueError, match="neither an object nor a list"):
        adapter.validate({"entry": "nope"})


def test_validate_accepts_missing_entry_as_no_active_alerts() -> None:
    adapter = NcdrClosureAdapter()
    assert adapter.validate({"id": "x"}) == {"id": "x"}


async def test_fetch_extracts_closure_dates(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("ncdr_closure", "alerts_typhoon.json")
    _patch_client(monkeypatch, body)
    adapter = NcdrClosureAdapter()
    sym = Symbol(id="", market=Market.TW)

    result = await adapter.fetch(Capability.CALENDAR_EVENTS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "NCDR_DGPA_CLOSURE_CAP"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    closure_dates = result.payload["closure_dates"]
    assert closure_dates == ["2024-07-24"]  # type: ignore[comparison-overlap]
    events = result.payload["events"]
    assert len(events) == 3  # type: ignore[arg-type]
    areas = {event["area"] for event in events}  # type: ignore[union-attr]
    assert areas == {"基隆市", "新北市", "台北市"}
    # `calendar/events.py::_feed_event_from_row`'s expected keys must be present on every row
    # so a registered NCDR adapter's rows actually surface as CalendarEvents, not silently
    # skipped for lacking type/state/event_date.
    first = events[0]  # type: ignore[index]
    assert first["type"] == "market_closure"  # type: ignore[index]
    assert first["state"] == "confirmed"  # type: ignore[index]
    assert first["event_date"] == first["date"]  # type: ignore[index]
    assert first["title_zh"]  # type: ignore[index]
    assert first["title_en"]  # type: ignore[index]


def test_closure_dates_feed_trading_day_calendar() -> None:
    """The `closure_dates` payload shape must slot directly into
    `TradingDayCalendar(closure_dates=...)` with no reshaping."""
    closure_dates_raw = ["2024-07-24"]
    calendar = TradingDayCalendar(
        closure_dates=frozenset(date.fromisoformat(d) for d in closure_dates_raw)
    )

    assert calendar.is_trading_day(date(2024, 7, 24)) is False
    assert calendar.is_trading_day(date(2024, 7, 25)) is True


async def test_fetch_no_active_alerts_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    body = load_fixture("ncdr_closure", "alerts_none.json")
    _patch_client(monkeypatch, body)
    adapter = NcdrClosureAdapter()
    sym = Symbol(id="", market=Market.TW)

    result = await adapter.fetch(Capability.CALENDAR_EVENTS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {"closure_dates": [], "events": []}


async def test_fetch_unsupported_capability() -> None:
    adapter = NcdrClosureAdapter()
    sym = Symbol(id="", market=Market.TW)

    result = await adapter.fetch(Capability.PRICE_EOD, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER
