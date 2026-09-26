"""`get_symbol_directory_search` — the TW roster type-ahead that backs `/search?market=TW`
(the `get_symbol_search`/yfinance tool explicitly refuses TW; this is its ingested-roster
counterpart, same `STOCK_INFO_JOB` roster `GetSymbolInfoTool` reads)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.store import ParquetStore
from schemas.enums import Market
from tools.base import ToolContext
from tools.market_tools import (
    GetSymbolDirectorySearchInput,
    GetSymbolDirectorySearchTool,
)

_ROSTER_DAY = date(2025, 5, 7)

_ROSTER_ROWS = [
    {
        "symbol": "2330",
        "market": "TW",
        "name": "台積電",
        "industry_category": "半導體業",
        "security_type": "twse",
        "as_of": "2025-05-07",
    },
    {
        "symbol": "2454",
        "market": "TW",
        "name": "聯發科",
        "industry_category": "半導體業",
        "security_type": "twse",
        "as_of": "2025-05-07",
    },
    {
        "symbol": "0050",
        "market": "TW",
        "name": "元大台灣50",
        "industry_category": "ETF",
        "security_type": "twse",
        "as_of": "2025-05-07",
    },
    {
        "symbol": "00631L",
        "market": "TW",
        "name": "元大台灣50正2",
        "industry_category": "ETF",
        "security_type": "twse",
        "as_of": "2025-05-07",
    },
    # A warrant-shaped code (6-digit, not `00`-prefixed) — must never surface in search results,
    # same `is_scoreable_symbol` filter every other whole-roster reader applies.
    {
        "symbol": "031914",
        "market": "TW",
        "name": "台積電購01",
        "industry_category": "認購權證",
        "security_type": "twse",
        "as_of": "2025-05-07",
    },
]


def _store_with_roster(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(root=tmp_path)
    store.write_partition("TaiwanStockInfo", "TW", _ROSTER_DAY, _ROSTER_ROWS)
    return store


async def test_symbol_prefix_match_ranks_above_name_substring_match(tmp_path: Path) -> None:
    tool = GetSymbolDirectorySearchTool(_store_with_roster(tmp_path))

    output = await tool.run(
        GetSymbolDirectorySearchInput(query="2330", market=Market.TW), ToolContext()
    )

    assert [row.symbol for row in output.rows] == ["2330"]
    assert output.rows[0].name == "台積電"
    assert output.rows[0].instrument_kind == "stock"


async def test_query_matches_by_name_substring_too(tmp_path: Path) -> None:
    tool = GetSymbolDirectorySearchTool(_store_with_roster(tmp_path))

    output = await tool.run(
        GetSymbolDirectorySearchInput(query="台積電", market=Market.TW), ToolContext()
    )

    assert output.rows[0].symbol == "2330"


async def test_etf_symbols_are_classified_as_etf_including_leveraged(tmp_path: Path) -> None:
    tool = GetSymbolDirectorySearchTool(_store_with_roster(tmp_path))

    output = await tool.run(
        GetSymbolDirectorySearchInput(query="元大台灣50", market=Market.TW), ToolContext()
    )

    kinds = {row.symbol: row.instrument_kind for row in output.rows}
    assert kinds["0050"] == "etf"
    assert kinds["00631L"] == "etf"


async def test_warrant_shaped_codes_are_excluded_from_results(tmp_path: Path) -> None:
    tool = GetSymbolDirectorySearchTool(_store_with_roster(tmp_path))

    output = await tool.run(
        GetSymbolDirectorySearchInput(query="台積電", market=Market.TW), ToolContext()
    )

    assert "031914" not in {row.symbol for row in output.rows}


async def test_symbol_listed_under_multiple_industry_categories_appears_once(
    tmp_path: Path,
) -> None:
    """Regression guard: the ingested roster carries duplicate `(symbol, name)` rows under more
    than one `industry_category` (confirmed live for real TWSE symbols) — a search result must
    surface each symbol once, not once per category row."""
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "TaiwanStockInfo",
        "TW",
        _ROSTER_DAY,
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
                "symbol": "2330",
                "market": "TW",
                "name": "台積電",
                "industry_category": "電子工業",
                "security_type": "twse",
                "as_of": "2025-05-07",
            },
        ],
    )
    tool = GetSymbolDirectorySearchTool(store)

    output = await tool.run(
        GetSymbolDirectorySearchInput(query="2330", market=Market.TW), ToolContext()
    )

    assert len(output.rows) == 1


async def test_limit_bounds_the_returned_row_count(tmp_path: Path) -> None:
    tool = GetSymbolDirectorySearchTool(_store_with_roster(tmp_path))

    output = await tool.run(
        GetSymbolDirectorySearchInput(query="台", market=Market.TW, limit=1), ToolContext()
    )

    assert len(output.rows) == 1


async def test_no_ingested_roster_reports_unavailable_without_raising(tmp_path: Path) -> None:
    tool = GetSymbolDirectorySearchTool(ParquetStore(root=tmp_path))

    output = await tool.run(
        GetSymbolDirectorySearchInput(query="2330", market=Market.TW), ToolContext()
    )

    assert output.data_available is False
    assert output.unavailable_reason is not None
    assert output.rows == ()
