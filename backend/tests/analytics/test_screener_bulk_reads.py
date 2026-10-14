"""The screener's two whole-market numeric reads (technical indicators, ROE/EPS) are cheap, shared
and unchanged in value.

Before: every request with a technical or fundamentals condition re-read its window as row dicts
into the API process: 953 MB (technical) and 2,118 MB (fundamentals) on the dev data, per request,
so a burst of them, or one request with both, passed the API container's memory limit and got the
API killed. Now each is read once per data day as a filtered columnar frame (347 MB and 214 MB), and
requests share the result."""

from __future__ import annotations

import asyncio
from datetime import date

import pandas as pd
import pytest

from analytics.fundamentals import (
    ROE_INPUT_ITEMS,
    compute_fundamental_metrics,
    parse_statement_lines,
)
from analytics.screener import engine
from analytics.screener.engine import _bars_from_frame, _bulk_by_day
from ingest.store import ParquetStore, sql_in


@pytest.fixture(autouse=True)
def _clean_bulk_cache() -> None:
    engine._BULK_CACHE.clear()


# ---- shared per data day, one compute for a burst -----------------------------------------------


async def test_a_burst_of_requests_computes_once() -> None:
    computed = 0

    def compute() -> dict[str, dict[str, float]]:
        nonlocal computed
        computed += 1
        return {"2330": {"rsi_14": 55.0}}

    results = await asyncio.gather(
        *(_bulk_by_day("technical", "TW", date(2026, 10, 14), compute) for _ in range(20))
    )

    assert computed == 1
    assert all(r == {"2330": {"rsi_14": 55.0}} for r in results)


async def test_a_new_data_day_is_computed_again_and_the_old_one_is_dropped() -> None:
    days: list[date] = []

    def compute_for(day: date):  # type: ignore[no-untyped-def]
        def compute() -> dict[str, dict[str, float]]:
            days.append(day)
            return {"2330": {"rsi_14": float(day.day)}}

        return compute

    first = await _bulk_by_day(
        "technical", "TW", date(2026, 10, 14), compute_for(date(2026, 10, 14))
    )
    again = await _bulk_by_day(
        "technical", "TW", date(2026, 10, 14), compute_for(date(2026, 10, 14))
    )
    next_day = await _bulk_by_day(
        "technical", "TW", date(2026, 10, 15), compute_for(date(2026, 10, 15))
    )

    assert first == again
    assert next_day == {"2330": {"rsi_14": 15.0}}
    assert days == [date(2026, 10, 14), date(2026, 10, 15)]  # one per day, not per request
    assert len(engine._BULK_CACHE) == 1


async def test_kinds_and_no_data_do_not_interfere() -> None:
    assert await _bulk_by_day("technical", "TW", None, lambda: {"x": {"a": 1.0}}) == {}
    technical = await _bulk_by_day("technical", "TW", date(2026, 10, 14), lambda: {"a": {"f": 1.0}})
    fundamentals = await _bulk_by_day(
        "fundamentals", "TW", date(2026, 10, 14), lambda: {"b": {"f": 2.0}}
    )

    assert technical == {"a": {"f": 1.0}}
    assert fundamentals == {"b": {"f": 2.0}}


# ---- the bars built from a frame follow the same rule as before ---------------------------------


def test_bars_from_a_frame_skip_rows_without_a_usable_close_high_or_low() -> None:
    group = pd.DataFrame(
        {
            "symbol": ["2330"] * 5,
            "trade_date": ["2024-01-05", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-08"],
            "high": [12.0, 10.0, 11.0, None, 13.0],
            "low": [9.0, 8.0, 9.0, 7.0, 10.0],
            "close": [11.0, 9.0, 0.0, 8.0, 12.0],  # 01-03 has a zero close
            "volume": [100, None, 300, 400, 500],
        }
    )

    bars = _bars_from_frame(group)

    # ascending by date; 01-03 (zero close) and 01-04 (no high) dropped; a missing volume is 0
    assert [(b.close, b.volume) for b in bars] == [(9.0, 0), (11.0, 100), (12.0, 500)]


# ---- asking the store for only the ROE items changes nothing ------------------------------------


def _line(period: str, item: str, value: float, kind: str) -> dict[str, object]:
    return {
        "symbol": "2330",
        "market": "TW",
        "period_end": period,
        "statement_type": kind,
        "item": item,
        "origin_name": item,
        "value": value,
    }


def test_roe_from_only_its_input_items_equals_roe_from_every_line() -> None:
    periods = ["2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31", "2024-06-30"]
    every_line = []
    for i, period in enumerate(periods):
        every_line += [
            _line(period, "NetIncome", 100.0 + i, "income"),
            _line(period, "Revenue", 1000.0 + i, "income"),
            _line(period, "GrossProfit", 400.0 + i, "income"),
            _line(period, "OperatingIncome", 200.0 + i, "income"),
            _line(period, "Equity", 5000.0 + 10 * i, "balance_sheet"),
            _line(period, "TotalAssets", 9000.0 + i, "balance_sheet"),
        ]
    only_roe_items = [row for row in every_line if row["item"] in ROE_INPUT_ITEMS]
    assert 0 < len(only_roe_items) < len(every_line)

    full = compute_fundamental_metrics(parse_statement_lines(every_line), market_value=None).roe
    narrow = compute_fundamental_metrics(
        parse_statement_lines(only_roe_items), market_value=None
    ).roe

    assert full is not None
    assert narrow == full


def test_sql_in_quotes_each_value(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        "TaiwanStockBalanceSheet",
        "TW",
        date(2024, 1, 2),
        [
            {"symbol": "2330", "item": "Equity", "value": 1.0},
            {"symbol": "2330", "item": "O'Brien", "value": 2.0},
            {"symbol": "2330", "item": "TotalAssets", "value": 3.0},
        ],
    )

    frame = store.read_range_frame(
        "TaiwanStockBalanceSheet",
        "TW",
        [date(2024, 1, 2)],
        columns=("symbol", "item", "value"),
        where=sql_in("item", ("Equity", "O'Brien")),
    )

    assert sorted(frame["item"].tolist()) == ["Equity", "O'Brien"]
