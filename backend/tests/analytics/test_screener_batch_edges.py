"""`compute_flag_rows` grouping and `_write_screener_flags` gates: which rows are ignored, which
sources make a symbol exist, output ordering, and the skip / empty-write paths of the job."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date as date_
from pathlib import Path

from analytics.screener.batch import (
    INSTITUTIONAL_FLOW_DATASET,
    PRICE_EOD_DATASET,
    SCREENER_FLAGS_DATASET,
    SHAREHOLDING_DISTRIBUTION_DATASET,
    compute_flag_rows,
    job_screener_flags,
)
from ingest.context import IngestContext
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04"]


def _flow(symbol: object, day: str, buy: int, sell: int) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": day,
        "investor": "Foreign_Investor",
        "buy": buy,
        "sell": sell,
    }


def _margin(symbol: object, day: str, margin_balance: int, short_balance: int) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": day,
        "margin_purchase_today_balance": margin_balance,
        "short_sale_today_balance": short_balance,
    }


def _holder(symbol: object, percent: float) -> dict[str, object]:
    return {"symbol": symbol, "holding_shares_level": "1-999", "percent": percent, "unit": 0}


def _tags(rows: list[dict[str, object]], symbol: str) -> set[object]:
    return {r["tag"] for r in rows if r["symbol"] == symbol}


def test_no_rows_gives_no_flags() -> None:
    assert compute_flag_rows([], [], [], []) == []


def test_rows_without_a_text_symbol_are_ignored_in_every_source() -> None:
    flow = [_flow(sym, d, 500, 100) for sym in (None, "", 2330) for d in DAYS]
    margin = [
        _margin(sym, d, 1000 + 100 * i, 500 - 100 * i)
        for sym in (None, "", 2330)
        for i, d in enumerate([*DAYS, "2024-01-05"])
    ]
    snapshots = [[_holder(sym, 10.0 + week) for sym in (None, "", 2330)] for week in range(4)]

    assert compute_flag_rows([], flow, margin, snapshots) == []


def test_a_symbol_in_only_one_source_still_gets_its_flags() -> None:
    flow = [_flow("1101", d, 500, 100) for d in DAYS]
    margin = [_margin("2330", d, 1000 + 100 * i, 500 - 100 * i) for i, d in enumerate([*DAYS, "x"])]
    snapshots = [[_holder("2409", 10.0 + week)] for week in range(4)]

    rows = compute_flag_rows([], flow, margin, snapshots)

    assert "foreign_net_buy_streak_3" in _tags(rows, "1101")
    assert _tags(rows, "2330") <= {
        "margin_purchase_balance_streak_up_3",
        "short_sale_balance_streak_down_3",
    }
    assert "small_holder_share_rising_3w" in _tags(rows, "2409")
    assert {r["symbol"] for r in rows} <= {"1101", "2330", "2409"}


def test_flag_rows_are_sorted_by_symbol_then_tag() -> None:
    flow = [
        *[_flow("2330", d, 500, 100) for d in DAYS],
        *[_flow("1101", d, 500, 100) for d in DAYS],
        *[
            {
                "symbol": "1101",
                "trade_date": d,
                "investor": "Investment_Trust",
                "buy": 500,
                "sell": 100,
            }
            for d in DAYS
        ],
    ]

    rows = compute_flag_rows([], flow)

    keys = [(str(r["symbol"]), str(r["tag"])) for r in rows]
    assert keys == sorted(keys)
    assert rows[0]["symbol"] == "1101"
    assert len(_tags(rows, "1101")) >= 2
    assert all(set(r) == {"symbol", "tag"} for r in rows)


def test_a_symbol_missing_from_some_weekly_snapshots_gets_no_holder_flag_and_no_error() -> None:
    snapshots: list[list[dict[str, object]]] = [
        [],
        [],
        [_holder("2330", 11.0)],
        [_holder("2330", 12.0)],
    ]

    assert compute_flag_rows([], [], [], snapshots) == []


def test_three_weekly_snapshots_are_never_enough_for_the_holder_streak() -> None:
    snapshots = [[_holder("2330", 10.0 + week)] for week in range(3)]
    assert compute_flag_rows([], [], [], snapshots) == []


def test_a_symbol_with_too_little_price_history_earns_no_row() -> None:
    price = [
        {
            "symbol": "2330",
            "trade_date": d,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1000,
        }
        for d in DAYS
    ]
    assert compute_flag_rows(price, []) == []


# ---- the job's gates -------------------------------------------------------------------------


def _ctx(root: Path, *, leader: object | None = None) -> IngestContext:
    return IngestContext(
        finmind=None,  # type: ignore[arg-type]  # this job makes no FinMind call
        store=ParquetStore(root=root),
        leader=leader or AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


_MONDAY = date_(2024, 7, 1)


def _price_row(symbol: str, day: date_) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "trade_date": day.isoformat(),
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "volume": 1000,
        "turnover": None,
    }


async def test_a_non_trading_day_is_a_journaled_skip(tmp_path: Path) -> None:
    run = await job_screener_flags(date_(2024, 6, 30), _ctx(tmp_path))

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "non-trading day (calendar-gated)"
    assert run.rows_written == 0


async def test_a_non_leader_writes_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, leader=NeverLeader())
    ctx.store.write_partition(PRICE_EOD_DATASET, "TW", _MONDAY, [_price_row("2330", _MONDAY)])

    run = await job_screener_flags(_MONDAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "not the leader — write skipped"
    assert not ctx.store.has_partition(SCREENER_FLAGS_DATASET, "TW", _MONDAY)


async def test_without_the_days_prices_it_skips_instead_of_using_older_prices(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    ctx.store.write_partition(
        PRICE_EOD_DATASET, "TW", date_(2024, 6, 28), [_price_row("2330", date_(2024, 6, 28))]
    )

    run = await job_screener_flags(_MONDAY, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "no TW price data for 2024-07-01 yet; not using older prices"
    assert not ctx.store.has_partition(SCREENER_FLAGS_DATASET, "TW", _MONDAY)


async def test_a_day_with_no_scoreable_history_writes_an_honest_empty_partition(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    # Prices exist for the day but belong to a symbol the scoreable filter rejects (an ETF-like
    # warrant code), so nothing is left to evaluate.
    ctx.store.write_partition(PRICE_EOD_DATASET, "TW", _MONDAY, [_price_row("030001", _MONDAY)])

    run = await job_screener_flags(_MONDAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == 0
    assert run.detail == "no upstream price/flow history in the read window — empty flag partition"
    assert ctx.store.has_partition(SCREENER_FLAGS_DATASET, "TW", _MONDAY)


async def test_a_day_with_chip_history_writes_the_computed_flags(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.store.write_partition(PRICE_EOD_DATASET, "TW", _MONDAY, [_price_row("030001", _MONDAY)])
    days = [date_(2024, 6, 26), date_(2024, 6, 27), date_(2024, 6, 28)]
    for day in days:
        ctx.store.write_partition(
            INSTITUTIONAL_FLOW_DATASET,
            "TW",
            day,
            [_flow("2330", day.isoformat(), 500, 100)],
        )

    run = await job_screener_flags(_MONDAY, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.detail == "evaluated the tag catalog over TW price/flow history"
    rows = ctx.store.read_partition(SCREENER_FLAGS_DATASET, "TW", _MONDAY)
    assert run.rows_written == len(rows)
    assert {"symbol": "2330", "tag": "foreign_net_buy_streak_3"} in [
        {"symbol": r["symbol"], "tag": r["tag"]} for r in rows
    ]


async def test_weekly_snapshots_on_or_before_the_run_feed_the_holder_streak(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    ctx.store.write_partition(PRICE_EOD_DATASET, "TW", _MONDAY, [_price_row("030001", _MONDAY)])
    weeks = [date_(2024, 6, 7), date_(2024, 6, 14), date_(2024, 6, 21), date_(2024, 6, 28)]
    for index, day in enumerate(weeks):
        ctx.store.write_partition(
            SHAREHOLDING_DISTRIBUTION_DATASET, "TW", day, [_holder("2409", 10.0 + index)]
        )
    # A snapshot after the run date must not be read.
    ctx.store.write_partition(
        SHAREHOLDING_DISTRIBUTION_DATASET, "TW", date_(2024, 7, 5), [_holder("2409", 1.0)]
    )

    await job_screener_flags(_MONDAY, ctx)

    rows = ctx.store.read_partition(SCREENER_FLAGS_DATASET, "TW", _MONDAY)
    assert "small_holder_share_rising_3w" in {r["tag"] for r in rows if r["symbol"] == "2409"}
