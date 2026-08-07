"""E2E tests for `get_calendar_events` via `dispatch_tool` — proves the tool returns real
dividend/CB events sourced from a recorded FinMind fixture (not just an always-empty feed),
and that a symbol with no matching fixture rows still degrades to an empty, not error, event
list."""

from __future__ import annotations

from calendar.models import EventState, EventType

import httpx
import pytest

import sources.finmind as finmind_module
from config.settings import Settings
from dataaccess.router import DataAccessRouter
from schemas.enums import Market, Tier
from sources.finmind import FinMindAdapter
from tests.sources.conftest import load_fixture
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.calendar_tools import GetCalendarEventsOutput, GetCalendarEventsTool
from tools.common import DateRangeInput

_PRIORITY = {
    Market.TW.value: {
        "dividends": ["finmind"],
        "cb_put_provision": ["finmind"],
    }
}


def _settings() -> Settings:
    return Settings(
        finmind_api_key="test-token", finmind_base_url="https://api.finmindtrade.com/api/v4"
    )


def _dataset_dispatching_client(
    monkeypatch: pytest.MonkeyPatch, bodies_by_dataset: dict[str, object]
) -> None:
    """A single mock transport that answers differently depending on the `dataset` query
    param — needed because `get_calendar_events` fetches two distinct FinMind datasets
    (`TaiwanStockDividend`, `TaiwanStockConvertibleBondPutProvision`) through the same
    adapter in one tool call."""

    def handler(request: httpx.Request) -> httpx.Response:
        dataset = request.url.params.get("dataset", "")
        body = bodies_by_dataset.get(dataset, {"msg": "success", "status": 200, "data": []})
        return httpx.Response(200, json=body)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(finmind_module, "build_async_client", factory)


async def test_get_calendar_events_returns_real_dividend_and_cb_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symbol with recorded dividend + CB-put fixture data must yield real
    `dividend_ex_rights` and `cb_put_or_maturity` events, not an empty feed list — the exact
    gap this dispatch closes."""
    dividends_body = load_fixture("finmind", "dividends_2330.json")
    cb_put_body = load_fixture("finmind", "cb_put_provision.json")
    _dataset_dispatching_client(
        monkeypatch,
        {
            "TaiwanStockDividend": dividends_body,
            "TaiwanStockConvertibleBondPutProvision": cb_put_body,
        },
    )
    adapter = FinMindAdapter(settings=_settings())
    router = DataAccessRouter({"finmind": adapter}, priority_config=_PRIORITY)
    tool = GetCalendarEventsTool(router)

    args = DateRangeInput(
        id="2330", market=Market.TW, start_date="2011-01-01", end_date="2025-12-31"
    )
    output = await dispatch_tool(tool, args, ToolContext(tier=Tier.PRO))

    assert isinstance(output, GetCalendarEventsOutput)
    dividend_events = [e for e in output.events if e.type == EventType.DIVIDEND_EX_RIGHTS.value]
    cb_events = [e for e in output.events if e.type == EventType.CB_PUT_OR_MATURITY.value]
    assert dividend_events, "expected a real 除權息 event from the recorded fixture"
    assert dividend_events[0].event_date is not None
    assert dividend_events[0].event_date.isoformat() == "2024-06-17"
    assert dividend_events[0].state == EventState.RELEASED.value  # 2024-06-17 is in the past
    assert cb_events, "expected a real CB put-provision event from the recorded fixture"
    assert cb_events[0].event_date is not None
    assert cb_events[0].event_date.isoformat() == "2011-06-22"


async def test_get_calendar_events_masked_for_unentitled_tier() -> None:
    """The tool bundles Sponsor-tier CB data, so it is classed `sponsor` as a whole — an
    unentitled caller gets the masked teaser, never a hard error and never real data."""
    router = DataAccessRouter({}, priority_config={"TW": {}})
    tool = GetCalendarEventsTool(router)
    args = DateRangeInput(id="2330", market=Market.TW)

    output = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))

    assert isinstance(output, MaskedTeaser)


async def test_get_calendar_events_degrades_gracefully_with_no_matching_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symbol with no dividend/CB rows in the fixture data still returns an empty (not
    error) event list — no regression on the existing degrade-gracefully behavior."""
    empty_body = {"msg": "success", "status": 200, "data": []}
    _dataset_dispatching_client(
        monkeypatch,
        {
            "TaiwanStockDividend": empty_body,
            "TaiwanStockConvertibleBondPutProvision": empty_body,
        },
    )
    adapter = FinMindAdapter(settings=_settings())
    router = DataAccessRouter({"finmind": adapter}, priority_config=_PRIORITY)
    tool = GetCalendarEventsTool(router)

    args = DateRangeInput(
        id="9999", market=Market.TW, start_date="2024-01-01", end_date="2024-12-31"
    )
    output = await dispatch_tool(tool, args, ToolContext(tier=Tier.PRO))

    assert isinstance(output, GetCalendarEventsOutput)
    assert output.events == ()
