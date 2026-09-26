"""`get_advance_decline`/`get_price_momentum`/`get_sector_heatmap`/`get_sector_constituents`/
`get_stock_heatmap` — whole-market breadth/momentum/heatmap tools, each of which now branches
between TW's FinMind-ingested datasets (`TaiwanStockPrice`/`TaiwanStockInfo`) and US's
yfinance-ingested ones (`us_price_eod`/`us_sector_class`/`us_symbol_universe`). One TW test per
tool pins the pre-existing behavior unchanged; one US test per tool proves the new market branch
actually resolves the right dataset and produces real output; one "no ingested US partition yet"
test per a representative tool proves an honest `data_available=False` degrade, never a crash or
fabricated row."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.store import ParquetStore
from schemas.enums import Market
from tools.base import ToolContext
from tools.market_tools import (
    GetAdvanceDeclineInput,
    GetAdvanceDeclineTool,
    GetPriceMomentumInput,
    GetPriceMomentumTool,
    GetSectorConstituentsInput,
    GetSectorConstituentsTool,
    GetSectorHeatmapInput,
    GetSectorHeatmapTool,
    GetStockHeatmapInput,
    GetStockHeatmapTool,
)

_TODAY = date(2025, 6, 2)
_PRIOR = date(2025, 6, 1)


def _write_tw_fixture(store: ParquetStore) -> None:
    store.write_partition(
        "TaiwanStockInfo",
        "TW",
        _TODAY,
        [
            {
                "symbol": "2330",
                "market": "TW",
                "name": "台積電",
                "industry_category": "半導體業",
                "security_type": "twse",
                "as_of": _TODAY.isoformat(),
            },
            {
                "symbol": "2454",
                "market": "TW",
                "name": "聯發科",
                "industry_category": "半導體業",
                "security_type": "twse",
                "as_of": _TODAY.isoformat(),
            },
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        _PRIOR,
        [
            {"symbol": "2330", "trade_date": _PRIOR.isoformat(), "close": 100.0, "volume": 1000},
            {"symbol": "2454", "trade_date": _PRIOR.isoformat(), "close": 50.0, "volume": 500},
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        _TODAY,
        [
            {"symbol": "2330", "trade_date": _TODAY.isoformat(), "close": 110.0, "volume": 1200},
            {"symbol": "2454", "trade_date": _TODAY.isoformat(), "close": 48.0, "volume": 600},
        ],
    )


def _write_us_fixture(store: ParquetStore) -> None:
    store.write_partition(
        "us_symbol_universe",
        "US",
        _TODAY,
        [
            {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NMS"},
            {"symbol": "BRK-B", "name": "Berkshire Hathaway Inc.", "exchange": "NYQ"},
        ],
    )
    store.write_partition(
        "us_sector_class",
        "US",
        _TODAY,
        [
            {
                "symbol": "AAPL",
                "market": "US",
                "sector": "Technology",
                "industry": "Consumer Electronics",
                "sector_key": "technology",
                "industry_key": "consumer-electronics",
            },
            {
                "symbol": "BRK-B",
                "market": "US",
                "sector": "Financial Services",
                "industry": "Insurance—Diversified",
                "sector_key": "financial-services",
                "industry_key": "insurance-diversified",
            },
        ],
    )
    store.write_partition(
        "us_price_eod",
        "US",
        _PRIOR,
        [
            {
                "symbol": "AAPL",
                "market": "US",
                "trade_date": _PRIOR.isoformat(),
                "close": 200.0,
                "volume": 5000,
                "turnover": None,
            },
            {
                "symbol": "BRK-B",
                "market": "US",
                "trade_date": _PRIOR.isoformat(),
                "close": 400.0,
                "volume": 2000,
                "turnover": None,
            },
        ],
    )
    store.write_partition(
        "us_price_eod",
        "US",
        _TODAY,
        [
            {
                "symbol": "AAPL",
                "market": "US",
                "trade_date": _TODAY.isoformat(),
                "close": 220.0,
                "volume": 6000,
                "turnover": None,
            },
            {
                "symbol": "BRK-B",
                "market": "US",
                "trade_date": _TODAY.isoformat(),
                "close": 380.0,
                "volume": 2500,
                "turnover": None,
            },
        ],
    )


# ---------------------------------------------------------------------------
# get_advance_decline
# ---------------------------------------------------------------------------


async def test_advance_decline_tw_unchanged(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_tw_fixture(store)
    tool = GetAdvanceDeclineTool(store)

    output = await tool.run(GetAdvanceDeclineInput(market=Market.TW), ToolContext())

    assert output.data_available is True
    assert output.advancing_count == 1  # 2330 up
    assert output.declining_count == 1  # 2454 down


async def test_advance_decline_us_resolves_us_dataset(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_us_fixture(store)
    tool = GetAdvanceDeclineTool(store)

    output = await tool.run(GetAdvanceDeclineInput(market=Market.US), ToolContext())

    assert output.data_available is True
    assert output.advancing_count == 1  # AAPL 200 -> 220
    assert output.declining_count == 1  # BRK-B 400 -> 380


async def test_advance_decline_us_degrades_honestly_with_no_ingested_data(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    tool = GetAdvanceDeclineTool(store)

    output = await tool.run(GetAdvanceDeclineInput(market=Market.US), ToolContext())

    assert output.data_available is False
    assert output.unavailable_reason is not None


# ---------------------------------------------------------------------------
# get_price_momentum
# ---------------------------------------------------------------------------


async def test_price_momentum_tw_unchanged(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_tw_fixture(store)
    tool = GetPriceMomentumTool(store)

    output = await tool.run(
        GetPriceMomentumInput(market=Market.TW, period="day", trade_date=_TODAY), ToolContext()
    )

    assert output.data_available is True
    gainers = {row.symbol: row for row in output.top_gainers}
    assert gainers["2330"].stock_name == "台積電"


async def test_price_momentum_us_resolves_us_dataset_and_names(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_us_fixture(store)
    tool = GetPriceMomentumTool(store)

    output = await tool.run(
        GetPriceMomentumInput(market=Market.US, period="day", trade_date=_TODAY), ToolContext()
    )

    assert output.data_available is True
    gainers = {row.symbol: row for row in output.top_gainers}
    assert gainers["AAPL"].stock_name == "Apple Inc."
    # BRK-B (5+ chars, contains "-") must survive `is_scoreable_symbol` when the US market
    # branch is passed through — the pre-existing TW-hardcoded code-shape filter would silently
    # drop it if `market` weren't threaded correctly.
    losers = {row.symbol: row for row in output.top_losers}
    assert "BRK-B" in losers


# ---------------------------------------------------------------------------
# get_sector_heatmap
# ---------------------------------------------------------------------------


async def test_sector_heatmap_tw_unchanged(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_tw_fixture(store)
    tool = GetSectorHeatmapTool(store)

    output = await tool.run(
        GetSectorHeatmapInput(market=Market.TW, trade_date=_TODAY), ToolContext()
    )

    assert output.data_available is True
    industries = {row.industry for row in output.industries}
    assert industries == {"半導體業"}


async def test_sector_heatmap_us_groups_by_broad_sector_not_industry(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_us_fixture(store)
    tool = GetSectorHeatmapTool(store)

    output = await tool.run(
        GetSectorHeatmapInput(market=Market.US, trade_date=_TODAY), ToolContext()
    )

    assert output.data_available is True
    industries = {row.industry for row in output.industries}
    # Grouped by `sector` (Technology / Financial Services), never the finer `industry` field
    # (Consumer Electronics / Insurance—Diversified) — see `_industry_by_symbol_for`'s docstring.
    assert industries == {"Technology", "Financial Services"}
    # No `turnover` is ever populated for US (`ingest/us_jobs.py` always writes `None`) — every
    # industry must honestly degrade to the unweighted average, never a fabricated weighting.
    assert all(not row.weighted_by_turnover for row in output.industries)


# ---------------------------------------------------------------------------
# get_sector_constituents
# ---------------------------------------------------------------------------


async def test_sector_constituents_tw_unchanged(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_tw_fixture(store)
    tool = GetSectorConstituentsTool(store)

    output = await tool.run(
        GetSectorConstituentsInput(market=Market.TW, trade_date=_TODAY), ToolContext()
    )

    assert output.data_available is True
    assert {row.symbol for row in output.rows} == {"2330", "2454"}


async def test_sector_constituents_us_resolves_us_datasets(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_us_fixture(store)
    tool = GetSectorConstituentsTool(store)

    output = await tool.run(
        GetSectorConstituentsInput(market=Market.US, industry="Technology", trade_date=_TODAY),
        ToolContext(),
    )

    assert output.data_available is True
    assert {row.symbol for row in output.rows} == {"AAPL"}


# ---------------------------------------------------------------------------
# get_stock_heatmap
# ---------------------------------------------------------------------------


async def test_stock_heatmap_tw_unchanged(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_tw_fixture(store)
    tool = GetStockHeatmapTool(store)

    output = await tool.run(
        GetStockHeatmapInput(market=Market.TW, trade_date=_TODAY), ToolContext()
    )

    assert output.data_available is True
    assert {row.symbol for row in output.stocks} == {"2330", "2454"}


async def test_stock_heatmap_us_resolves_us_datasets(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_us_fixture(store)
    tool = GetStockHeatmapTool(store)

    output = await tool.run(
        GetStockHeatmapInput(market=Market.US, trade_date=_TODAY), ToolContext()
    )

    assert output.data_available is True
    by_symbol = {row.symbol: row for row in output.stocks}
    assert by_symbol["AAPL"].stock_name == "Apple Inc."
    assert by_symbol["AAPL"].industry == "Technology"
    assert by_symbol["BRK-B"].stock_name == "Berkshire Hathaway Inc."
