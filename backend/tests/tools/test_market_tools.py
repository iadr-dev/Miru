from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from dataaccess.router import DataAccessRouter
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.common import DateRangeInput, SymbolInput
from tools.market import (
    GetActiveEtfReportInput,
    GetActiveEtfReportTool,
    GetChipFlowsTool,
    GetCompanyProfileTool,
    GetDailySignalInput,
    GetDailySignalTool,
    GetDividendScheduleTool,
    GetEtfMoneyFlowInput,
    GetEtfMoneyFlowTool,
    GetEtfsHoldingStockInput,
    GetEtfsHoldingStockTool,
    GetHolderAccumulationInput,
    GetHolderAccumulationTool,
    GetIndexContributionInput,
    GetIndexContributionTool,
    GetInstitutionalFlowTool,
    GetLivePriceQuoteTool,
    GetPriceQuoteTool,
    HoldingStatus,
)
from tools.market.chip_flows import _CHIP_BROKER_MAX_TRADING_DAYS, InstitutionalFlowRollingNet


def _bar_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="price caveat",
        payload={
            "bars": [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "trade_date": "2026-08-01",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1000,
                    "turnover": None,
                }
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _dividend_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="dividend caveat",
        payload={
            "events": [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "announced_date": "2026-06-01",
                    "fiscal_year": "2025",
                    "cash_dividend": 10.0,
                    "stock_dividend": None,
                    "cash_ex_dividend_date": None,
                    "stock_ex_dividend_date": None,
                }
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _institutional_flow_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="flow caveat",
        payload={
            "rows": [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "trade_date": "2026-08-01",
                    "investor": "Foreign_Investor",
                    "buy": 1000,
                    "sell": 500,
                }
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _chip_broker_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="chip caveat",
        payload={
            "rows": [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "trade_date": "2026-08-01",
                    "broker_id": "1020",
                    "broker_name": "Fubon",
                    "price": 100.0,
                    "buy": 500,
                    "sell": 100,
                }
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _market_value_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="market value caveat",
        payload={
            "rows": [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "trade_date": "2026-08-01",
                    "market_value": 15000000000,
                }
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _valuation_ratios_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="valuation ratios caveat",
        payload={
            "rows": [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "trade_date": "2026-08-01",
                    "dividend_yield": 2.1,
                    "per": 18.5,
                    "pbr": 5.2,
                }
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _income_statement_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="income statement caveat",
        payload={
            "lines": [
                {
                    "symbol": "2330",
                    "market": "TW",
                    "period_end": "2025-12-31",
                    "statement_type": "income_statement",
                    "item": "EPS",
                    "origin_name": "基本每股盈餘（元）",
                    "value": 9.21,
                },
                {
                    "symbol": "2330",
                    "market": "TW",
                    "period_end": "2025-12-31",
                    "statement_type": "income_statement",
                    "item": "Revenue",
                    "origin_name": "營業收入",
                    "value": 625529765000.0,
                },
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _sector_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="sector caveat",
        payload={
            "classification": {
                "symbol": "2330",
                "market": "TW",
                "sector": "Tech",
                "industry": "Semis",
            }
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_price_quote_delegates_to_price_eod_capability() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.PRICE_EOD: _bar_result()})
    tool = GetPriceQuoteTool(router)

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.PRICE_EOD
    assert output.bar is not None
    assert output.bar.close == 100.5


async def test_get_live_price_quote_delegates_to_live_price_quote_capability() -> None:
    symbol = Symbol(id="AAPL", market=Market.US)
    tick_result = SourceResult(
        source_id="YFINANCE_LIVE",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.LIVE_ISH,
        fetched_at=datetime.now(UTC),
        currentness_caveat="live caveat",
        payload={"tick": {"price": 230.5, "change_percent": 1.2}},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, adapter = make_router(symbol, {Capability.LIVE_PRICE_QUOTE: tick_result})
    tool = GetLivePriceQuoteTool(router)

    output = await tool.run(SymbolInput(id="AAPL", market=Market.US), ToolContext())

    assert adapter.calls[0][0] is Capability.LIVE_PRICE_QUOTE
    assert output.tick is not None
    assert output.tick.price == 230.5
    assert output.tick.change_percent == 1.2


async def test_get_live_price_quote_rejects_non_us_market_without_touching_router() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.LIVE_PRICE_QUOTE: _bar_result()})
    tool = GetLivePriceQuoteTool(router)

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls == []
    assert output.tick is None
    assert output.health_status is HealthStatus.UNAVAILABLE_TIER


async def test_get_dividend_schedule_delegates_and_parses_events() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.DIVIDENDS: _dividend_result()})
    tool = GetDividendScheduleTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.DIVIDENDS
    assert output.events[0].cash_dividend == 10.0


async def test_get_dividend_schedule_attaches_a_bar_chart_hint_when_events_exist() -> None:
    """A dozen+ announced quarters is materially clearer as a bar chart than the raw event-list
    card (see the live screenshot complaint this migration fixes) — `chart_hint` must be present
    whenever there's at least one event to plot."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, _adapter = make_router(symbol, {Capability.DIVIDENDS: _dividend_result()})
    tool = GetDividendScheduleTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert output.chart_hint is not None
    assert output.chart_hint.mark.value == "bar"
    assert output.chart_hint.x is not None
    assert output.chart_hint.x.field == "announced_date"
    assert output.chart_hint.y is not None
    assert output.chart_hint.y.field == "cash_dividend"


async def test_get_dividend_schedule_omits_chart_hint_when_no_events() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    empty_result = SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="dividend caveat",
        payload={"events": []},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _adapter = make_router(symbol, {Capability.DIVIDENDS: empty_result})
    tool = GetDividendScheduleTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert output.chart_hint is None


async def test_get_institutional_flow_delegates_and_parses_rows() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(
        symbol, {Capability.INSTITUTIONAL_FLOW: _institutional_flow_result()}
    )
    tool = GetInstitutionalFlowTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.INSTITUTIONAL_FLOW
    assert output.rows[0].investor == "Foreign_Investor"
    # Only one trading day is present in the fixture — every rolling window (5/10/20 days)
    # must come back None rather than a partial-window figure silently mislabeled as the full
    # window (see `InstitutionalFlowRollingNet`'s docstring).
    assert output.rolling_net == (
        InstitutionalFlowRollingNet(
            investor="Foreign_Investor", net_5d=None, net_10d=None, net_20d=None
        ),
    )


async def test_get_institutional_flow_computes_rolling_net_per_investor() -> None:
    """Regression guard for Finding #69: `chip-flow-review` returned zero-prose answers because
    the model manually summed 20+ raw daily rows across investor categories in its own
    reasoning. `rolling_net` must carry that pre-computed 5-day figure so the model can cite it
    directly instead."""
    symbol = Symbol(id="2330", market=Market.TW)
    rows = [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": f"2026-08-{day:02d}",
            "investor": investor,
            "buy": buy,
            "sell": sell,
        }
        for day, investor, buy, sell in [
            (1, "Foreign_Investor", 1000, 500),
            (2, "Foreign_Investor", 800, 900),
            (3, "Foreign_Investor", 1200, 700),
            (4, "Foreign_Investor", 600, 600),
            (5, "Foreign_Investor", 500, 300),
            (1, "Investment_Trust", 300, 100),
            (2, "Investment_Trust", 200, 250),
            (3, "Investment_Trust", 400, 100),
            (4, "Investment_Trust", 100, 150),
            (5, "Investment_Trust", 300, 200),
        ]
    ]
    result = SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="flow caveat",
        payload={"rows": rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _adapter = make_router(symbol, {Capability.INSTITUTIONAL_FLOW: result})
    tool = GetInstitutionalFlowTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    by_investor = {r.investor: r for r in output.rolling_net}
    # sum(buy - sell) over the 5 fetched days: (500)+(-100)+(500)+(0)+(200) = 1100
    assert by_investor["Foreign_Investor"].net_5d == 1100.0
    assert by_investor["Foreign_Investor"].net_10d is None  # fewer than 10 days fetched
    assert by_investor["Investment_Trust"].net_5d == 200 + (-50) + 300 + (-50) + 100


async def test_get_chip_flows_run_returns_real_sponsor_data_for_an_entitled_caller() -> None:
    """`run()` itself computes the row-preview mask from `ctx.tier` (governance.md §4
    top-N-preview exception) — an entitled caller's `run()` call gets every row real."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.CHIP_BROKER: _chip_broker_result()})
    tool = GetChipFlowsTool(router)
    assert tool.data_class is DataClass.SPONSOR
    assert tool.supports_row_preview is True

    output = await tool.run(
        DateRangeInput(id="2330", market=Market.TW), ToolContext(tier=Tier.PREMIUM)
    )

    assert adapter.calls[0][0] is Capability.CHIP_BROKER
    assert output.rows[0].broker_name == "Fubon"
    assert output.top_brokers[0].broker_name == "Fubon"
    assert output.top_brokers[0].net == 500 - 100
    assert output.buy_top[0].broker_name == "Fubon"
    assert output.buy_top[0].locked is False


async def test_get_chip_flows_masks_broker_top_rows_beyond_free_preview_for_unentitled_caller() -> (
    None
):
    """governance.md §4 top-N-preview exception: an unentitled (`Tier.FREE`) caller still gets
    real aggregate stats (`total_volume`/`main_force_net`) and the top rows of `buy_top`, but
    every row beyond the free preview has its identity AND value fields nulled — and `rows`/
    `top_brokers` (the raw per-day feed) are empty entirely."""
    symbol = Symbol(id="2330", market=Market.TW)
    rows = [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": "2026-08-01",
            "broker_id": broker_id,
            "broker_name": name,
            "price": 100.0,
            "buy": buy,
            "sell": sell,
        }
        for broker_id, name, buy, sell in [
            ("1001", "Big Buyer", 10_000, 1_000),
            ("1002", "Mid Buyer", 3_000, 1_000),
            ("1003", "Small Buyer", 1_000, 900),
        ]
    ]
    result = SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="chip caveat",
        payload={"rows": rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _adapter = make_router(symbol, {Capability.CHIP_BROKER: result})
    tool = GetChipFlowsTool(router)
    input_args = DateRangeInput(id="2330", market=Market.TW)

    free_output = await tool.run(input_args, ToolContext(tier=Tier.FREE))
    assert free_output.free_row_count == 2
    # total_volume/main_force_net are always real (never masked) even for an unentitled caller.
    assert free_output.total_volume == (10_000 + 1_000) + (3_000 + 1_000) + (1_000 + 900)
    assert free_output.main_force_net == (10_000 - 1_000) + (3_000 - 1_000) + (1_000 - 900)
    assert free_output.rows == ()
    assert free_output.top_brokers == ()
    assert free_output.buy_top[0].locked is False
    assert free_output.buy_top[0].broker_id == "1001"
    assert free_output.buy_top[1].locked is False
    locked_row = free_output.buy_top[2]
    assert locked_row.locked is True
    assert locked_row.rank == 3
    assert locked_row.broker_id is None
    assert locked_row.broker_name is None
    assert locked_row.net is None

    premium_output = await tool.run(input_args, ToolContext(tier=Tier.PREMIUM))
    assert premium_output.free_row_count == 3
    assert all(not row.locked for row in premium_output.buy_top)
    assert premium_output.rows != ()


async def test_get_chip_flows_top_brokers_ranks_by_net_magnitude_across_branches() -> None:
    """Regression guard for the live repro against 鴻海/2317 (a follow-on from Finding #69): with
    only raw per-day per-branch rows and no pre-aggregation, the model spent its whole response
    budget manually eyeballing/summing 6,500+ rows and produced zero prose. `top_brokers` must
    rank by |net| across the *whole* fetched range, keeping only the largest movers, so the
    model never needs to re-derive this from `rows` itself."""
    symbol = Symbol(id="2330", market=Market.TW)
    rows = [
        {
            "symbol": "2330",
            "market": "TW",
            "trade_date": trade_date,
            "broker_id": broker_id,
            "broker_name": name,
            "price": 100.0,
            "buy": buy,
            "sell": sell,
        }
        for trade_date, broker_id, name, buy, sell in [
            ("2026-08-01", "1001", "Big Buyer", 10_000, 1_000),
            ("2026-08-02", "1001", "Big Buyer", 8_000, 1_000),
            ("2026-08-01", "1002", "Small Fry", 100, 50),
            ("2026-08-01", "1003", "Big Seller", 500, 9_000),
        ]
    ]
    result = SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="chip caveat",
        payload={"rows": rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _adapter = make_router(symbol, {Capability.CHIP_BROKER: result})
    tool = GetChipFlowsTool(router)

    output = await tool.run(
        DateRangeInput(id="2330", market=Market.TW), ToolContext(tier=Tier.PREMIUM)
    )

    by_broker = {b.broker_id: b for b in output.top_brokers}
    assert by_broker["1001"].net == (10_000 - 1_000) + (8_000 - 1_000)  # 16,000
    assert by_broker["1003"].net == 500 - 9_000  # -8,500
    assert by_broker["1002"].net == 100 - 50  # 50 — still present, just ranked last
    # Ranked by |net| descending: Big Buyer (16,000) > Big Seller (-8,500) > Small Fry (50).
    assert [b.broker_id for b in output.top_brokers] == ["1001", "1003", "1002"]


class _PerDateChipBrokerAdapter:
    """A stub `SourceAdapter` whose `CHIP_BROKER` response depends on the `date=` kwarg it's
    called with — `StubAdapter` (the shared test helper) always returns the same canned result
    regardless of params, which can't exercise `GetChipFlowsTool`'s new per-trading-day fan-out
    (one `router.fetch(..., date=X)` call per day, merged)."""

    source_id = "STUB"
    timing_class = TimingClass.OFFICIAL_EOD

    def __init__(self, rows_by_date: dict[str, list[dict[str, object]]]) -> None:
        self._rows_by_date = rows_by_date
        self.calls: list[dict[str, object]] = []

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.CHIP_BROKER, Market.TW)}

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy, **params: object
    ) -> SourceResult:
        self.calls.append(params)
        day = str(params.get("date"))
        return SourceResult(
            source_id=self.source_id,
            data_class=DataClass.SPONSOR,
            timing_class=self.timing_class,
            fetched_at=datetime.now(UTC),
            currentness_caveat="chip caveat",
            payload={"rows": self._rows_by_date.get(day, [])},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        return raw


async def test_get_chip_flows_multi_day_range_fans_out_and_merges_per_trading_day() -> None:
    """`CHIP_BROKER`'s upstream is single-day-per-call — a `start_date`/`end_date` range must
    issue one `router.fetch(..., date=X)` per trading day in the window and merge every day's
    rows, not just fetch the single most recent day."""

    def _row(trade_date: str, buy: int, sell: int) -> dict[str, object]:
        return {
            "symbol": "2330",
            "market": "TW",
            "trade_date": trade_date,
            "broker_id": "1020",
            "broker_name": "Fubon",
            "price": 100.0,
            "buy": buy,
            "sell": sell,
        }

    # 2026-08-03/04 are a Mon/Tue — both real trading days under plain weekday math.
    adapter = _PerDateChipBrokerAdapter(
        {
            "2026-08-03": [_row("2026-08-03", 500, 100)],
            "2026-08-04": [_row("2026-08-04", 200, 50)],
        }
    )
    router = DataAccessRouter(
        {"STUB": adapter},
        priority_config={"TW": {"chip_broker": ["STUB"]}},
    )
    tool = GetChipFlowsTool(router)

    output = await tool.run(
        DateRangeInput(
            id="2330", market=Market.TW, start_date=date(2026, 8, 3), end_date=date(2026, 8, 4)
        ),
        ToolContext(tier=Tier.PREMIUM),
    )

    called_dates = {str(call.get("date")) for call in adapter.calls}
    assert called_dates == {"2026-08-03", "2026-08-04"}
    assert {row.trade_date.isoformat() for row in output.rows} == {"2026-08-03", "2026-08-04"}
    assert output.top_brokers[0].net == (500 - 100) + (200 - 50)


async def test_get_chip_flows_wide_range_caps_at_max_trading_days() -> None:
    """A window wider than `_CHIP_BROKER_MAX_TRADING_DAYS` must not fan out to every trading day
    in it — capped, most-recent-first, so one page load never issues an unbounded number of
    Sponsor-tier FinMind calls."""
    adapter = _PerDateChipBrokerAdapter({})
    router = DataAccessRouter(
        {"STUB": adapter},
        priority_config={"TW": {"chip_broker": ["STUB"]}},
    )
    tool = GetChipFlowsTool(router)

    await tool.run(
        DateRangeInput(
            id="2330", market=Market.TW, start_date=date(2025, 1, 1), end_date=date(2026, 1, 1)
        ),
        ToolContext(),
    )

    assert len(adapter.calls) == _CHIP_BROKER_MAX_TRADING_DAYS


def _active_etf_holding_result(*, health_status: HealthStatus = HealthStatus.OK) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="active etf holding caveat",
        payload={
            "holdings": [
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-06",
                    "component_stock_id": "2330",
                    "component_stock_name": "台積電",
                    "asset_type": "stock",
                    "shares": 100000,
                    "weight": 8.5,
                    "market_value": 59300000.0,
                    "currency": "TWD",
                },
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-06",
                    "component_stock_id": "2454",
                    "component_stock_name": "聯發科",
                    "asset_type": "stock",
                    "shares": 50000,
                    "weight": 5.0,
                    "market_value": 30000000.0,
                    "currency": "TWD",
                },
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-07",
                    "component_stock_id": "2330",
                    "component_stock_name": "台積電",
                    "asset_type": "stock",
                    "shares": 120000,
                    "weight": 9.2,
                    "market_value": 71160000.0,
                    "currency": "TWD",
                },
                # 2454 is absent on 2025-05-07 -> fully sold (status = 刪除)
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-07",
                    "component_stock_id": "2317",
                    "component_stock_name": "鴻海",
                    "asset_type": "stock",
                    "shares": 40000,
                    "weight": 3.0,
                    "market_value": 5000000.0,
                    "currency": "TWD",
                },
            ]
        },
        health=SourceHealth(status=health_status, detail="ok"),
    )


def _active_etf_holding_change_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="active etf holding change caveat",
        payload={
            "changes": [
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-07",
                    "component_stock_id": "2330",
                    "component_stock_name": "台積電",
                    "buy": 20000,
                    "sell": 0,
                },
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-07",
                    "component_stock_id": "2454",
                    "component_stock_name": "聯發科",
                    "buy": 0,
                    "sell": 50000,
                },
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-07",
                    "component_stock_id": "2317",
                    "component_stock_name": "鴻海",
                    "buy": 40000,
                    "sell": 0,
                },
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _write_price_partitions(store: ParquetStore) -> None:
    """`fund_size`/`fund_size_prior` are recomputed from `shares x close`, read from the
    ingested `TaiwanStockPrice` partition (never FinMind's own `market_value` field on the
    holding row — see `_holdings_market_value`'s docstring). Closes here are deliberately
    different from `_active_etf_holding_result`'s own (stale/broken) `market_value` figures, so
    an assertion against `shares x close` cannot pass by coincidentally matching the old
    field — it only passes if the fix genuinely reads price data, not the holding row's own
    `market_value`."""
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        date(2025, 5, 6),
        [
            {"symbol": "2330", "trade_date": "2025-05-06", "close": 100.0},
            {"symbol": "2454", "trade_date": "2025-05-06", "close": 50.0},
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        date(2025, 5, 7),
        [
            {"symbol": "2330", "trade_date": "2025-05-07", "close": 110.0},
            {"symbol": "2317", "trade_date": "2025-05-07", "close": 20.0},
        ],
    )


async def test_get_active_etf_report_classifies_status_from_prior_day_weight(
    tmp_path: Path,
) -> None:
    """新增 (2317, no prior weight), 刪除 (2454, no current weight), 加碼 (2330, buy>0 with prior
    weight present) — all classified from `(buy, sell, prior_weight, current_weight)`."""
    symbol = Symbol(id="00980A", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.ACTIVE_ETF_HOLDING: _active_etf_holding_result(),
            Capability.ACTIVE_ETF_HOLDING_CHANGE: _active_etf_holding_change_result(),
        },
    )
    store = ParquetStore(root=tmp_path)
    _write_price_partitions(store)
    tool = GetActiveEtfReportTool(router, store)
    assert tool.data_class is DataClass.SPONSOR

    output = await tool.run(
        GetActiveEtfReportInput(id="00980A", market=Market.TW, trade_date=date(2025, 5, 7)),
        ToolContext(),
    )

    by_stock = {row.component_stock_id: row for row in output.rows}
    assert by_stock["2317"].status is HoldingStatus.NEW
    assert by_stock["2454"].status is HoldingStatus.REMOVED
    assert by_stock["2330"].status is HoldingStatus.INCREASED
    assert output.report_date == date(2025, 5, 7)
    assert output.prior_date == date(2025, 5, 6)
    # report_date (2025-05-07) holdings: 2330 (120000 shares @ 110.0) + 2317 (40000 shares @ 20.0)
    assert output.fund_size == 120000 * 110.0 + 40000 * 20.0
    # prior_date (2025-05-06) holdings: 2330 (100000 shares @ 100.0) + 2454 (50000 shares @ 50.0)
    assert output.fund_size_prior == 100000 * 100.0 + 50000 * 50.0
    # Never the holding row's own (broken/stale) `market_value` field — see
    # `_write_price_partitions`'s docstring for why these two are deliberately far apart.
    assert output.fund_size != 71160000.0 + 5000000.0
    assert output.fund_size_prior != 59300000.0 + 30000000.0
    assert output.premium_discount_pct is None
    assert output.premium_discount_unavailable_reason is not None


async def test_get_active_etf_report_exposes_full_current_holdings_with_industry(
    tmp_path: Path,
) -> None:
    """`current_holdings` (成分股/產業分佈 tabs' real backing) must carry EVERY constituent on
    `report_date` — not just the ones `rows` diffed as changed — joined against the whole-market
    roster for `industry_category`."""
    symbol = Symbol(id="00980A", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.ACTIVE_ETF_HOLDING: _active_etf_holding_result(),
            Capability.ACTIVE_ETF_HOLDING_CHANGE: _active_etf_holding_change_result(),
        },
    )
    store = ParquetStore(root=tmp_path)
    _write_price_partitions(store)
    store.write_partition(
        "TaiwanStockInfo",
        "TW",
        date(2025, 5, 7),
        [
            {
                "symbol": "2330",
                "market": "TW",
                "name": "台積電",
                "industry_category": "半導體業",
                "security_type": "twse",
                "as_of": "2025-05-07",
            },
            {
                "symbol": "2317",
                "market": "TW",
                "name": "鴻海",
                "industry_category": "電子零組件業",
                "security_type": "twse",
                "as_of": "2025-05-07",
            },
        ],
    )
    tool = GetActiveEtfReportTool(router, store)

    output = await tool.run(
        GetActiveEtfReportInput(id="00980A", market=Market.TW, trade_date=date(2025, 5, 7)),
        ToolContext(),
    )

    by_stock = {row.component_stock_id: row for row in output.current_holdings}
    # report_date (2025-05-07) holdings are 2330 + 2317 — 2454 was sold and no longer holds.
    assert set(by_stock) == {"2330", "2317"}
    assert by_stock["2330"].weight == 9.2
    assert by_stock["2330"].industry_category == "半導體業"
    assert by_stock["2317"].industry_category == "電子零組件業"
    # Sorted descending by weight — the caller never needs to re-sort for its own donut/table.
    assert [row.component_stock_id for row in output.current_holdings] == ["2330", "2317"]


async def test_get_active_etf_report_current_holdings_industry_none_when_roster_missing(
    tmp_path: Path,
) -> None:
    """No `stock_info` roster partition at all — `industry_category` is honestly `None` for
    every holding, never fabricated, and the weights are still exposed."""
    symbol = Symbol(id="00980A", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.ACTIVE_ETF_HOLDING: _active_etf_holding_result(),
            Capability.ACTIVE_ETF_HOLDING_CHANGE: _active_etf_holding_change_result(),
        },
    )
    store = ParquetStore(root=tmp_path)
    _write_price_partitions(store)
    tool = GetActiveEtfReportTool(router, store)

    output = await tool.run(
        GetActiveEtfReportInput(id="00980A", market=Market.TW, trade_date=date(2025, 5, 7)),
        ToolContext(),
    )

    assert len(output.current_holdings) == 2
    assert all(row.industry_category is None for row in output.current_holdings)


async def test_get_active_etf_report_falls_back_to_etf_aum_when_prices_not_ingested_yet(
    tmp_path: Path,
) -> None:
    """The genuine gap this fallback closes: `report_date`'s constituent closes haven't landed
    in `TaiwanStockPrice` yet (a same-day ingest-timing lag), so the shares-x-close computation
    returns `None` — `fund_size` must fall back to the already-ingested `etf_aum` figure rather
    than staying empty, but `fund_size_prior`/`fund_size_change_pct` must NOT be fabricated from
    it (see `_fund_size_with_aum_fallback`'s own docstring for why)."""
    symbol = Symbol(id="00980A", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.ACTIVE_ETF_HOLDING: _active_etf_holding_result(),
            Capability.ACTIVE_ETF_HOLDING_CHANGE: _active_etf_holding_change_result(),
        },
    )
    store = ParquetStore(root=tmp_path)
    # Deliberately no `_write_price_partitions(store)` — report_date's closes are absent, so
    # `fund_size` from the shares-x-close path is `None` before the fallback.
    store.write_partition(
        "etf_aum",
        "TW",
        date(2025, 5, 7),
        [{"symbol": "00980A", "market": "TW", "as_of": "2025-05-07", "aum": 123_456_789.0}],
    )
    tool = GetActiveEtfReportTool(router, store)

    output = await tool.run(
        GetActiveEtfReportInput(id="00980A", market=Market.TW, trade_date=date(2025, 5, 7)),
        ToolContext(),
    )

    assert output.fund_size == 123_456_789.0
    assert output.fund_size_prior is None
    assert output.fund_size_change_pct is None
    assert output.fund_size_caveat is not None


async def test_get_active_etf_report_no_prior_day_is_not_guessed(tmp_path: Path) -> None:
    """A change row on the earliest date the window returns has no prior partition to diff
    against — `status` must say so explicitly, never guess 新增/加碼."""
    symbol = Symbol(id="00980A", market=Market.TW)
    single_day_holding = SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={
            "holdings": [
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-05",
                    "component_stock_id": "2330",
                    "component_stock_name": "台積電",
                    "asset_type": "stock",
                    "shares": 100000,
                    "weight": 8.5,
                    "market_value": 59300000.0,
                    "currency": "TWD",
                },
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    single_day_change = SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={
            "changes": [
                {
                    "symbol": "00980A",
                    "market": "TW",
                    "trade_date": "2025-05-05",
                    "component_stock_id": "2330",
                    "component_stock_name": "台積電",
                    "buy": 100000,
                    "sell": 0,
                },
            ]
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _ = make_router(
        symbol,
        {
            Capability.ACTIVE_ETF_HOLDING: single_day_holding,
            Capability.ACTIVE_ETF_HOLDING_CHANGE: single_day_change,
        },
    )
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "TaiwanStockPrice", "TW", date(2025, 5, 5), [{"symbol": "2330", "close": 593.0}]
    )
    tool = GetActiveEtfReportTool(router, store)

    output = await tool.run(
        GetActiveEtfReportInput(id="00980A", market=Market.TW, trade_date=date(2025, 5, 5)),
        ToolContext(),
    )

    assert output.prior_date is None
    assert output.rows[0].status is HoldingStatus.NO_PRIOR_DATA
    assert output.fund_size_prior is None


async def test_get_active_etf_report_degrades_gracefully_when_holding_data_missing(
    tmp_path: Path,
) -> None:
    """No adapter configured for `ACTIVE_ETF_HOLDING` at all — the router surfaces its own
    `unavailable` result rather than the tool crashing or fabricating rows."""
    symbol = Symbol(id="00980A", market=Market.TW)
    router, _ = make_router(symbol, {})
    store = ParquetStore(root=tmp_path)
    tool = GetActiveEtfReportTool(router, store)

    output = await tool.run(GetActiveEtfReportInput(id="00980A", market=Market.TW), ToolContext())

    assert output.rows == ()
    assert output.report_date is None
    assert output.health_status is not HealthStatus.OK


async def test_get_etf_money_flow_reports_unavailable_when_store_has_no_partition(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    tool = GetEtfMoneyFlowTool(store)
    assert tool.data_class is DataClass.SPONSOR

    output = await tool.run(
        GetEtfMoneyFlowInput(
            market=Market.TW, start_date=date(2025, 5, 5), end_date=date(2025, 5, 7)
        ),
        ToolContext(tier=Tier.PREMIUM),
    )

    assert output.data_available is False
    assert output.unavailable_reason is not None
    assert output.inflow_ranking == ()


async def test_get_etf_money_flow_aggregates_across_etfs_and_ranks_flows(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    trading_day = date(2025, 5, 7)
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        trading_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 20000,
                "sell": 0,
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 10000,
                "sell": 0,
            },
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2454",
                "component_stock_name": "聯發科",
                "buy": 0,
                "sell": 50000,
            },
        ],
    )
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        trading_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 120000,
                "weight": 9.2,
                # FinMind hardcodes this field to 0.0 for every row of this dataset — the tool
                # must never trust it; this value is deliberately wrong to prove that.
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    # `_closes_by_symbol`/`_shares_outstanding_by_symbol` read from these two ingested datasets —
    # 2330's real close (600) recomputes `held_market_value` (never FinMind's own broken field),
    # and 2330's market cap (600,000,000) ÷ that close recovers a 1,000,000-share outstanding
    # count, so `stock_shares_held_pct` = 120,000 ÷ 1,000,000 = 12%.
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        trading_day,
        [{"symbol": "2330", "trade_date": "2025-05-07", "close": 600.0}],
    )
    store.write_partition(
        "TaiwanStockMarketValue",
        "TW",
        trading_day,
        [
            {
                "symbol": "2330",
                "market": "TW",
                "trade_date": "2025-05-07",
                "market_value": 600_000_000,
            }
        ],
    )
    tool = GetEtfMoneyFlowTool(store)

    output = await tool.run(
        GetEtfMoneyFlowInput(market=Market.TW, start_date=trading_day, end_date=trading_day),
        ToolContext(tier=Tier.PREMIUM),
    )

    assert output.data_available is True
    assert output.total_stock_count == 2
    by_stock = {row.component_stock_id: row for row in output.inflow_ranking}
    assert by_stock["2330"].net_shares_flow == 30000
    assert by_stock["2330"].buying_etf_count == 2
    assert by_stock["2330"].selling_etf_count == 0
    assert by_stock["2330"].active_etf_holder_count == 1
    assert by_stock["2330"].held_shares == 120000
    assert by_stock["2330"].held_market_value == pytest.approx(120000 * 600.0)
    assert by_stock["2330"].stock_shares_held_pct == pytest.approx(12.0)
    assert by_stock["2454"].net_shares_flow == -50000
    # 2454 has no `TaiwanStockActiveETFHolding` row in this fixture — every holding-derived field
    # must stay `None`, never a fabricated `0`/estimate.
    assert by_stock["2454"].held_shares is None
    assert by_stock["2454"].held_market_value is None
    assert by_stock["2454"].stock_shares_held_pct is None
    outflow_ids = [row.component_stock_id for row in output.outflow_ranking]
    assert outflow_ids[0] == "2454"


async def test_get_etf_money_flow_falls_back_to_latest_ingested_holding_day(
    tmp_path: Path,
) -> None:
    """`ACTIVE_ETF_HOLDING_JOB` lagging one day behind the price/holding-change calendar (a
    normal, frequent ingest state) must not silently zero out every holding-derived field —
    the tool should fall back to the latest day that dataset actually has, not the calendar's
    own last business day."""
    store = ParquetStore(root=tmp_path)
    lagging_day = date(2025, 5, 7)  # holding-change + price ingested for this day too
    latest_holding_day = date(2025, 5, 6)  # ACTIVE_ETF_HOLDING only ingested through here
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        lagging_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 20000,
                "sell": 0,
            },
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        lagging_day,
        [{"symbol": "2330", "trade_date": "2025-05-07", "close": 610.0}],
    )
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        latest_holding_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 100000,
                "weight": 8.5,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        latest_holding_day,
        [{"symbol": "2330", "trade_date": "2025-05-06", "close": 600.0}],
    )
    tool = GetEtfMoneyFlowTool(store)

    output = await tool.run(
        GetEtfMoneyFlowInput(market=Market.TW, start_date=lagging_day, end_date=lagging_day),
        ToolContext(tier=Tier.PREMIUM),
    )

    assert output.data_available is True
    row = output.inflow_ranking[0]
    assert row.component_stock_id == "2330"
    # Falls back to `latest_holding_day`'s real 100,000 shares x 600.0 close — never `None`
    # just because `lagging_day` itself has no `ACTIVE_ETF_HOLDING_JOB` partition yet.
    assert row.held_shares == 100000
    assert row.held_market_value == pytest.approx(100000 * 600.0)
    assert row.active_etf_holder_count == 1


async def test_get_etf_money_flow_masks_rows_beyond_free_preview_for_free_tier(
    tmp_path: Path,
) -> None:
    """governance.md §4 top-N-preview exception: an unentitled (FREE) caller gets the top
    `_ETF_MONEY_FLOW_FREE_ROW_COUNT` rows of each ranking real, everything else with identity/
    value fields nulled and `locked=True`."""
    store = ParquetStore(root=tmp_path)
    trading_day = date(2025, 5, 7)
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        trading_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 30000,
                "sell": 0,
            },
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2454",
                "component_stock_name": "聯發科",
                "buy": 20000,
                "sell": 0,
            },
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2317",
                "component_stock_name": "鴻海",
                "buy": 10000,
                "sell": 0,
            },
        ],
    )
    tool = GetEtfMoneyFlowTool(store)
    assert tool.supports_row_preview is True

    output = await tool.run(
        GetEtfMoneyFlowInput(market=Market.TW, start_date=trading_day, end_date=trading_day),
        ToolContext(tier=Tier.FREE),
    )

    assert output.data_available is True
    assert output.free_row_count == 2
    assert len(output.inflow_ranking) == 3
    free_rows = [row for row in output.inflow_ranking if row.rank <= 2]
    locked_rows = [row for row in output.inflow_ranking if row.rank > 2]
    assert len(free_rows) == 2
    assert len(locked_rows) == 1
    for row in free_rows:
        assert row.locked is False
        assert row.component_stock_id is not None
    locked_row = locked_rows[0]
    assert locked_row.locked is True
    assert locked_row.component_stock_id is None
    assert locked_row.net_shares_flow is None


async def test_get_daily_signal_reports_unavailable_when_store_has_no_partition(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    tool = GetDailySignalTool(store)
    assert tool.data_class is DataClass.SPONSOR

    output = await tool.run(
        GetDailySignalInput(market=Market.TW, trade_date=date(2025, 5, 7)),
        ToolContext(tier=Tier.PREMIUM),
    )

    assert output.data_available is False
    assert output.unavailable_reason is not None
    assert output.rows == ()


async def test_get_daily_signal_classifies_status_across_all_etfs_combined(
    tmp_path: Path,
) -> None:
    """2330 is held by 00980A (prior day) and gets bought by BOTH 00980A and 00981A today — the
    aggregate status must be 加碼 (already held in aggregate, net buy), never 新增, even though
    it's a first-time holding for 00981A specifically. 2454 has no prior-day holding anywhere and
    gets bought today — aggregate 新增. 9999 was held prior but sold out of everywhere today —
    aggregate 刪除."""
    store = ParquetStore(root=tmp_path)
    prior_day = date(2025, 5, 6)
    report_day = date(2025, 5, 7)
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        prior_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 100000,
                "weight": 8.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "9999",
                "component_stock_name": "測試股",
                "asset_type": "stock",
                "shares": 5000,
                "weight": 1.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        report_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 110000,
                "weight": 8.5,
                "market_value": 0.0,
                "currency": "TWD",
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 20000,
                "weight": 3.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2454",
                "component_stock_name": "聯發科",
                "asset_type": "stock",
                "shares": 8000,
                "weight": 2.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        report_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 10000,
                "sell": 0,
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 20000,
                "sell": 0,
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2454",
                "component_stock_name": "聯發科",
                "buy": 8000,
                "sell": 0,
            },
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "9999",
                "component_stock_name": "測試股",
                "buy": 0,
                "sell": 5000,
            },
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        report_day,
        [
            {"symbol": "2330", "trade_date": "2025-05-07", "close": 600.0},
            {"symbol": "2454", "trade_date": "2025-05-07", "close": 900.0},
        ],
    )
    tool = GetDailySignalTool(store)

    output = await tool.run(
        GetDailySignalInput(market=Market.TW, trade_date=report_day),
        ToolContext(tier=Tier.PREMIUM),
    )

    assert output.data_available is True
    assert output.report_date == report_day
    assert output.prior_date == prior_day
    assert output.total_stock_count == 3
    by_stock = {row.component_stock_id: row for row in output.rows}
    assert by_stock["2330"].status == HoldingStatus.INCREASED
    assert by_stock["2330"].shares_delta == 30000
    assert by_stock["2330"].buying_etf_count == 2
    assert by_stock["2454"].status == HoldingStatus.NEW
    assert by_stock["9999"].status == HoldingStatus.REMOVED
    assert output.increased_count == 1
    assert output.new_count == 1
    assert output.removed_count == 1
    assert output.decreased_count == 0
    # 2330's aggregate net-buy (+30,000) is the largest inflow; its held-value delta uses the
    # real close (600.0), never FinMind's own broken market_value field.
    assert output.top_inflow is not None
    assert output.top_inflow.component_stock_id == "2330"
    assert output.top_inflow.held_market_value_delta == pytest.approx(30000 * 600.0)
    assert output.top_outflow is not None
    assert output.top_outflow.component_stock_id == "9999"


async def test_get_daily_signal_masks_rows_and_highlights_beyond_free_preview_for_free_tier(
    tmp_path: Path,
) -> None:
    """governance.md §4 top-N-preview exception: an unentitled (FREE) caller gets the top
    `_DAILY_SIGNAL_FREE_ROW_COUNT` rows real, everything else with identity/value fields nulled
    and `locked=True` — and both 焦點股 highlights locked entirely, never counted against the
    free-row budget."""
    store = ParquetStore(root=tmp_path)
    prior_day = date(2025, 6, 2)
    report_day = date(2025, 6, 3)
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        prior_day,
        [
            {
                "symbol": "00982A",
                "market": "TW",
                "trade_date": "2025-06-02",
                "component_stock_id": "2308",
                "component_stock_name": "台達電",
                "asset_type": "stock",
                "shares": 60000,
                "weight": 4.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        report_day,
        [
            {
                "symbol": "00982A",
                "market": "TW",
                "trade_date": "2025-06-03",
                "component_stock_id": "2308",
                "component_stock_name": "台達電",
                "asset_type": "stock",
                "shares": 70000,
                "weight": 4.5,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        report_day,
        [
            {
                "symbol": "00982A",
                "market": "TW",
                "trade_date": "2025-06-03",
                "component_stock_id": "2308",
                "component_stock_name": "台達電",
                "buy": 10000,
                "sell": 0,
            },
            {
                "symbol": "00982A",
                "market": "TW",
                "trade_date": "2025-06-03",
                "component_stock_id": "3034",
                "component_stock_name": "聯詠",
                "buy": 8000,
                "sell": 0,
            },
            {
                "symbol": "00982A",
                "market": "TW",
                "trade_date": "2025-06-03",
                "component_stock_id": "2379",
                "component_stock_name": "瑞昱",
                "buy": 5000,
                "sell": 0,
            },
        ],
    )
    tool = GetDailySignalTool(store)
    assert tool.supports_row_preview is True

    output = await tool.run(
        GetDailySignalInput(market=Market.TW, trade_date=report_day), ToolContext(tier=Tier.FREE)
    )

    assert output.data_available is True
    assert output.free_row_count == 2
    assert len(output.rows) == 3
    free_rows = [row for row in output.rows if row.rank <= 2]
    locked_rows = [row for row in output.rows if row.rank > 2]
    assert len(free_rows) == 2
    assert len(locked_rows) == 1
    for row in free_rows:
        assert row.locked is False
        assert row.component_stock_id is not None
    locked_row = locked_rows[0]
    assert locked_row.locked is True
    assert locked_row.component_stock_id is None
    assert locked_row.component_stock_name is None
    assert locked_row.shares_delta is None
    assert output.top_inflow is not None
    assert output.top_inflow.locked is True
    assert output.top_inflow.component_stock_id is None


def _profile_result() -> SourceResult:
    return SourceResult(
        source_id="MOPS",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="profile caveat",
        payload={
            "profile": {
                "symbol": "2330",
                "market": "TW",
                "company_name": "台灣積體電路製造股份有限公司",
                "industry_classification_code": "24",
                "founding_date": "1987-02-21",
                "listed_date": "1994-09-05",
                "chairman": "魏哲家",
                "general_manager": "魏哲家",
                "spokesperson": "黃仁昭",
                "spokesperson_title": "副總經理",
                "headquarters": "新竹科學園區力行六路8號",
                "phone": "(03)563-6688",
                "website": "https://www.tsmc.com",
                "capital": 259303805050,
                "auditor": "勤業眾信聯合會計師事務所",
            }
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_company_profile_enriches_all_fields_when_every_capability_is_healthy(
    tmp_path: Path,
) -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.SECTOR_CLASS: _sector_result(),
            Capability.DIVIDENDS: _dividend_result(),
            Capability.MARKET_VALUE: _market_value_result(),
            Capability.VALUATION_RATIOS: _valuation_ratios_result(),
            Capability.FINANCIALS: _income_statement_result(),
            Capability.PROFILE: _profile_result(),
        },
    )
    tool = GetCompanyProfileTool(router, ParquetStore(root=tmp_path))

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert output.sector == "Tech"
    assert output.latest_dividend is not None
    assert output.market_value == 15000000000
    assert output.per == 18.5
    assert output.dividend_yield == 2.1
    assert output.eps == 9.21

    # MOPS-sourced identity fields, real when the profile capability is healthy.
    assert output.chairman == "魏哲家"
    assert output.auditor == "勤業眾信聯合會計師事務所"
    assert output.headquarters == "新竹科學園區力行六路8號"
    assert output.website == "https://www.tsmc.com"
    assert output.capital == 259303805050
    assert output.spokesperson == "黃仁昭"
    assert output.founding_date == "1987-02-21"
    assert output.industry_classification_code == "24"
    assert "chairman" not in output.missing_fields
    assert "auditor" not in output.missing_fields

    # revenue_yoy/margins are read from the derived Parquet store, not the router — an empty
    # store (no ingested partitions in this test) correctly degrades both to missing, independent
    # of the router-fetched capabilities' health above.
    assert "revenue_yoy" in output.missing_fields
    assert "margins" in output.missing_fields

    # Successfully-enriched fields must NOT be listed as missing.
    assert "market_value" not in output.missing_fields
    assert "per" not in output.missing_fields
    assert "dividend_yield" not in output.missing_fields
    assert "eps" not in output.missing_fields


async def test_get_company_profile_lists_mops_fields_missing_when_no_profile_adapter(
    tmp_path: Path,
) -> None:
    """A market with no MOPS/`profile` adapter (e.g. the router's own `unavailable` degrade)
    must list every MOPS-sourced identity field in `missing_fields`, never fabricate one."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.SECTOR_CLASS: _sector_result(),
            Capability.DIVIDENDS: _dividend_result(),
            Capability.MARKET_VALUE: _market_value_result(),
            Capability.VALUATION_RATIOS: _valuation_ratios_result(),
            Capability.FINANCIALS: _income_statement_result(),
        },
    )
    tool = GetCompanyProfileTool(router, ParquetStore(root=tmp_path))

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert output.chairman is None
    assert "chairman" in output.missing_fields
    assert "auditor" in output.missing_fields
    assert "headquarters" in output.missing_fields
    assert "website" in output.missing_fields
    assert "capital" in output.missing_fields
    assert "spokesperson" in output.missing_fields
    assert "founding_date" in output.missing_fields
    assert "industry_classification_code" in output.missing_fields


async def test_get_company_profile_lists_field_missing_when_its_capability_fetch_is_unhealthy(
    tmp_path: Path,
) -> None:
    """`missing_fields` is computed per-call, not a static reduced list — a field whose
    capability exists in general still lands there for a symbol where this specific fetch came
    back empty/unhealthy."""
    symbol = Symbol(id="2330", market=Market.TW)
    unavailable_market_value = SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="market value unavailable",
        payload={},
        health=SourceHealth(status=HealthStatus.UNREACHABLE, detail="no data"),
    )
    router, _ = make_router(
        symbol,
        {
            Capability.SECTOR_CLASS: _sector_result(),
            Capability.DIVIDENDS: _dividend_result(),
            Capability.MARKET_VALUE: unavailable_market_value,
            Capability.VALUATION_RATIOS: _valuation_ratios_result(),
            Capability.FINANCIALS: _income_statement_result(),
        },
    )
    tool = GetCompanyProfileTool(router, ParquetStore(root=tmp_path))

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert output.market_value is None
    assert "market_value" in output.missing_fields
    # The other enriched fields still succeeded and must not be dragged into missing_fields too.
    assert "per" not in output.missing_fields
    assert "eps" not in output.missing_fields


def _yfinance_income_statement_result() -> SourceResult:
    """yfinance's own item naming (`Diluted EPS`/`Total Revenue`/`Gross Profit`) with
    `period_type="annual"` — the shape `_latest_eps`/`_yfinance_revenue_yoy_and_margins` read for
    every non-TW market, distinct from TW's FinMind-shaped `_income_statement_result` fixture
    above (plain `"EPS"`/`"Revenue"`, no `period_type` at all)."""
    lines = []
    for period_end, eps, revenue, gross_profit in (
        ("2025-08-31", 7.59, 37_378_000_000.0, 12_500_000_000.0),
        ("2024-08-31", 0.70, 25_111_000_000.0, 6_200_000_000.0),
    ):
        lines.extend(
            [
                {
                    "symbol": "MU",
                    "market": "US",
                    "period_end": period_end,
                    "statement_type": "income_statement",
                    "period_type": "annual",
                    "item": "Diluted EPS",
                    "origin_name": "Diluted EPS",
                    "value": eps,
                },
                {
                    "symbol": "MU",
                    "market": "US",
                    "period_end": period_end,
                    "statement_type": "income_statement",
                    "period_type": "annual",
                    "item": "Total Revenue",
                    "origin_name": "Total Revenue",
                    "value": revenue,
                },
                {
                    "symbol": "MU",
                    "market": "US",
                    "period_end": period_end,
                    "statement_type": "income_statement",
                    "period_type": "annual",
                    "item": "Gross Profit",
                    "origin_name": "Gross Profit",
                    "value": gross_profit,
                },
            ]
        )
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="yfinance income statement caveat",
        payload={"lines": lines},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_company_profile_reads_eps_and_revenue_yoy_live_for_us(
    tmp_path: Path,
) -> None:
    """US (and every other non-TW market) has no ingested `financials_lines`/
    `month_revenue_rows` partition — `eps`/`revenue_yoy`/`margins` must be computed live off the
    yfinance `financials` fetch instead of the permanently-empty derived-store read TW uses."""
    symbol = Symbol(id="MU", market=Market.US)
    router, _ = make_router(
        symbol,
        {
            Capability.SECTOR_CLASS: _sector_result(),
            Capability.DIVIDENDS: _dividend_result(),
            Capability.MARKET_VALUE: _market_value_result(),
            Capability.VALUATION_RATIOS: _valuation_ratios_result(),
            Capability.FINANCIALS: _yfinance_income_statement_result(),
        },
    )
    tool = GetCompanyProfileTool(router, ParquetStore(root=tmp_path))

    output = await tool.run(SymbolInput(id="MU", market=Market.US), ToolContext())

    assert output.eps == pytest.approx(7.59)  # the LATEST period's Diluted EPS, not Basic
    assert output.revenue_yoy == pytest.approx(37_378_000_000.0 / 25_111_000_000.0 - 1)
    assert output.margins == pytest.approx(12_500_000_000.0 / 37_378_000_000.0)
    assert "eps" not in output.missing_fields
    assert "revenue_yoy" not in output.missing_fields
    assert "margins" not in output.missing_fields


async def test_get_etfs_holding_stock_reports_unavailable_when_store_has_no_partition(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    tool = GetEtfsHoldingStockTool(store)
    assert tool.data_class is DataClass.SPONSOR

    output = await tool.run(
        GetEtfsHoldingStockInput(id="2330", market=Market.TW, as_of=date(2025, 5, 7)),
        ToolContext(),
    )

    assert output.data_available is False
    assert output.unavailable_reason is not None
    assert output.holding_rank == ()


async def test_get_etfs_holding_stock_reports_unavailable_when_no_etf_holds_it(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    trading_day = date(2025, 5, 7)
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        trading_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2454",
                "component_stock_name": "聯發科",
                "asset_type": "stock",
                "shares": 1000,
                "weight": 5.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    tool = GetEtfsHoldingStockTool(store)

    output = await tool.run(
        GetEtfsHoldingStockInput(id="2330", market=Market.TW, as_of=trading_day),
        ToolContext(),
    )

    assert output.data_available is False
    assert output.unavailable_reason is not None
    assert "2330" in (output.unavailable_reason or "")


async def test_get_etfs_holding_stock_ranks_by_shares_and_weight_with_deltas(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    prior_day = date(2025, 5, 6)
    report_day = date(2025, 5, 7)
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        prior_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 100000,
                "weight": 8.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        report_day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 120000,
                "weight": 9.2,
                "market_value": 0.0,
                "currency": "TWD",
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 50000,
                "weight": 3.5,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        report_day,
        [{"symbol": "2330", "trade_date": "2025-05-07", "close": 600.0}],
    )
    store.write_partition(
        "TaiwanStockMarketValue",
        "TW",
        report_day,
        [
            {
                "symbol": "2330",
                "market": "TW",
                "trade_date": "2025-05-07",
                "market_value": 600_000_000,
            }
        ],
    )
    tool = GetEtfsHoldingStockTool(store)

    output = await tool.run(
        GetEtfsHoldingStockInput(id="2330", market=Market.TW, as_of=report_day),
        ToolContext(),
    )

    assert output.data_available is True
    assert output.as_of == report_day
    assert output.prior_date == prior_day
    # Holding rank sorted descending by shares — 00980A (120,000) before 00981A (50,000).
    assert [row.etf_symbol for row in output.holding_rank] == ["00980A", "00981A"]
    top = output.holding_rank[0]
    assert top.shares == 120000
    assert top.shares_delta == 20000  # 120,000 - 100,000 vs. the prior snapshot
    # 170,000 combined shares / 1,000,000 shares-outstanding (600,000,000 market_value / 600 close)
    assert top.stock_capital_pct == pytest.approx(17.0)
    # Weight rank sorted descending by weight_pct — same two ETFs, weight order matches shares here.
    assert [row.etf_symbol for row in output.weight_rank] == ["00980A", "00981A"]
    assert output.weight_rank[0].weight_pct == pytest.approx(9.2)
    assert output.weight_rank[0].weight_pct_delta == pytest.approx(1.2)  # 9.2 - 8.0
    # 00981A has no prior-day row for 2330 — its delta must be None, never a fabricated 0.
    assert output.weight_rank[1].weight_pct_delta is None


def _write_2330_price_and_market_value(store: ParquetStore, day: date) -> None:
    """The `TaiwanStockPrice`/`TaiwanStockMarketValue` partitions `_shares_outstanding_by_symbol`
    reads to compute 2330's `stock_capital_pct` — shared by every `get_etfs_holding_stock` event
    test below, none of which asserts on this figure directly."""
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        day,
        [{"symbol": "2330", "trade_date": day.isoformat(), "close": 600.0}],
    )
    store.write_partition(
        "TaiwanStockMarketValue",
        "TW",
        day,
        [
            {
                "symbol": "2330",
                "market": "TW",
                "trade_date": day.isoformat(),
                "market_value": 600_000_000,
            }
        ],
    )


async def test_get_etfs_holding_stock_reports_position_open_and_close_events(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    day1 = date(2025, 5, 5)
    day2 = date(2025, 5, 6)
    # Day 1: 00980A already holds 2330; 00981A does not exist for 2330 yet.
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        day1,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-05",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 100000,
                "weight": 8.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    # Day 2: 00980A fully exits (清倉); 00981A newly opens a position (建倉).
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        day2,
        [
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 30000,
                "weight": 2.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        day2,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 0,
                "sell": 100000,
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 30000,
                "sell": 0,
            },
        ],
    )
    _write_2330_price_and_market_value(store, day2)
    tool = GetEtfsHoldingStockTool(store)

    output = await tool.run(
        GetEtfsHoldingStockInput(id="2330", market=Market.TW, as_of=day2),
        ToolContext(),
    )

    assert output.data_available is True
    events_by_etf = {e.etf_symbol: e for e in output.position_events}
    assert events_by_etf["00980A"].event is HoldingStatus.REMOVED
    assert events_by_etf["00980A"].shares_delta == -100000
    assert events_by_etf["00981A"].event is HoldingStatus.NEW
    assert events_by_etf["00981A"].shares_delta == 30000
    assert output.position_event_counts.removed_count == 1
    assert output.position_event_counts.new_count == 1
    assert output.position_event_counts.increased_count == 0
    assert output.position_event_counts.decreased_count == 0


async def test_get_etfs_holding_stock_reports_increase_and_decrease_events(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    day1 = date(2025, 5, 5)
    day2 = date(2025, 5, 6)
    # Day 1: both ETFs already hold 2330.
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        day1,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-05",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 100000,
                "weight": 8.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-05",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 50000,
                "weight": 5.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    # Day 2: 00980A adds (加碼); 00981A trims (減碼) — both still held, no zero-crossing.
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        day2,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 120000,
                "weight": 9.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 40000,
                "weight": 4.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        day2,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 20000,
                "sell": 0,
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-06",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "buy": 0,
                "sell": 10000,
            },
        ],
    )
    _write_2330_price_and_market_value(store, day2)
    tool = GetEtfsHoldingStockTool(store)

    output = await tool.run(
        GetEtfsHoldingStockInput(id="2330", market=Market.TW, as_of=day2),
        ToolContext(),
    )

    assert output.data_available is True
    assert output.etf_count == 2
    events_by_etf = {e.etf_symbol: e for e in output.position_events}
    assert events_by_etf["00980A"].event is HoldingStatus.INCREASED
    assert events_by_etf["00980A"].shares_delta == 20000
    assert events_by_etf["00981A"].event is HoldingStatus.DECREASED
    assert events_by_etf["00981A"].shares_delta == -10000
    assert output.position_event_counts.increased_count == 1
    assert output.position_event_counts.decreased_count == 1
    assert output.position_event_counts.new_count == 0
    assert output.position_event_counts.removed_count == 0


async def test_get_etfs_holding_stock_masks_rows_beyond_free_preview_for_unentitled_caller(
    tmp_path: Path,
) -> None:
    """governance.md §4 top-N-preview exception: an unentitled (`Tier.FREE`) caller still gets
    every row's real `etf_symbol`/`etf_name`/rank, but every row past the fixed free-preview
    count has its value fields nulled server-side — the real number must never reach this
    caller, not even inside the JSON body."""
    store = ParquetStore(root=tmp_path)
    day = date(2025, 5, 7)
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        day,
        [
            {
                "symbol": "00980A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 300000,
                "weight": 9.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
            {
                "symbol": "00981A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 200000,
                "weight": 7.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
            {
                "symbol": "00982A",
                "market": "TW",
                "trade_date": "2025-05-07",
                "component_stock_id": "2330",
                "component_stock_name": "台積電",
                "asset_type": "stock",
                "shares": 100000,
                "weight": 5.0,
                "market_value": 0.0,
                "currency": "TWD",
            },
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        day,
        [{"symbol": "2330", "trade_date": "2025-05-07", "close": 600.0}],
    )
    store.write_partition(
        "TaiwanStockMarketValue",
        "TW",
        day,
        [
            {
                "symbol": "2330",
                "market": "TW",
                "trade_date": "2025-05-07",
                "market_value": 600_000_000,
            }
        ],
    )
    tool = GetEtfsHoldingStockTool(store)
    input_args = GetEtfsHoldingStockInput(id="2330", market=Market.TW, as_of=day)

    free_output = await tool.run(input_args, ToolContext(tier=Tier.FREE))
    assert free_output.free_row_count == 2
    assert free_output.etf_count == 3
    # Top 2 rows (by shares) stay real, including identity; the 3rd is fully masked.
    assert free_output.holding_rank[0].locked is False
    assert free_output.holding_rank[0].etf_symbol == "00980A"
    assert free_output.holding_rank[0].shares == 300000
    assert free_output.holding_rank[1].locked is False
    assert free_output.holding_rank[1].etf_symbol == "00981A"
    assert free_output.holding_rank[1].shares == 200000
    locked_row = free_output.holding_rank[2]
    assert locked_row.locked is True
    assert locked_row.rank == 3  # rank survives masking — the only thing that does
    assert locked_row.etf_symbol is None  # identity is masked too, not just the value
    assert locked_row.etf_name is None
    assert locked_row.shares is None
    assert locked_row.stock_capital_pct is None
    assert free_output.weight_rank[2].locked is True
    assert free_output.weight_rank[2].etf_symbol is None
    assert free_output.weight_rank[2].weight_pct is None

    premium_output = await tool.run(input_args, ToolContext(tier=Tier.PREMIUM))
    assert premium_output.free_row_count == 3
    assert all(not row.locked for row in premium_output.holding_rank)
    assert premium_output.holding_rank[2].etf_symbol == "00982A"
    assert premium_output.holding_rank[2].shares == 100000

    # Through the real `dispatch_tool` gate: `supports_row_preview=True` means an unentitled
    # caller gets the row-masked output above, never the whole-tool `MaskedTeaser` envelope.
    dispatched = await dispatch_tool(tool, input_args, ToolContext(tier=Tier.FREE))
    assert not isinstance(dispatched, MaskedTeaser)
    assert dispatched.free_row_count == 2


async def test_get_holder_accumulation_masks_rows_beyond_free_preview_for_free_tier(
    tmp_path: Path,
) -> None:
    """governance.md §4 top-N-preview exception: an unentitled (FREE) caller gets the top
    `_HOLDER_ACCUMULATION_FREE_ROW_COUNT` rows real, everything else with identity/value fields
    nulled and `locked=True`."""
    store = ParquetStore(root=tmp_path)
    week_ago = date(2026, 8, 14)
    this_week = date(2026, 8, 21)
    store.write_partition(
        "TaiwanStockHoldingSharesPer",
        "TW",
        week_ago,
        [
            {
                "symbol": symbol,
                "market": "TW",
                "holding_shares_level": "400,001-600,000",
                "people": 1,
                "percent": 10.0,
                "unit": 500_000,
            }
            for symbol in ("2330", "2454", "2317")
        ],
    )
    store.write_partition(
        "TaiwanStockHoldingSharesPer",
        "TW",
        this_week,
        [
            {
                "symbol": "2330",
                "market": "TW",
                "holding_shares_level": "400,001-600,000",
                "people": 1,
                "percent": 30.0,
                "unit": 700_000,
            },
            {
                "symbol": "2454",
                "market": "TW",
                "holding_shares_level": "400,001-600,000",
                "people": 1,
                "percent": 20.0,
                "unit": 650_000,
            },
            {
                "symbol": "2317",
                "market": "TW",
                "holding_shares_level": "400,001-600,000",
                "people": 1,
                "percent": 15.0,
                "unit": 600_000,
            },
        ],
    )
    store.write_partition(
        "TaiwanStockInfo",
        "TW",
        this_week,
        [
            {
                "symbol": symbol,
                "market": "TW",
                "name": name,
                "industry_category": "半導體業",
                "security_type": "twse",
                "as_of": "2026-08-21",
            }
            for symbol, name in [("2330", "台積電"), ("2454", "聯發科"), ("2317", "鴻海")]
        ],
    )
    tool = GetHolderAccumulationTool(store)
    assert tool.supports_row_preview is True

    output = await tool.run(
        GetHolderAccumulationInput(market=Market.TW, threshold=400, window=1, as_of=this_week),
        ToolContext(tier=Tier.FREE),
    )

    assert output.data_available is True
    assert output.free_row_count == 2
    assert len(output.rows) == 3
    free_rows = [row for row in output.rows if row.rank <= 2]
    locked_rows = [row for row in output.rows if row.rank > 2]
    assert len(free_rows) == 2
    assert len(locked_rows) == 1
    for row in free_rows:
        assert row.locked is False
        assert row.symbol is not None
    locked_row = locked_rows[0]
    assert locked_row.locked is True
    assert locked_row.symbol is None
    assert locked_row.percent_of_custody_change is None


async def test_get_holder_accumulation_counts_finmind_top_tier_label(tmp_path: Path) -> None:
    """The 1,000張 threshold is the top tier only, which FinMind labels "more than 1,000,001". A
    table that knew only the TDCC spelling read every 千張大戶 change as zero."""
    store = ParquetStore(root=tmp_path)
    week_ago, this_week = date(2026, 9, 17), date(2026, 9, 24)
    for day, percent in ((week_ago, 45.7), (this_week, 51.7)):
        store.write_partition(
            "TaiwanStockHoldingSharesPer",
            "TW",
            day,
            [
                {
                    "symbol": "2409",
                    "market": "TW",
                    "holding_shares_level": "more than 1,000,001",
                    "people": 367,
                    "percent": percent,
                    "unit": 0,
                }
            ],
        )
    store.write_partition(
        "TaiwanStockInfo",
        "TW",
        this_week,
        [
            {
                "symbol": "2409",
                "market": "TW",
                "name": "友達",
                "industry_category": "光電業",
                "security_type": "twse",
                "as_of": "2026-09-24",
            }
        ],
    )
    output = await GetHolderAccumulationTool(store).run(
        GetHolderAccumulationInput(market=Market.TW, threshold=1000, window=1, as_of=this_week),
        ToolContext(tier=Tier.PRO),
    )
    (row,) = output.rows
    assert row.symbol == "2409"
    assert row.percent_of_custody_change == pytest.approx(6.0)


async def test_get_index_contribution_masks_rows_beyond_free_preview_for_free_tier(
    tmp_path: Path,
) -> None:
    """governance.md §4 top-N-preview exception: an unentitled (FREE) caller gets the top
    `_INDEX_CONTRIBUTION_FREE_ROW_COUNT` rows of each side (positive/negative) real, everything
    else with identity/value fields nulled and `locked=True` — the budget is per side."""
    store = ParquetStore(root=tmp_path)
    trade_day = date(2025, 5, 7)
    prior_day = date(2025, 5, 6)
    positives = [
        ("2330", "台積電", 600.0, 500.0),
        ("2454", "聯發科", 900.0, 800.0),
        ("2303", "聯電", 160.0, 150.0),
    ]
    negatives = [
        ("2317", "鴻海", 100.0, 120.0),
        ("2308", "台達電", 200.0, 250.0),
        ("2891", "中信金", 30.0, 40.0),
    ]
    all_symbols = positives + negatives
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        trade_day,
        [
            {"symbol": sym, "trade_date": trade_day.isoformat(), "close": close}
            for sym, _, close, _ in all_symbols
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        prior_day,
        [
            {"symbol": sym, "trade_date": prior_day.isoformat(), "close": prior_close}
            for sym, _, _, prior_close in all_symbols
        ],
    )
    store.write_partition(
        "TaiwanStockMarketValueWeight",
        "TW",
        trade_day,
        [
            {
                "symbol": sym,
                "market": "TW",
                "trade_date": trade_day.isoformat(),
                "rank": i,
                "stock_name": name,
                "weight_per": 5.0,
                "listing_type": "twse",
            }
            for i, (sym, name, _, _) in enumerate(all_symbols, start=1)
        ],
    )
    tool = GetIndexContributionTool(store)
    assert tool.supports_row_preview is True

    output = await tool.run(
        GetIndexContributionInput(market=Market.TW, trade_date=trade_day),
        ToolContext(tier=Tier.FREE),
    )

    assert output.data_available is True
    assert output.free_row_count == 2
    assert len(output.positive_contributors) == 3
    assert len(output.negative_contributors) == 3
    for contributors in (output.positive_contributors, output.negative_contributors):
        free_rows = [row for row in contributors if row.rank <= 2]
        locked_rows = [row for row in contributors if row.rank > 2]
        assert len(free_rows) == 2
        assert len(locked_rows) == 1
        for row in free_rows:
            assert row.locked is False
            assert row.symbol is not None
        locked_row = locked_rows[0]
        assert locked_row.locked is True
        assert locked_row.symbol is None
        assert locked_row.contribution_estimate is None
