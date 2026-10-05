"""`get_tw_market_rankings` — one test per kind, each writing known whole-market partitions
into a `ParquetStore` over `tmp_path` and asserting the ranked output, mirroring
`test_market_breadth_heatmap_tools.py`'s own write-then-read fixture pattern."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.jobs import MARGIN_SHORT_JOB, MONTH_REVENUE_JOB, STOCK_INFO_JOB, VALUATION_RATIOS_JOB
from ingest.store import ParquetStore
from tools.base import ToolContext
from tools.tw_ranking_tools import GetTwMarketRankingsInput, GetTwMarketRankingsTool, TwRankingKind

_TODAY = date(2025, 6, 2)
_INSTITUTIONAL_FLOW_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"

_NAMES = {
    "2330": "台積電",
    "2454": "聯發科",
}


def _write_stock_info(store: ParquetStore) -> None:
    store.write_partition(
        STOCK_INFO_JOB.dataset,
        "TW",
        _TODAY,
        [
            {
                "symbol": symbol,
                "market": "TW",
                "name": name,
                "industry_category": "半導體業",
                "security_type": "twse",
                "as_of": _TODAY.isoformat(),
            }
            for symbol, name in _NAMES.items()
        ],
    )


async def test_dealer_streak_ranks_by_streak_length(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_stock_info(store)
    # 2330 on a 3-day buy streak, 2454 on a 2-day sell streak.
    store.write_partition(
        _INSTITUTIONAL_FLOW_DATASET,
        "TW",
        date(2025, 5, 29),
        [
            {
                "symbol": "2330",
                "trade_date": "2025-05-29",
                "investor": "Dealer_self",
                "buy": 80,
                "sell": 40,
            },
            {
                "symbol": "2454",
                "trade_date": "2025-05-29",
                "investor": "Dealer_self",
                "buy": 90,
                "sell": 30,
            },
        ],
    )
    store.write_partition(
        _INSTITUTIONAL_FLOW_DATASET,
        "TW",
        date(2025, 5, 30),
        [
            {
                "symbol": "2330",
                "trade_date": "2025-05-30",
                "investor": "Dealer_self",
                "buy": 100,
                "sell": 50,
            },
            {
                "symbol": "2454",
                "trade_date": "2025-05-30",
                "investor": "Dealer_self",
                "buy": 50,
                "sell": 100,
            },
        ],
    )
    store.write_partition(
        _INSTITUTIONAL_FLOW_DATASET,
        "TW",
        _TODAY,
        [
            {
                "symbol": "2330",
                "trade_date": "2025-06-02",
                "investor": "Dealer_self",
                "buy": 200,
                "sell": 50,
            },
            {
                "symbol": "2454",
                "trade_date": "2025-06-02",
                "investor": "Dealer_self",
                "buy": 10,
                "sell": 80,
            },
        ],
    )

    tool = GetTwMarketRankingsTool(store)
    output = await tool.run(
        GetTwMarketRankingsInput(kind=TwRankingKind.DEALER_STREAK), ToolContext()
    )

    assert output.data_available is True
    assert [row.symbol for row in output.rows] == ["2330", "2454"]
    assert output.rows[0].extra_value == 3.0
    assert output.rows[1].extra_value == -2.0
    assert output.rows[0].name == "台積電"


async def test_dealer_streak_symbol_filter_returns_one_row_ignoring_top_n(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_stock_info(store)
    store.write_partition(
        _INSTITUTIONAL_FLOW_DATASET,
        "TW",
        date(2025, 5, 29),
        [
            {
                "symbol": "2330",
                "trade_date": "2025-05-29",
                "investor": "Dealer_self",
                "buy": 80,
                "sell": 40,
            },
            {
                "symbol": "2454",
                "trade_date": "2025-05-29",
                "investor": "Dealer_self",
                "buy": 90,
                "sell": 30,
            },
        ],
    )
    store.write_partition(
        _INSTITUTIONAL_FLOW_DATASET,
        "TW",
        _TODAY,
        [
            {
                "symbol": "2330",
                "trade_date": "2025-06-02",
                "investor": "Dealer_self",
                "buy": 200,
                "sell": 50,
            },
            {
                "symbol": "2454",
                "trade_date": "2025-06-02",
                "investor": "Dealer_self",
                "buy": 10,
                "sell": 80,
            },
        ],
    )

    tool = GetTwMarketRankingsTool(store)
    output = await tool.run(
        GetTwMarketRankingsInput(kind=TwRankingKind.DEALER_STREAK, symbol="2330"), ToolContext()
    )

    assert output.data_available is True
    assert [row.symbol for row in output.rows] == ["2330"]
    assert output.rows[0].extra_value == 2.0


async def test_symbol_filter_returns_empty_rows_when_symbol_has_no_streak(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_stock_info(store)
    store.write_partition(
        _INSTITUTIONAL_FLOW_DATASET,
        "TW",
        _TODAY,
        [
            {
                "symbol": "2330",
                "trade_date": "2025-06-02",
                "investor": "Dealer_self",
                "buy": 200,
                "sell": 50,
            }
        ],
    )

    tool = GetTwMarketRankingsTool(store)
    output = await tool.run(
        GetTwMarketRankingsInput(kind=TwRankingKind.DEALER_STREAK, symbol="9999"), ToolContext()
    )

    assert output.data_available is True
    assert output.rows == ()


async def test_margin_change_ranks_by_signed_balance_delta(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_stock_info(store)
    store.write_partition(
        MARGIN_SHORT_JOB.dataset,
        "TW",
        _TODAY,
        [
            {
                "symbol": "2330",
                "trade_date": _TODAY.isoformat(),
                "margin_purchase_buy": 100,
                "margin_purchase_cash_repayment": 0,
                "margin_purchase_limit": 500000,
                "margin_purchase_sell": 50,
                "margin_purchase_today_balance": 20000,
                "margin_purchase_yesterday_balance": 19000,
                "note": None,
                "offset_loan_and_short": 0,
                "short_sale_buy": 0,
                "short_sale_cash_repayment": 0,
                "short_sale_limit": 100000,
                "short_sale_sell": 0,
                "short_sale_today_balance": 0,
                "short_sale_yesterday_balance": 0,
            },
            {
                "symbol": "2454",
                "trade_date": _TODAY.isoformat(),
                "margin_purchase_buy": 10,
                "margin_purchase_cash_repayment": 0,
                "margin_purchase_limit": 500000,
                "margin_purchase_sell": 100,
                "margin_purchase_today_balance": 5000,
                "margin_purchase_yesterday_balance": 5500,
                "note": None,
                "offset_loan_and_short": 0,
                "short_sale_buy": 0,
                "short_sale_cash_repayment": 0,
                "short_sale_limit": 100000,
                "short_sale_sell": 0,
                "short_sale_today_balance": 0,
                "short_sale_yesterday_balance": 0,
            },
        ],
    )

    tool = GetTwMarketRankingsTool(store)
    output = await tool.run(
        GetTwMarketRankingsInput(kind=TwRankingKind.MARGIN_CHANGE), ToolContext()
    )

    assert output.data_available is True
    assert [row.symbol for row in output.rows] == ["2330", "2454"]
    assert output.rows[0].extra_value == 1000.0
    assert output.rows[1].extra_value == -500.0


async def test_dividend_yield_ranks_descending(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_stock_info(store)
    store.write_partition(
        VALUATION_RATIOS_JOB.dataset,
        "TW",
        _TODAY,
        [
            {
                "symbol": "2330",
                "trade_date": _TODAY.isoformat(),
                "dividend_yield": 1.5,
                "per": 20.0,
                "pbr": 6.0,
            },
            {
                "symbol": "2454",
                "trade_date": _TODAY.isoformat(),
                "dividend_yield": 3.0,
                "per": 15.0,
                "pbr": 4.0,
            },
        ],
    )

    tool = GetTwMarketRankingsTool(store)
    output = await tool.run(
        GetTwMarketRankingsInput(kind=TwRankingKind.DIVIDEND_YIELD), ToolContext()
    )

    assert output.data_available is True
    assert [row.symbol for row in output.rows] == ["2454", "2330"]
    assert output.rows[0].extra_value == 3.0


async def test_revenue_growth_computes_real_yoy_match(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _write_stock_info(store)
    store.write_partition(
        MONTH_REVENUE_JOB.dataset,
        "TW",
        _TODAY,
        [
            {
                "symbol": "2330",
                "market": "TW",
                "trade_date": _TODAY.isoformat(),
                "country": "TW",
                "revenue": 1_200_000,
                "revenue_month": 5,
                "revenue_year": 2025,
                "create_time": None,
            }
        ],
    )
    store.write_partition(
        MONTH_REVENUE_JOB.dataset,
        "TW",
        date(2024, 6, 5),
        [
            {
                "symbol": "2330",
                "market": "TW",
                "trade_date": "2024-06-05",
                "country": "TW",
                "revenue": 1_000_000,
                "revenue_month": 5,
                "revenue_year": 2024,
                "create_time": None,
            }
        ],
    )

    tool = GetTwMarketRankingsTool(store)
    output = await tool.run(
        GetTwMarketRankingsInput(kind=TwRankingKind.REVENUE_GROWTH), ToolContext()
    )

    assert output.data_available is True
    assert len(output.rows) == 1
    assert output.rows[0].symbol == "2330"
    assert output.rows[0].extra_value == 20.0


async def test_degrades_honestly_with_no_ingested_data(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    tool = GetTwMarketRankingsTool(store)

    output = await tool.run(
        GetTwMarketRankingsInput(kind=TwRankingKind.DIVIDEND_YIELD), ToolContext()
    )

    assert output.data_available is False
    assert output.unavailable_reason is not None
    assert output.rows == ()


async def test_full_ranking_is_cached_and_recomputed_after_ingest_writes_a_new_date(
    tmp_path: Path,
) -> None:
    """The whole-market ranking is computed once per kind; `top_n`/`symbol` only slice it. A new
    `date=` partition changes the store fingerprint, so the next call reflects it."""

    def row(symbol: str, today: int, yesterday: int) -> dict[str, object]:
        return {
            "symbol": symbol,
            "trade_date": _TODAY.isoformat(),
            "margin_purchase_today_balance": today,
            "margin_purchase_yesterday_balance": yesterday,
        }

    store = ParquetStore(root=tmp_path)
    _write_stock_info(store)
    store.write_partition(MARGIN_SHORT_JOB.dataset, "TW", _TODAY, [row("2330", 2000, 1000)])
    tool = GetTwMarketRankingsTool(store)
    kind = TwRankingKind.MARGIN_CHANGE

    first = await tool.run(GetTwMarketRankingsInput(kind=kind), ToolContext())
    assert [r.symbol for r in first.rows] == ["2330"]

    # Same data, different slice: served from the cached full ranking, no re-read.
    reads: list[str] = []
    real_read = store.read_partition
    store.read_partition = lambda *a, **k: (reads.append("read"), real_read(*a, **k))[1]  # type: ignore[method-assign]
    single = await tool.run(GetTwMarketRankingsInput(kind=kind, symbol="2330"), ToolContext())
    assert [r.symbol for r in single.rows] == ["2330"]
    assert reads == []

    store.read_partition = real_read  # type: ignore[method-assign]
    next_day = date(2025, 6, 3)
    store.write_partition(MARGIN_SHORT_JOB.dataset, "TW", next_day, [row("2454", 9000, 1000)])
    fresh = await tool.run(GetTwMarketRankingsInput(kind=kind), ToolContext())
    assert fresh.trade_date == next_day
    assert [r.symbol for r in fresh.rows] == ["2454"]
