"""策略盯盤 08:00 screen: each strategy setup on hand-built series, the context/exclusion layer,
and the job end to end on a real ParquetStore (no network)."""

from __future__ import annotations

import math
from calendar.trading_days import TradingDayCalendar
from datetime import date as date_
from pathlib import Path

import pytest

from analytics.indicators import PriceBar, accumulation_distribution
from analytics.strategy_watch import context
from analytics.strategy_watch.batch import (
    PRICE_EOD_DATASET,
    STRATEGY_WATCHLIST_DATASET,
    build_watchlist_rows,
    job_strategy_watchlist,
)
from analytics.strategy_watch.strategies import (
    MIN_HISTORY,
    Regime,
    Strategy,
    evaluate_setups,
    regime_from_breadth,
)
from ingest.context import IngestContext
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy

_AS_OF = date_(2024, 6, 28)  # a Friday
_RUN_DATE = date_(2024, 7, 1)  # the Monday morning after
_VOLUME = 2_000_000


def _bar(close: float, *, up: float = 0.5, down: float = 0.5, volume: int = _VOLUME) -> PriceBar:
    return PriceBar(high=close + up, low=close - down, close=close, volume=volume, open=close)


def _squeeze_series() -> list[PriceBar]:
    """Wide swings for most of the history, then 20 quiet sessions above the long average."""
    n = MIN_HISTORY + 20
    wide = [_bar(100 + 10 * math.sin(i / 3)) for i in range(n - 20)]
    quiet = [_bar(115 + 0.2 * math.sin(i)) for i in range(20)]
    return wide + quiet


def _oversold_series() -> list[PriceBar]:
    flat = [_bar(100 + 0.3 * math.sin(i)) for i in range(MIN_HISTORY + 10)]
    drop = [_bar(100 - 2.2 * (i + 1)) for i in range(10)]  # about -22% in 10 sessions
    return flat + drop


def _divergence_series() -> list[PriceBar]:
    """Price drifts to its 20-day low while every bar closes near its high: the A/D line climbs."""
    flat = [_bar(100 + 0.3 * math.sin(i)) for i in range(MIN_HISTORY)]
    drift = [_bar(100 - 0.25 * (i + 1), up=0.1, down=2.0) for i in range(20)]
    return flat + drift


def test_ad_line_credits_a_close_near_the_high_even_on_a_down_day() -> None:
    bars = [
        PriceBar(high=10, low=8, close=9.8, volume=100),  # near the high: +80
        PriceBar(high=10, low=8, close=8.2, volume=100),  # near the low: -80
        PriceBar(high=9, low=9, close=9, volume=100),  # no range: adds nothing
    ]
    assert accumulation_distribution(bars).values == pytest.approx(
        (
            100 * (1.8 - 0.2) / 2,
            0.0,
            0.0,
        )
    )


def test_squeeze_records_the_upper_band_and_the_close_five_sessions_ago() -> None:
    bars = _squeeze_series()
    setups = evaluate_setups("2330", _AS_OF, bars, regime=Regime.NEUTRAL)
    squeeze = [s for s in setups if s.strategy is Strategy.VOLATILITY_SQUEEZE]
    assert len(squeeze) == 1
    assert squeeze[0].upper_band is not None and squeeze[0].upper_band > bars[-1].close
    assert squeeze[0].close_5d_ago == bars[-6].close


def test_squeeze_is_not_listed_when_breadth_is_risk_off() -> None:
    setups = evaluate_setups("2330", _AS_OF, _squeeze_series(), regime=Regime.RISK_OFF)
    assert Strategy.VOLATILITY_SQUEEZE not in {s.strategy for s in setups}


def test_an_illiquid_stock_gets_no_setup() -> None:
    thin = [_bar(b.close, volume=10_000) for b in _squeeze_series()]
    assert evaluate_setups("1234", _AS_OF, thin, regime=Regime.NEUTRAL) == []


def test_oversold_bounce_needs_a_steep_drop_and_rsi_under_30() -> None:
    setups = evaluate_setups("2317", _AS_OF, _oversold_series(), regime=Regime.NEUTRAL)
    oversold = [s for s in setups if s.strategy is Strategy.OVERSOLD_BOUNCE]
    assert len(oversold) == 1
    assert oversold[0].drop_10d_pct is not None and oversold[0].drop_10d_pct <= -15
    assert oversold[0].rsi14 is not None and oversold[0].rsi14 < 30


def test_ad_divergence_lists_a_stock_near_its_low_with_a_rising_ad_line() -> None:
    bars = _divergence_series()
    setups = evaluate_setups("2454", _AS_OF, bars, regime=Regime.NEUTRAL)
    divergence = [s for s in setups if s.strategy is Strategy.AD_DIVERGENCE]
    assert len(divergence) == 1
    setup = divergence[0]
    assert setup.low_20d == min(b.low for b in bars[-20:])
    assert setup.ad_value is not None and setup.ad_low_20d is not None
    assert setup.ad_value > setup.ad_low_20d
    assert setup.rsi_at_low is not None


def test_a_flat_series_matches_no_strategy() -> None:
    flat = [_bar(100.0) for _ in range(MIN_HISTORY + 20)]
    assert evaluate_setups("1101", _AS_OF, flat, regime=Regime.NEUTRAL) == []


def test_regime_thresholds() -> None:
    assert regime_from_breadth(65) is Regime.RISK_ON
    assert regime_from_breadth(45) is Regime.NEUTRAL
    assert regime_from_breadth(20) is Regime.RISK_OFF


def test_exclusions_cover_disposition_attention_and_upcoming_ex_dividend() -> None:
    reasons = context.excluded_symbols(
        run_date=_RUN_DATE,
        as_of=_AS_OF,
        ex_dividend_until=date_(2024, 7, 4),
        attention_rows=[
            {"symbol": "1111", "flagged_date": "2024-06-28"},
            {"symbol": "1112", "flagged_date": "2024-06-20"},  # an older flag: not excluded
        ],
        disposition_rows=[
            {"symbol": "2222", "period_start": "2024-06-28", "period_end": "2024-07-05"},
            {"symbol": "2223", "period_start": "2024-06-10", "period_end": "2024-06-20"},
        ],
        dividend_rows=[
            {"symbol": "3333", "cash_ex_dividend_date": "2024-07-03"},
            {"symbol": "3334", "cash_ex_dividend_date": "2024-07-20"},  # beyond the horizon
        ],
    )
    assert reasons == {"1111": "attention", "2222": "disposition", "3333": "ex_dividend"}


def test_chip_streaks_and_margin_change() -> None:
    flow = [
        {"investor": "Foreign_Investor", "trade_date": f"2024-06-{d}", "buy": 10, "sell": 1}
        for d in (26, 27, 28)
    ] + [{"investor": "Investment_Trust", "trade_date": "2024-06-28", "buy": 0, "sell": 5}]
    assert context.chip_streaks(flow) == (3, -1)

    margin = [
        {"trade_date": f"2024-06-{d:02d}", "margin_purchase_today_balance": 1000 + 20 * i}
        for i, d in enumerate(range(20, 29))
    ]
    change = context.margin_change_pct(margin)
    assert change is not None and round(change, 2) == round((1160 - 1060) / 1060 * 100, 2)


def test_headline_is_the_newest_announcement_since_the_last_session() -> None:
    rows = [
        {"symbol": "2330", "announced_date": "2024-06-27", "subject": "old"},
        {
            "symbol": "2330",
            "announced_date": "2024-06-28",
            "announced_time": "17:01",
            "subject": "a",
        },
        {
            "symbol": "2330",
            "announced_date": "2024-06-28",
            "announced_time": "18:30",
            "subject": "b",
        },
    ]
    assert context.latest_headlines(rows, since=_AS_OF) == {"2330": "b"}


def test_watchlist_rows_drop_excluded_stocks_and_carry_context() -> None:
    rows, regime, _ = build_watchlist_rows(
        run_date=_RUN_DATE,
        as_of=_AS_OF,
        bars={"2330": _squeeze_series(), "2317": _oversold_series()},
        industry_by_symbol={"2330": "半導體業", "2317": "其他電子業"},
        flow_rows=[],
        margin_rows=[],
        exclusions={"2317": "ex_dividend"},
        headlines={"2330": "法說會"},
    )
    assert {r["symbol"] for r in rows} == {"2330"}
    row = rows[0]
    assert row["strategy"] == Strategy.VOLATILITY_SQUEEZE.value
    assert row["industry"] == "半導體業"
    assert row["headline"] == "法說會"
    assert row["regime"] == regime.value


def _ctx(root: Path) -> IngestContext:
    return IngestContext(
        finmind=None,  # type: ignore[arg-type]  # this job makes no FinMind call
        store=ParquetStore(root=root),
        leader=AlwaysLeader(),
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
    )


def _write_history(store: ParquetStore, calendar: TradingDayCalendar, last: date_) -> None:
    series = {"2330": _squeeze_series(), "2317": _oversold_series()}
    length = len(next(iter(series.values())))
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
        ]
        store.write_partition(PRICE_EOD_DATASET, "TW", day, rows)


async def test_the_job_writes_todays_watchlist_from_the_last_session(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, _AS_OF)

    run = await job_strategy_watchlist(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    rows = ctx.store.read_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE)
    assert {(r["symbol"], r["strategy"]) for r in rows} >= {
        ("2317", Strategy.OVERSOLD_BOUNCE.value),
    }
    assert all(r["as_of"] == _AS_OF.isoformat() for r in rows)


async def test_the_job_refuses_to_publish_on_stale_prices(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, date_(2024, 6, 14))  # two weeks before the run

    run = await job_strategy_watchlist(_RUN_DATE, ctx)

    assert run.status is RunStatus.SKIPPED
    assert "stale" in run.detail
    assert not ctx.store.has_partition(STRATEGY_WATCHLIST_DATASET, "TW", _RUN_DATE)
