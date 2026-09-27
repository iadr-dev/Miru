from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

import tools.us_market_index_strip_tools as us_market_index_strip_tools_module
from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tools.base import ToolContext, dispatch_tool
from tools.us_market_index_strip_tools import (
    GetUsMarketIndexStripInput,
    GetUsMarketIndexStripTool,
)


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


class _StubAdapter:
    def __init__(self, fed_rows: list[dict[str, object]]) -> None:
        self.source_id = "STUB"
        self.timing_class = TimingClass.OFFICIAL_EOD
        self._fed_rows = fed_rows

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.CENTRAL_BANK_RATE, Market.GLOBAL)}

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        del cap, sym, tls_policy, params
        return _result({"rows": self._fed_rows})

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        return raw


def _router(fed_rows: list[dict[str, object]]) -> DataAccessRouter:
    adapter = _StubAdapter(fed_rows)
    priority = {"GLOBAL": {"central_bank_rate": ["STUB"]}}
    return DataAccessRouter({"STUB": adapter}, priority_config=priority)


def _fed_row(rate: float, day: str) -> dict[str, object]:
    return {
        "symbol": "FED",
        "market": "GLOBAL",
        "date": day,
        "country": "FED",
        "full_country_name": "United States",
        "interest_rate": rate,
    }


def _patch_index_quotes(
    monkeypatch: pytest.MonkeyPatch, quotes: dict[str, tuple[float | None, float | None]]
) -> None:
    async def fake_fetch_index_quote(yahoo_symbol: str) -> tuple[float | None, float | None]:
        return quotes.get(yahoo_symbol, (None, None))

    monkeypatch.setattr(
        us_market_index_strip_tools_module, "fetch_index_quote", fake_fetch_index_quote
    )


async def test_get_us_market_index_strip_computes_change_for_every_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_index_quotes(
        monkeypatch,
        {
            "^GSPC": (7743.41, 7704.13),
            "^DJI": (51828.62, 51349.98),
            "^IXIC": (27068.72, 26939.37),
            "^SOX": (12668.93, 12492.54),
            "^VIX": (14.87, 15.67),
            "^TNX": (5.184, 5.162),
        },
    )
    tool = GetUsMarketIndexStripTool(
        _router([_fed_row(3.75, "2026-07-30"), _fed_row(3.5, "2026-09-17")])
    )
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(GetUsMarketIndexStripInput(), ToolContext(tier=Tier.FREE))

    by_key = {row.key: row for row in output.rows}
    assert by_key["sp500"].value == 7743.41
    assert round(by_key["sp500"].change, 2) == 39.28
    assert by_key["dow_jones"].value == 51828.62
    assert by_key["nasdaq"].value == 27068.72
    assert by_key["sox"].value == 12668.93
    assert by_key["vix"].value == 14.87
    assert by_key["us10y"].value == 5.184
    assert by_key["fed_rate"].value == 3.5
    assert by_key["fed_rate"].change == -0.25
    assert by_key["fed_rate"].as_of == date(2026, 9, 17)


async def test_get_us_market_index_strip_omits_failed_index_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live yfinance fetch failure for one index must never blank the whole strip — the
    honest-degrade contract `fetch_index_quote` itself returns `(None, None)` on failure, and
    the tool must simply omit that one item, never fabricate a value."""
    _patch_index_quotes(monkeypatch, {"^GSPC": (7743.41, 7704.13)})  # every other symbol fails
    tool = GetUsMarketIndexStripTool(_router([]))

    output = await tool.run(GetUsMarketIndexStripInput(), ToolContext(tier=Tier.FREE))

    by_key = {row.key: row for row in output.rows}
    assert "sp500" in by_key
    assert "dow_jones" not in by_key
    assert "fed_rate" not in by_key  # no fed rows -> omitted


async def test_get_us_market_index_strip_masked_teaser_never_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`DataClass.PUBLIC` — `dispatch_tool` always runs it for real, never a masked teaser."""
    _patch_index_quotes(monkeypatch, {})
    tool = GetUsMarketIndexStripTool(_router([]))

    result = await dispatch_tool(tool, GetUsMarketIndexStripInput(), ToolContext(tier=Tier.FREE))

    assert not hasattr(result, "locked")
