from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import DateRangeInput, SymbolInput
from tools.market_tools import (
    GetActiveEtfReportInput,
    GetActiveEtfReportTool,
    GetChipFlowsTool,
    GetCompanyProfileTool,
    GetDividendScheduleTool,
    GetEtfMoneyFlowInput,
    GetEtfMoneyFlowTool,
    GetInstitutionalFlowTool,
    GetPriceQuoteTool,
    HoldingStatus,
    InstitutionalFlowRollingNet,
)


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


async def test_get_dividend_schedule_delegates_and_parses_events() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.DIVIDENDS: _dividend_result()})
    tool = GetDividendScheduleTool(router)

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.DIVIDENDS
    assert output.events[0].cash_dividend == 10.0


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


async def test_get_chip_flows_run_returns_real_sponsor_data_when_called_directly() -> None:
    """`run()` itself never gates — proves entitlement is the dispatcher's job, not the tool's."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.CHIP_BROKER: _chip_broker_result()})
    tool = GetChipFlowsTool(router)
    assert tool.data_class is DataClass.SPONSOR

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.CHIP_BROKER
    assert output.rows[0].broker_name == "Fubon"
    assert output.top_brokers[0].broker_name == "Fubon"
    assert output.top_brokers[0].net == 500 - 100


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

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    by_broker = {b.broker_id: b for b in output.top_brokers}
    assert by_broker["1001"].net == (10_000 - 1_000) + (8_000 - 1_000)  # 16,000
    assert by_broker["1003"].net == 500 - 9_000  # -8,500
    assert by_broker["1002"].net == 100 - 50  # 50 — still present, just ranked last
    # Ranked by |net| descending: Big Buyer (16,000) > Big Seller (-8,500) > Small Fry (50).
    assert [b.broker_id for b in output.top_brokers] == ["1001", "1003", "1002"]


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
        ToolContext(),
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
                "market_value": 71160000.0,
                "currency": "TWD",
            },
        ],
    )
    tool = GetEtfMoneyFlowTool(store)

    output = await tool.run(
        GetEtfMoneyFlowInput(market=Market.TW, start_date=trading_day, end_date=trading_day),
        ToolContext(),
    )

    assert output.data_available is True
    by_stock = {row.component_stock_id: row for row in output.inflow_ranking}
    assert by_stock["2330"].net_shares_flow == 30000
    assert by_stock["2330"].buying_etf_count == 2
    assert by_stock["2330"].selling_etf_count == 0
    assert by_stock["2330"].active_etf_holder_count == 1
    assert by_stock["2454"].net_shares_flow == -50000
    outflow_ids = [row.component_stock_id for row in output.outflow_ranking]
    assert outflow_ids[0] == "2454"


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
