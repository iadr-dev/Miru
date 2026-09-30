from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.chip_tools import (
    GetGovernmentBankFlowsTool,
    GetIndustryMoneyFlowTool,
    GetInstitutionalFlowTotalMarketTool,
    GetMarginMaintenanceTool,
    GetMarginShortTool,
    GetSecuritiesTraderInfoTool,
)
from tools.common import DateRangeInput, MarketOnlyInput


def _result(
    payload: dict[str, object], *, data_class: DataClass = DataClass.PUBLIC
) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=data_class,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_margin_maintenance_attaches_an_area_chart_hint_when_rows_exist() -> None:
    """A bounded warning-threshold ratio hovering near a danger line reads better filled
    (`area`) than as a bare `line` — the first real caller of that mark."""
    symbol = Symbol(id="", market=Market.TW)
    row = {
        "market": "TW",
        "trade_date": "2026-08-01",
        "total_exchange_margin_maintenance": 165.2,
    }
    router, _adapter = make_router(
        symbol, {Capability.MARGIN_MAINTENANCE_RATIO: _result({"rows": [row]})}
    )
    tool = GetMarginMaintenanceTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert output.chart_hint is not None
    assert output.chart_hint.mark.value == "area"
    assert output.chart_hint.x is not None
    assert output.chart_hint.x.field == "trade_date"
    assert output.chart_hint.y is not None
    assert output.chart_hint.y.field == "total_exchange_margin_maintenance"


async def test_get_margin_maintenance_omits_chart_hint_when_no_rows() -> None:
    symbol = Symbol(id="", market=Market.TW)
    router, _adapter = make_router(
        symbol, {Capability.MARGIN_MAINTENANCE_RATIO: _result({"rows": []})}
    )
    tool = GetMarginMaintenanceTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert output.chart_hint is None


async def test_get_margin_short_delegates_and_parses_rows() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2026-08-01",
        "margin_purchase_buy": 1,
        "margin_purchase_cash_repayment": 0,
        "margin_purchase_limit": 0,
        "margin_purchase_sell": 0,
        "margin_purchase_today_balance": 0,
        "margin_purchase_yesterday_balance": 0,
        "note": None,
        "offset_loan_and_short": 0,
        "short_sale_buy": 0,
        "short_sale_cash_repayment": 0,
        "short_sale_limit": 0,
        "short_sale_sell": 0,
        "short_sale_today_balance": 0,
        "short_sale_yesterday_balance": 0,
    }
    router, adapter = make_router(symbol, {Capability.MARGIN_SHORT: _result({"rows": [row]})})
    tool = GetMarginShortTool(router)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(DateRangeInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.MARGIN_SHORT
    assert output.rows[0].margin_purchase_buy == 1


async def test_get_institutional_flow_total_market_is_market_only_input() -> None:
    symbol = Symbol(id="", market=Market.TW)
    row = {
        "market": "TW",
        "trade_date": "2026-08-01",
        "name": "Foreign_Investor",
        "buy": 1,
        "sell": 2,
    }
    router, adapter = make_router(
        symbol, {Capability.INSTITUTIONAL_FLOW_TOTAL_MARKET: _result({"rows": [row]})}
    )
    tool = GetInstitutionalFlowTotalMarketTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.INSTITUTIONAL_FLOW_TOTAL_MARKET
    assert output.rows[0].name == "Foreign_Investor"


async def test_get_industry_money_flow_resolves_real_latest_trading_day_when_omitted(
    tmp_path: Path,
) -> None:
    """Regression guard for the FinMind adapter's 30-day-lookback default silently starving
    `TaiwanStockIndustryChainMoneyFlow` (single-day-only upstream semantics) — the tool must
    resolve an omitted `start_date` to `PRICE_EOD_JOB`'s own latest ingested partition date,
    never leave it for the router/adapter's own generic default to fill in."""
    store = ParquetStore(root=tmp_path)
    latest = date(2026, 9, 7)
    store.write_partition(
        "TaiwanStockPrice", "TW", latest, [{"symbol": "2330", "trade_date": "2026-09-07"}]
    )
    symbol = Symbol(id="", market=Market.TW)
    row = {
        "market": "TW",
        "trade_date": "2026-09-07",
        "industry": "半導體業",
        "sub_industry": "",
        "stock_count": 10,
        "trading_volume": 100,
        "trading_money": 1000.0,
        "trading_money_pct": 5.0,
    }
    router, adapter = make_router(
        symbol, {Capability.INDUSTRY_CHAIN_MONEY_FLOW: _result({"rows": [row]})}
    )
    tool = GetIndustryMoneyFlowTool(router, store)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert adapter.calls[0][2]["start_date"] == "2026-09-07"
    assert "end_date" not in adapter.calls[0][2]
    assert output.rows[0].industry == "半導體業"


async def test_get_industry_money_flow_masks_rows_beyond_free_preview_for_free_tier(
    tmp_path: Path,
) -> None:
    """governance.md §4 top-N-preview exception: an unentitled (FREE) caller gets the top
    `_INDUSTRY_MONEY_FLOW_FREE_ROW_COUNT` chain-TOTAL rows real, everything else with identity/
    value fields nulled and `locked=True` — chain totals and sub-industry breakdown rows rank
    (and mask) as two separate groups, distinguished by the masking-surviving `is_total` flag."""
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "TaiwanStockPrice", "TW", date(2026, 9, 7), [{"symbol": "2330", "trade_date": "2026-09-07"}]
    )
    symbol = Symbol(id="", market=Market.TW)
    rows = [
        {
            "market": "TW",
            "trade_date": "2026-09-07",
            "industry": industry,
            "sub_industry": "",
            "stock_count": 10,
            "trading_volume": 100,
            "trading_money": 1000.0,
            "trading_money_pct": pct,
        }
        for industry, pct in [("半導體業", 30.0), ("電腦及週邊設備業", 20.0), ("通信網路業", 10.0)]
    ]
    router, _ = make_router(symbol, {Capability.INDUSTRY_CHAIN_MONEY_FLOW: _result({"rows": rows})})
    tool = GetIndustryMoneyFlowTool(router, store)
    assert tool.supports_row_preview is True

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext(tier=Tier.FREE))

    assert output.free_row_count == 2
    assert len(output.rows) == 3
    free_rows = [row for row in output.rows if row.rank <= 2]
    locked_rows = [row for row in output.rows if row.rank > 2]
    assert len(free_rows) == 2
    assert len(locked_rows) == 1
    for row in free_rows:
        assert row.locked is False
        assert row.industry is not None
    locked_row = locked_rows[0]
    assert locked_row.locked is True
    assert locked_row.industry is None
    assert locked_row.is_total is True
    assert locked_row.trading_money_pct is None


async def test_get_industry_money_flow_never_overrides_an_explicit_start_date(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "TaiwanStockPrice", "TW", date(2026, 9, 7), [{"symbol": "2330", "trade_date": "2026-09-07"}]
    )
    symbol = Symbol(id="", market=Market.TW)
    router, adapter = make_router(
        symbol, {Capability.INDUSTRY_CHAIN_MONEY_FLOW: _result({"rows": []})}
    )
    tool = GetIndustryMoneyFlowTool(router, store)

    await tool.run(MarketOnlyInput(market=Market.TW, start_date=date(2026, 9, 4)), ToolContext())

    assert adapter.calls[0][2]["start_date"] == "2026-09-04"


async def test_get_securities_trader_info_reads_market_level_roster() -> None:
    symbol = Symbol(id="", market=Market.TW)
    row = {
        "market": "TW",
        "trader_id": "1020",
        "trader_name": "Fubon",
        "as_of": "2026-08-01",
        "address": None,
        "phone": None,
    }
    router, _ = make_router(symbol, {Capability.SECURITIES_TRADER_INFO: _result({"rows": [row]})})
    tool = GetSecuritiesTraderInfoTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert output.rows[0].trader_name == "Fubon"


async def test_get_government_bank_flows_is_sponsor_gated() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "trade_date": "2026-08-01",
        "buy_amount": 1.0,
        "sell_amount": 0.0,
        "buy": 1,
        "sell": 0,
        "bank_name": "Bank A",
    }
    router, _ = make_router(
        symbol,
        {
            Capability.GOVERNMENT_BANK_BUYSELL: _result(
                {"rows": [row]}, data_class=DataClass.SPONSOR
            )
        },
    )
    tool = GetGovernmentBankFlowsTool(router)
    assert tool.data_class is DataClass.SPONSOR
    args = DateRangeInput(id="2330", market=Market.TW)

    masked = await dispatch_tool(tool, args, ToolContext(tier=Tier.FREE))
    assert isinstance(masked, MaskedTeaser)

    unmasked = await dispatch_tool(tool, args, ToolContext(tier=Tier.PREMIUM))
    assert not isinstance(unmasked, MaskedTeaser)
