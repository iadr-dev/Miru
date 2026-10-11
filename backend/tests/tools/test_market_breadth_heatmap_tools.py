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
from tools.market import (
    GetAdvanceDeclineInput,
    GetAdvanceDeclineTool,
    GetPriceHistoryInput,
    GetPriceHistoryTool,
    GetPriceMomentumInput,
    GetPriceMomentumTool,
    GetSectorConstituentsInput,
    GetSectorConstituentsTool,
    GetSectorHeatmapInput,
    GetSectorHeatmapTool,
    GetStockHeatmapInput,
    GetStockHeatmapTool,
    GetSymbolInfoInput,
    GetSymbolInfoTool,
    GetSymbolQuotesInput,
    GetSymbolQuotesOutput,
    GetSymbolQuotesTool,
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
            {
                "symbol": "2330",
                "trade_date": _PRIOR.isoformat(),
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
            },
            {
                "symbol": "2454",
                "trade_date": _PRIOR.isoformat(),
                "open": 50.0,
                "high": 50.0,
                "low": 50.0,
                "close": 50.0,
                "volume": 500,
            },
        ],
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        _TODAY,
        [
            {
                "symbol": "2330",
                "trade_date": _TODAY.isoformat(),
                "open": 110.0,
                "high": 110.0,
                "low": 110.0,
                "close": 110.0,
                "volume": 1200,
            },
            {
                "symbol": "2454",
                "trade_date": _TODAY.isoformat(),
                "open": 48.0,
                "high": 48.0,
                "low": 48.0,
                "close": 48.0,
                "volume": 600,
            },
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
                "open": 200.0,
                "high": 200.0,
                "low": 200.0,
                "close": 200.0,
                "volume": 5000,
                "turnover": None,
            },
            {
                "symbol": "BRK-B",
                "market": "US",
                "trade_date": _PRIOR.isoformat(),
                "open": 400.0,
                "high": 400.0,
                "low": 400.0,
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
                "open": 220.0,
                "high": 220.0,
                "low": 220.0,
                "close": 220.0,
                "volume": 6000,
                "turnover": None,
            },
            {
                "symbol": "BRK-B",
                "market": "US",
                "trade_date": _TODAY.isoformat(),
                "open": 380.0,
                "high": 380.0,
                "low": 380.0,
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


# ---------------------------------------------------------------------------
# get_symbol_info — backs `useSymbolQuotes`' name/industry column for the AI leaderboard and
# 漲跌幅排行 momentum cards; without a US branch here those cards' 價格/走勢 columns render "—"
# for every US row even though the ranking itself resolves real US data.
# ---------------------------------------------------------------------------


async def test_symbol_info_tw_unchanged(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_tw_fixture(store)
    tool = GetSymbolInfoTool(store)

    output = await tool.run(
        GetSymbolInfoInput(symbols=("2330", "2454"), market=Market.TW), ToolContext()
    )

    assert output.data_available is True
    by_symbol = {row.symbol: row for row in output.rows}
    assert by_symbol["2330"].name == "台積電"
    assert by_symbol["2330"].industry_category == "半導體業"


async def test_symbol_info_us_resolves_us_universe_and_sector(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_us_fixture(store)
    tool = GetSymbolInfoTool(store)

    output = await tool.run(
        GetSymbolInfoInput(symbols=("AAPL", "BRK-B"), market=Market.US), ToolContext()
    )

    assert output.data_available is True
    by_symbol = {row.symbol: row for row in output.rows}
    assert by_symbol["AAPL"].name == "Apple Inc."
    assert by_symbol["AAPL"].industry_category == "Technology"
    assert by_symbol["BRK-B"].name == "Berkshire Hathaway Inc."
    assert by_symbol["BRK-B"].security_type == "equity"


async def test_symbol_info_us_degrades_honestly_with_no_ingested_data(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    tool = GetSymbolInfoTool(store)

    output = await tool.run(GetSymbolInfoInput(symbols=("AAPL",), market=Market.US), ToolContext())

    assert output.data_available is False
    assert output.unavailable_reason is not None


# ---------------------------------------------------------------------------
# get_price_history — backs `useSymbolQuotes`' price/chg%/sparkline columns.
# ---------------------------------------------------------------------------


async def test_price_history_tw_unchanged(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_tw_fixture(store)
    tool = GetPriceHistoryTool(store)

    output = await tool.run(
        GetPriceHistoryInput(symbols=("2330",), market=Market.TW, days=2), ToolContext()
    )

    assert output.data_available is True
    closes = [row.close for row in output.rows if row.symbol == "2330"]
    assert closes == [100.0, 110.0]


async def test_price_history_us_resolves_us_price_eod_dataset(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_us_fixture(store)
    tool = GetPriceHistoryTool(store)

    output = await tool.run(
        GetPriceHistoryInput(symbols=("AAPL", "BRK-B"), market=Market.US, days=2), ToolContext()
    )

    assert output.data_available is True
    closes_by_symbol: dict[str, list[float]] = {}
    for row in output.rows:
        closes_by_symbol.setdefault(row.symbol, []).append(row.close)
    assert closes_by_symbol["AAPL"] == [200.0, 220.0]
    assert closes_by_symbol["BRK-B"] == [400.0, 380.0]


# ---------------------------------------------------------------------------
# get_symbol_quotes — the list-row view behind `useSymbolQuotes`.
# ---------------------------------------------------------------------------


async def test_symbol_quotes_change_is_daily_whatever_the_sparkline_window(
    tmp_path: Path,
) -> None:
    """A 20-day sparkline window must not turn 漲跌% into a 20-day change: the change is
    always the latest close vs. the previous trading day's close."""
    store = ParquetStore(root=tmp_path)
    _write_tw_fixture(store)
    tool = GetSymbolQuotesTool(store)

    output = await tool.run(
        GetSymbolQuotesInput(symbols=("2454", "2330"), market=Market.TW, days=20), ToolContext()
    )

    assert isinstance(output, GetSymbolQuotesOutput)
    assert [row.symbol for row in output.rows] == ["2454", "2330"]  # request order kept
    tsmc = output.rows[1]
    assert tsmc.name == "台積電"
    assert tsmc.close == 110.0
    assert tsmc.prev_close == 100.0
    assert tsmc.change == 10.0
    assert tsmc.change_pct is not None
    assert round(tsmc.change_pct, 6) == 10.0
    assert tsmc.volume == 1200
    assert tsmc.sparkline == (100.0, 110.0)
    mediatek = output.rows[0]
    assert mediatek.change_pct is not None
    assert round(mediatek.change_pct, 6) == -4.0


async def test_symbol_quotes_unknown_symbol_keeps_an_empty_row(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_tw_fixture(store)
    tool = GetSymbolQuotesTool(store)

    output = await tool.run(
        GetSymbolQuotesInput(symbols=("9999",), market=Market.TW), ToolContext()
    )

    assert isinstance(output, GetSymbolQuotesOutput)
    (row,) = output.rows
    assert row.symbol == "9999"
    assert row.name is None
    assert row.close is None
    assert row.change_pct is None
    assert row.sparkline == ()
