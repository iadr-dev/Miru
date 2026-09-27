"""Finance-calendar adapter tests — mocked HTTP transport, no live network (default profile)."""

from __future__ import annotations

import httpx
import pytest

import sources.financecalendar as financecalendar_module
from schemas.enums import DataClass, Market, TLSPolicy
from schemas.source import HealthStatus
from sources.financecalendar import FinanceCalendarAdapter


def _client_factory(response_json: object, status_code: int = 200) -> object:
    def handler(_request: httpx.Request) -> httpx.Response:
        if status_code >= 400:
            return httpx.Response(status_code, text="upstream error")
        return httpx.Response(status_code, json=response_json)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


async def test_fetch_calendar_parses_real_events(monkeypatch: pytest.MonkeyPatch) -> None:
    response_json = {
        "from": "2026-09-26",
        "to": "2026-10-10",
        "count": 2,
        "events": [
            {
                "date": "2026-09-30",
                "time_utc": "2026-09-30T12:30:00+00:00",
                "time_et": "08:30",
                "all_day": False,
                "name": "US GDP",
                "title": "US Gross Domestic Product September 2026",
                "impact": "high",
                "category": "economic-indicators",
                "consensus": None,
                "prior": None,
                "actual": None,
                "url": "https://www.financecalendar.com/event/us-gdp-september-2026/",
            },
            {
                "date": "2026-10-01",
                "time_utc": "2026-10-01T14:00:00+00:00",
                "time_et": "10:00",
                "all_day": False,
                "name": "ISM Manufacturing PMI",
                "title": "US ISM Manufacturing PMI October 2026",
                "impact": "high",
                "category": "economic-indicators",
                "consensus": None,
                "prior": "55.6",
                "actual": None,
                "url": "https://www.financecalendar.com/event/ism-pmi-october-2026/",
            },
        ],
    }
    monkeypatch.setattr(
        financecalendar_module, "build_async_client", _client_factory(response_json)
    )
    adapter = FinanceCalendarAdapter()

    result = await adapter.fetch_calendar(tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "FINANCE_CALENDAR"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    assert result.currentness_caveat
    events = result.payload["events"]
    assert isinstance(events, list)
    assert len(events) == 2
    gdp = events[0]
    assert gdp["name"] == "US GDP"  # type: ignore[index]
    assert gdp["market"] == "GLOBAL"  # type: ignore[index]
    pmi = events[1]
    assert pmi["prior"] == "55.6"  # type: ignore[index]
    assert pmi["actual"] is None  # type: ignore[index]


async def test_fetch_calendar_passes_impact_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"events": []})

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(financecalendar_module, "build_async_client", factory)
    adapter = FinanceCalendarAdapter()

    await adapter.fetch_calendar(impact="high", tls_policy=TLSPolicy.STRICT)

    params = captured["params"]
    assert isinstance(params, dict)
    assert params["impact"] == "high"
    assert "from" in params
    assert "to" in params


async def test_fetch_calendar_empty_events_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        financecalendar_module, "build_async_client", _client_factory({"events": []})
    )
    adapter = FinanceCalendarAdapter()

    result = await adapter.fetch_calendar(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    events = result.payload["events"]
    assert events == []


async def test_fetch_calendar_degrades_honestly_on_upstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        financecalendar_module, "build_async_client", _client_factory({}, status_code=500)
    )
    adapter = FinanceCalendarAdapter()

    result = await adapter.fetch_calendar(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {}


def test_market_is_global_not_us_only() -> None:
    """The feed genuinely spans multiple countries (US GDP alongside RBA/ECB/BOJ events),
    distinguished only by free-text name/category — same reasoning `EconomicCalendarRow` already
    uses `Market.GLOBAL` for."""
    adapter = FinanceCalendarAdapter()
    assert adapter.source_id == "FINANCE_CALENDAR"
    # Sanity: Market.GLOBAL is a real enum member this module imports and uses.
    assert Market.GLOBAL.value == "GLOBAL"
