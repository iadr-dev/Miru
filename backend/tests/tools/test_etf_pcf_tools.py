from __future__ import annotations

from datetime import UTC, date, datetime

from ingest.jobs import ETF_AUM_DATASET, ETF_NAV_DATASET
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import SymbolInput
from tools.etf_pcf_tools import (
    GetEtfFundInfoTool,
    GetEtfPremiumDiscountInput,
    GetEtfPremiumDiscountTool,
    GetEtfThemeTagsTool,
)


async def test_get_etf_fund_info_delegates_and_parses() -> None:
    symbol = Symbol(id="0050", market=Market.TW)
    info = {
        "symbol": "0050",
        "market": "TW",
        "fund_name": "元大台灣50",
        "fund_type": "被動式",
        "tracked_index": "台灣50指數",
        "units_outstanding": 1000,
        "listed_date": "2003-06-30",
        "manager_company": "元大投信",
    }
    result = SourceResult(
        source_id="ETF_PCF",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={"info": info},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, adapter = make_router(symbol, {Capability.ETF_HOLDINGS: result})
    tool = GetEtfFundInfoTool(router)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(SymbolInput(id="0050", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.ETF_HOLDINGS
    assert output.info is not None
    assert output.info.fund_name == "元大台灣50"


async def test_get_etf_theme_tags_delegates_and_parses() -> None:
    symbol = Symbol(id="00929", market=Market.TW)
    result = SourceResult(
        source_id="ETF_THEME_TAGS",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={"tags": ["高股息", "科技主題型"]},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, adapter = make_router(symbol, {Capability.ETF_THEME_TAGS: result})
    tool = GetEtfThemeTagsTool(router)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(SymbolInput(id="00929", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.ETF_THEME_TAGS
    assert output.tags == ("高股息", "科技主題型")


async def test_get_etf_theme_tags_empty_when_no_tags_in_payload() -> None:
    symbol = Symbol(id="0050", market=Market.TW)
    result = SourceResult(
        source_id="ETF_THEME_TAGS",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={},
        health=SourceHealth(status=HealthStatus.STALE, detail="not found"),
    )
    router, _ = make_router(symbol, {Capability.ETF_THEME_TAGS: result})
    tool = GetEtfThemeTagsTool(router)

    output = await tool.run(SymbolInput(id="0050", market=Market.TW), ToolContext())

    assert output.tags == ()


def _etf_nav_row(symbol: str, as_of: date, premium_discount_pct: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "as_of": as_of.isoformat(),
        "fund_name": "統一台股增長",
        "close_price": 29.25,
        "estimated_nav": 29.10,
        "premium_discount_pct": premium_discount_pct,
        "prior_day_nav": 29.00,
    }


async def test_get_etf_premium_discount_returns_bounded_history(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        ETF_NAV_DATASET,
        "TW",
        date(2026, 8, 25),
        [_etf_nav_row("00981A", date(2026, 8, 25), 0.11)],
    )
    store.write_partition(
        ETF_NAV_DATASET,
        "TW",
        date(2026, 8, 26),
        [_etf_nav_row("00981A", date(2026, 8, 26), -0.03)],
    )
    store.write_partition(
        ETF_AUM_DATASET,
        "TW",
        date(2026, 8, 26),
        [{"symbol": "00981A", "market": "TW", "as_of": "2026-08-26", "aum": 28_700_000_000.0}],
    )

    tool = GetEtfPremiumDiscountTool(store)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(
        GetEtfPremiumDiscountInput(id="00981A", market=Market.TW, days=30), ToolContext()
    )

    assert output.data_available is True
    assert [row.as_of for row in output.rows] == [date(2026, 8, 25), date(2026, 8, 26)]
    assert output.rows[-1].premium_discount_pct == -0.03
    assert output.current_aum == 28_700_000_000.0
    assert output.aum_caveat is not None


async def test_get_etf_premium_discount_honestly_reports_no_partition(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    tool = GetEtfPremiumDiscountTool(store)

    output = await tool.run(
        GetEtfPremiumDiscountInput(id="00981A", market=Market.TW), ToolContext()
    )

    assert output.data_available is False
    assert output.unavailable_reason is not None
    assert output.rows == ()


async def test_get_etf_premium_discount_dedupes_carried_forward_as_of(tmp_path: object) -> None:
    """Confirmed against real ingested data: a day's `etf_nav` partition can carry forward the
    prior confirmed `EtfNavRow.as_of` (partition `date` != row `as_of`), so several partitions
    can yield rows for the same `as_of`. The tool must dedupe on `as_of`, never emit a repeated
    trade date in the history."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        ETF_NAV_DATASET, "TW", date(2026, 8, 8), [_etf_nav_row("00981A", date(2026, 8, 7), 0.01)]
    )
    store.write_partition(
        ETF_NAV_DATASET, "TW", date(2026, 8, 9), [_etf_nav_row("00981A", date(2026, 8, 7), 0.01)]
    )
    store.write_partition(
        ETF_NAV_DATASET, "TW", date(2026, 8, 10), [_etf_nav_row("00981A", date(2026, 8, 10), 0.02)]
    )
    tool = GetEtfPremiumDiscountTool(store)

    output = await tool.run(
        GetEtfPremiumDiscountInput(id="00981A", market=Market.TW, days=30), ToolContext()
    )

    assert [row.as_of for row in output.rows] == [date(2026, 8, 7), date(2026, 8, 10)]


async def test_get_etf_premium_discount_honestly_reports_unknown_symbol(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        ETF_NAV_DATASET, "TW", date(2026, 8, 26), [_etf_nav_row("0050", date(2026, 8, 26), 0.02)]
    )
    tool = GetEtfPremiumDiscountTool(store)

    output = await tool.run(
        GetEtfPremiumDiscountInput(id="00981A", market=Market.TW), ToolContext()
    )

    assert output.data_available is False
    assert output.rows == ()
