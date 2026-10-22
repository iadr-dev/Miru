"""The strategy-watch job's gates and read paths (`_write_strategy_watchlist`) and the row
assembly in `build_watchlist_rows`: skips, staleness boundary, suspended stocks, the stock-info and
event feeds, the carried-forward day count, the per-strategy cap and the context columns."""

from __future__ import annotations

from datetime import date as date_
from pathlib import Path

from analytics.indicators import PriceBar
from analytics.strategy_watch.batch import (
    ATTENTION_STOCKS_DATASET,
    MAX_PER_STRATEGY,
    PRICE_EOD_DATASET,
    STOCK_INFO_DATASET,
    STRATEGY_WATCHLIST_DATASET,
    build_watchlist_rows,
    job_strategy_watchlist,
)
from analytics.strategy_watch.strategies import Regime, Strategy
from ingest.context import IngestContext
from ingest.journal import RunStatus
from ingest.leader import NeverLeader
from tests.analytics.test_strategy_watch import (
    _AS_OF,
    _RUN_DATE,
    _ctx,
    _oversold_series,
    _squeeze_series,
    _write_history,
)

OVERSOLD = Strategy.OVERSOLD_BOUNCE.value


def _write_series(
    ctx: IngestContext,
    series: dict[str, list[PriceBar]],
    last: date_,
    *,
    drop_last: set[str] = frozenset(),  # type: ignore[assignment]
) -> None:
    """One price partition per business day ending on `last`; `drop_last` symbols miss that day."""
    length = len(next(iter(series.values())))
    calendar = ctx.calendar
    days = calendar.business_days_between(calendar.shift_business_days(last, -(length - 1)), last)
    for index, day in enumerate(days):
        rows = [
            {
                "symbol": symbol,
                "market": "TW",
                "trade_date": day.isoformat(),
                "open": bars[index].open,
                "high": bars[index].high,
                "low": bars[index].low,
                "close": bars[index].close,
                "volume": bars[index].volume,
                "turnover": None,
            }
            for symbol, bars in series.items()
            if not (day == last and symbol in drop_last)
        ]
        ctx.store.write_partition(PRICE_EOD_DATASET, "TW", day, rows)


def _watchlist(ctx: IngestContext) -> list[dict[str, object]]:
    return ctx.store.read_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE)


# ---- the job's gates -------------------------------------------------------------------------


async def test_a_non_trading_day_is_skipped_before_the_leader_check(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    sunday = date_(2024, 6, 30)

    run = await job_strategy_watchlist(sunday, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "non-trading day (calendar-gated)"
    assert run.rows_written == 0


async def test_a_non_leader_writes_nothing(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, _AS_OF)
    standby = IngestContext(
        finmind=None,  # type: ignore[arg-type]
        store=ctx.store,
        leader=NeverLeader(),
        calendar=ctx.calendar,
        tls_policy=ctx.tls_policy,
    )

    run = await job_strategy_watchlist(_RUN_DATE, standby)

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "not the leader — write skipped"
    assert not ctx.store.has_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE)


async def test_an_empty_price_window_is_skipped(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    run = await job_strategy_watchlist(_RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    assert run.detail == "no price history in the read window"
    assert not ctx.store.has_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE)


async def test_prices_three_sessions_old_still_publish_but_four_do_not(tmp_path: Path) -> None:
    fresh = _ctx(tmp_path / "fresh")
    _write_history(fresh.store, fresh.calendar, date_(2024, 6, 25))  # 3 sessions before the 28th
    stale = _ctx(tmp_path / "stale")
    _write_history(stale.store, stale.calendar, date_(2024, 6, 24))  # 4 sessions before

    ok = await job_strategy_watchlist(_RUN_DATE, fresh)
    refused = await job_strategy_watchlist(_RUN_DATE, stale)

    assert ok.status is RunStatus.SUCCESS
    assert refused.status is RunStatus.SKIPPED
    assert refused.detail == ("prices are stale (last session 2024-06-24), watchlist not published")


async def test_a_stock_that_missed_the_last_session_is_left_off(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    series = {"2330": _squeeze_series(), "2317": _oversold_series()}
    _write_series(ctx, series, _AS_OF, drop_last={"2317"})

    run = await job_strategy_watchlist(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert {r["symbol"] for r in _watchlist(ctx)} == {"2330"}


async def test_the_journal_row_counts_what_was_written(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, _AS_OF)

    run = await job_strategy_watchlist(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert run.rows_written == len(_watchlist(ctx))
    assert run.rows_written >= 2


# ---- the context feeds -----------------------------------------------------------------------


async def test_name_and_industry_come_from_the_newest_stock_info_on_or_before_the_run(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, _AS_OF)
    ctx.store.write_partition(
        STOCK_INFO_DATASET,
        "TW",
        date_(2024, 6, 20),
        [{"symbol": "2317", "industry_category": "舊產業", "name": "舊名"}],
    )
    ctx.store.write_partition(
        STOCK_INFO_DATASET,
        "TW",
        date_(2024, 6, 27),
        [
            {"symbol": "2317", "industry_category": "其他電子業", "name": "鴻海"},
            {"symbol": "2330", "industry_category": "", "name": ""},  # blanks are not recorded
            {"symbol": 1234, "industry_category": "x", "name": "y"},  # a non-text symbol
        ],
    )
    ctx.store.write_partition(
        STOCK_INFO_DATASET,
        "TW",
        date_(2024, 7, 2),  # after the run date: not read
        [{"symbol": "2317", "industry_category": "未來", "name": "未來名"}],
    )

    await job_strategy_watchlist(_RUN_DATE, ctx)

    by_symbol = {r["symbol"]: r for r in _watchlist(ctx)}
    assert by_symbol["2317"]["name"] == "鴻海"
    assert by_symbol["2317"]["industry"] == "其他電子業"
    assert by_symbol["2330"]["name"] is None
    assert by_symbol["2330"]["industry"] is None
    assert by_symbol["2330"]["sector_strength_pct"] is None


async def test_a_flagged_stock_is_excluded_from_the_watchlist(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, _AS_OF)
    ctx.store.write_partition(
        ATTENTION_STOCKS_DATASET,
        "TW",
        _AS_OF,
        [{"symbol": "2317", "flagged_date": _AS_OF.isoformat()}],
    )

    await job_strategy_watchlist(_RUN_DATE, ctx)

    assert "2317" not in {r["symbol"] for r in _watchlist(ctx)}
    assert "2330" in {r["symbol"] for r in _watchlist(ctx)}


async def test_only_the_previous_trading_days_list_carries_the_day_count_forward(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, _AS_OF)
    previous_row = {
        "symbol": "2317",
        "strategy": OVERSOLD,
        "first_listed": "2024-06-26",
        "days_on_list": 3,
    }
    # The 28th is the previous trading day of the 1st: its list carries forward.
    ctx.store.write_partition(STRATEGY_WATCHLIST_DATASET, "TW", _AS_OF, [previous_row])

    await job_strategy_watchlist(_RUN_DATE, ctx)

    row = next(r for r in _watchlist(ctx) if r["symbol"] == "2317" and r["strategy"] == OVERSOLD)
    assert row["first_listed"] == "2024-06-26"
    assert row["days_on_list"] == 4
    other = next(r for r in _watchlist(ctx) if r["symbol"] == "2330")
    assert other["first_listed"] == _RUN_DATE.isoformat()
    assert other["days_on_list"] == 1


async def test_a_list_older_than_the_previous_trading_day_starts_the_count_again(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, _AS_OF)
    ctx.store.write_partition(
        STRATEGY_WATCHLIST_DATASET,
        "TW",
        date_(2024, 6, 27),
        [{"symbol": "2317", "strategy": OVERSOLD, "first_listed": "2024-06-26", "days_on_list": 3}],
    )

    await job_strategy_watchlist(_RUN_DATE, ctx)

    row = next(r for r in _watchlist(ctx) if r["symbol"] == "2317" and r["strategy"] == OVERSOLD)
    assert row["first_listed"] == _RUN_DATE.isoformat()
    assert row["days_on_list"] == 1


async def test_a_previous_row_with_a_bad_day_count_counts_as_one(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, _AS_OF)
    ctx.store.write_partition(
        STRATEGY_WATCHLIST_DATASET,
        "TW",
        _AS_OF,
        [{"symbol": "2317", "strategy": OVERSOLD, "first_listed": None, "days_on_list": True}],
    )

    await job_strategy_watchlist(_RUN_DATE, ctx)

    row = next(r for r in _watchlist(ctx) if r["symbol"] == "2317" and r["strategy"] == OVERSOLD)
    assert row["first_listed"] == _AS_OF.isoformat()  # falls back to the carried run's date
    assert row["days_on_list"] == 2


# ---- build_watchlist_rows --------------------------------------------------------------------


def _scaled_oversold(volume: int) -> list[PriceBar]:
    return [
        PriceBar(high=b.high, low=b.low, close=b.close, volume=volume, open=b.open)
        for b in _oversold_series()
    ]


def test_no_bars_gives_an_empty_risk_off_list_with_zero_breadth() -> None:
    rows, regime, breadth = build_watchlist_rows(
        run_date=_RUN_DATE,
        as_of=_AS_OF,
        bars={},
        industry_by_symbol={},
        name_by_symbol={},
        flow_rows=[],
        margin_rows=[],
        exclusions={},
        headlines={},
    )
    assert rows == []
    assert regime is Regime.RISK_OFF
    assert breadth == 0.0


def test_each_strategy_keeps_only_the_most_liquid_stocks_up_to_the_cap() -> None:
    count = MAX_PER_STRATEGY + 5
    # Distinct liquidity per stock: the higher the volume, the more liquid.
    bars = {f"{1100 + i}": _scaled_oversold(1_000_000 + 10_000 * i) for i in range(count)}

    rows, _, _ = build_watchlist_rows(
        run_date=_RUN_DATE,
        as_of=_AS_OF,
        bars=bars,
        industry_by_symbol={},
        name_by_symbol={},
        flow_rows=[],
        margin_rows=[],
        exclusions={},
        headlines={},
    )

    oversold = [r["symbol"] for r in rows if r["strategy"] == OVERSOLD]
    assert len(oversold) == MAX_PER_STRATEGY
    assert oversold == [f"{1100 + i}" for i in range(count - 1, count - 1 - MAX_PER_STRATEGY, -1)]


def test_equal_liquidity_is_ordered_by_symbol() -> None:
    bars = {"2330": _scaled_oversold(2_000_000), "1101": _scaled_oversold(2_000_000)}

    rows, _, _ = build_watchlist_rows(
        run_date=_RUN_DATE,
        as_of=_AS_OF,
        bars=bars,
        industry_by_symbol={},
        name_by_symbol={},
        flow_rows=[],
        margin_rows=[],
        exclusions={},
        headlines={},
    )

    assert [r["symbol"] for r in rows if r["strategy"] == OVERSOLD] == ["1101", "2330"]


def test_rows_carry_the_chip_streaks_margin_change_and_sector_context() -> None:
    flow = [
        {
            "symbol": "2317",
            "investor": "Foreign_Investor",
            "trade_date": f"2024-06-{d}",
            "buy": 10,
            "sell": 1,
        }
        for d in (26, 27, 28)
    ] + [
        {
            "symbol": "2317",
            "investor": "Investment_Trust",
            "trade_date": "2024-06-28",
            "buy": 0,
            "sell": 5,
        }
    ]
    margin = [
        {
            "symbol": "2317",
            "trade_date": f"2024-06-{d:02d}",
            "margin_purchase_today_balance": 1000 + 20 * i,
        }
        for i, d in enumerate(range(20, 29))
    ]

    rows, regime, breadth = build_watchlist_rows(
        run_date=_RUN_DATE,
        as_of=_AS_OF,
        bars={"2317": _oversold_series(), "2330": _squeeze_series()},
        industry_by_symbol={"2317": "其他電子業", "2330": "半導體業"},
        name_by_symbol={"2317": "鴻海"},
        flow_rows=flow,
        margin_rows=margin,
        exclusions={},
        headlines={"2317": "法說會"},
    )

    row = next(r for r in rows if r["symbol"] == "2317")
    assert row["run_date"] == _RUN_DATE.isoformat()
    assert row["name"] == "鴻海"
    assert row["industry"] == "其他電子業"
    assert row["foreign_streak"] == 3
    assert row["trust_streak"] == -1
    assert round(float(row["margin_change_5d_pct"]), 2) == round((1160 - 1060) / 1060 * 100, 2)  # type: ignore[arg-type]
    assert row["headline"] == "法說會"
    assert row["regime"] == regime.value
    assert row["breadth_pct"] == breadth
    assert row["sector_strength_pct"] in (0.0, 100.0)
    other = next(r for r in rows if r["symbol"] == "2330")
    assert other["name"] is None
    assert other["headline"] is None
    assert other["foreign_streak"] == 0
    assert other["margin_change_5d_pct"] is None
