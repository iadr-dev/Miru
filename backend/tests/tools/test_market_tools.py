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


async def test_get_chip_flows_run_returns_real_sponsor_data_when_called_directly() -> None:
    """`run()` itself never gates — proves entitlement is the dispatcher's job, not the tool's."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.CHIP_BROKER: _chip_broker_result()})
    tool = GetChipFlowsTool(router)
    assert tool.data_class is DataClass.SPONSOR

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.CHIP_BROKER
    assert output.rows[0].broker_name == "Fubon"


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


async def test_get_active_etf_report_classifies_status_from_prior_day_weight() -> None:
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
    tool = GetActiveEtfReportTool(router)
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
    assert output.fund_size == 71160000.0 + 5000000.0
    assert output.fund_size_prior == 59300000.0 + 30000000.0
    assert output.premium_discount_pct is None
    assert output.premium_discount_unavailable_reason is not None


async def test_get_active_etf_report_no_prior_day_is_not_guessed() -> None:
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
    tool = GetActiveEtfReportTool(router)

    output = await tool.run(
        GetActiveEtfReportInput(id="00980A", market=Market.TW, trade_date=date(2025, 5, 5)),
        ToolContext(),
    )

    assert output.prior_date is None
    assert output.rows[0].status is HoldingStatus.NO_PRIOR_DATA
    assert output.fund_size_prior is None


async def test_get_active_etf_report_degrades_gracefully_when_holding_data_missing() -> None:
    """No adapter configured for `ACTIVE_ETF_HOLDING` at all — the router surfaces its own
    `unavailable` result rather than the tool crashing or fabricating rows."""
    symbol = Symbol(id="00980A", market=Market.TW)
    router, _ = make_router(symbol, {})
    tool = GetActiveEtfReportTool(router)

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


async def test_get_company_profile_composes_sector_and_dividend_and_lists_missing_fields() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.SECTOR_CLASS: _sector_result(),
            Capability.DIVIDENDS: _dividend_result(),
        },
    )
    tool = GetCompanyProfileTool(router)

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert output.sector == "Tech"
    assert output.latest_dividend is not None
    assert "chairman" in output.missing_fields
    assert "auditor" in output.missing_fields
