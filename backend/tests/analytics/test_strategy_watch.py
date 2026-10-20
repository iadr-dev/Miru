"""策略盯盤 08:00 screen: each strategy setup on hand-built series, the context/exclusion layer,
and the job end to end on a real ParquetStore (no network)."""

from __future__ import annotations

import math
import threading
from calendar.trading_days import TradingDayCalendar
from datetime import date as date_
from pathlib import Path

import pytest

from analytics.indicators import PriceBar, accumulation_distribution
from analytics.strategy_watch import batch as batch_module
from analytics.strategy_watch import context
from analytics.strategy_watch.batch import (
    PRICE_EOD_DATASET,
    STRATEGY_WATCHLIST_DATASET,
    build_watchlist_rows,
    job_strategy_watchlist,
)
from analytics.strategy_watch.strategies import (
    MIN_HISTORY,
    ChipInputs,
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
    assert squeeze[0].upper_band is not None
    assert squeeze[0].upper_band > bars[-1].close
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
    assert oversold[0].drop_10d_pct is not None
    assert oversold[0].drop_10d_pct <= -15
    assert oversold[0].rsi14 is not None
    assert oversold[0].rsi14 < 30


def test_ad_divergence_lists_a_stock_near_its_low_with_a_rising_ad_line() -> None:
    bars = _divergence_series()
    setups = evaluate_setups("2454", _AS_OF, bars, regime=Regime.NEUTRAL)
    divergence = [s for s in setups if s.strategy is Strategy.AD_DIVERGENCE]
    assert len(divergence) == 1
    setup = divergence[0]
    assert setup.low_20d == min(b.low for b in bars[-20:])
    assert setup.ad_value is not None
    assert setup.ad_low_20d is not None
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
    assert change is not None
    assert round(change, 2) == round((1160 - 1060) / 1060 * 100, 2)


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
        name_by_symbol={"2330": "台積電"},
        flow_rows=[],
        margin_rows=[],
        exclusions={"2317": "ex_dividend"},
        headlines={"2330": "法說會"},
    )
    assert {r["symbol"] for r in rows} == {"2330"}
    row = rows[0]
    assert row["strategy"] == Strategy.VOLATILITY_SQUEEZE.value
    assert row["industry"] == "半導體業"
    assert row["name"] == "台積電"
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


def _range_series() -> list[PriceBar]:
    """Long history, then 20 quiet sessions inside 30.0-32.0 (about a 6.7% range), closing at 31."""
    history = [_bar(30 + 0.3 * math.sin(i)) for i in range(MIN_HISTORY)]
    tight = [_bar(31 + 0.5 * math.sin(i), up=0.4, down=0.4) for i in range(20)]
    return history + tight


def _chips(**overrides: object) -> ChipInputs:
    values: dict[str, object] = {
        "foreign_net_20d": 300_000.0,
        "trust_net_20d": 10_790.0,
        "foreign_streak": 4,
        "trust_streak": 1,
        "large_holder_pct": 51.7,
        "large_holder_pct_before": 45.7,
        "holders": 581_955,
        "holders_before": 600_000,
        "margin_change_20d_pct": -6.0,
        **overrides,
    }
    return ChipInputs.model_validate(values)


def _accumulation(bars: list[PriceBar], chips: ChipInputs) -> list[object]:
    setups = evaluate_setups("2409", _AS_OF, bars, regime=Regime.NEUTRAL, chips=chips)
    return [s for s in setups if s.strategy is Strategy.CHIP_ACCUMULATION]


def test_chip_accumulation_lists_a_quiet_range_with_every_chip_sign_aligned() -> None:
    (setup,) = _accumulation(_range_series(), _chips())
    assert setup.range_high_20d is not None
    assert setup.range_low_20d is not None
    assert setup.range_high_20d > setup.close > setup.range_low_20d
    assert setup.large_holder_change_4w == pytest.approx(6.0)
    assert setup.holders_change_4w_pct is not None
    assert setup.holders_change_4w_pct < 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"foreign_streak": 1, "trust_streak": 1},  # one-day buying is noise
        {"foreign_net_20d": -50_000.0, "trust_net_20d": 10_000.0},  # net selling over the window
        {"large_holder_pct": 44.0},  # 大戶 share fell
        {"holders": 610_000},  # more holders: shares spreading out, not gathering
        {"margin_change_20d_pct": 3.0},  # retail margin money coming in
        {"holders_before": None},  # no weekly history to compare
    ],
)
def test_chip_accumulation_needs_every_chip_sign(overrides: dict[str, object]) -> None:
    assert _accumulation(_range_series(), _chips(**overrides)) == []


def test_chip_accumulation_skips_a_wide_range_or_a_breakout() -> None:
    wide = _range_series()[:-20] + [_bar(30 + i * 0.3) for i in range(20)]  # 30 -> 35.7, +19%
    assert _accumulation(wide, _chips()) == []
    broken = [*_range_series()[:-1], _bar(32.5, up=0.1, down=0.1)]  # closes above the range high
    assert _accumulation(broken, _chips()) == []


def test_chip_inputs_read_finmind_holder_rows() -> None:
    def week(percent: float, people: int) -> list[dict[str, object]]:
        return [
            {"holding_shares_level": "more than 1,000,001", "percent": percent, "people": 367},
            {"holding_shares_level": "total", "percent": 100.0, "people": people},
        ]

    flow = [
        {"investor": "Foreign_Investor", "trade_date": f"2026-09-{d:02d}", "buy": 20, "sell": 5}
        for d in range(1, 25)
    ]
    chips = context.chip_inputs(
        flow, [], [week(45.7, 600_000), week(47.0, 595_000), week(51.7, 581_955)]
    )
    assert chips.large_holder_pct == 51.7
    assert chips.large_holder_pct_before == 45.7
    assert chips.holders == 581_955
    assert chips.holders_before == 600_000
    assert chips.foreign_net_20d == 20 * 15  # the last 20 sessions only
    assert chips.foreign_streak == 24


def test_a_stock_that_stays_listed_keeps_counting_days() -> None:
    rows, _, _ = build_watchlist_rows(
        run_date=_RUN_DATE,
        as_of=_AS_OF,
        bars={"2317": _oversold_series()},
        industry_by_symbol={},
        name_by_symbol={},
        flow_rows=[],
        margin_rows=[],
        exclusions={},
        headlines={},
        previous={("2317", Strategy.OVERSOLD_BOUNCE.value): (date_(2024, 6, 26), 3)},
    )
    (row,) = rows
    assert row["first_listed"] == "2024-06-26"
    assert row["days_on_list"] == 4


def test_an_etf_gets_no_chip_accumulation_setup() -> None:
    holders = [[{"symbol": "00982A", "holding_shares_level": "total", "people": p}] for p in (9, 8)]
    rows, _, _ = build_watchlist_rows(
        run_date=_RUN_DATE,
        as_of=_AS_OF,
        bars={"00982A": _range_series()},
        industry_by_symbol={},
        name_by_symbol={},
        flow_rows=[],
        margin_rows=[],
        exclusions={},
        headlines={},
        holder_snapshots=holders,
    )
    assert all(r["strategy"] != Strategy.CHIP_ACCUMULATION.value for r in rows)


def _flag_series(
    *, pause_width: float = 0.8, pause_volume: int = 1_200_000, run_to: float = 60.0
) -> list[PriceBar]:
    """A long rising base, a 20-session run from 48 to `run_to`, then a 5-session pause."""
    base = [_bar(40 + i * 0.05) for i in range(MIN_HISTORY - 5)]
    run = [_bar(48 + (run_to - 48) * (i + 1) / 20, volume=3_000_000) for i in range(20)]
    pause = [
        _bar(run_to - 0.5 + pause_width * math.sin(i) / 2, up=0.3, down=0.3, volume=pause_volume)
        for i in range(5)
    ]
    return base + run + pause


def _flag(bars: list[PriceBar], chips: ChipInputs | None = None) -> list[object]:
    setups = evaluate_setups(
        "3017", _AS_OF, bars, regime=Regime.NEUTRAL, chips=chips or _chips(margin_change_20d_pct=25)
    )
    return [s for s in setups if s.strategy is Strategy.MOMENTUM_FLAG]


def test_momentum_flag_lists_a_tight_pause_after_a_run_on_drying_volume() -> None:
    (setup,) = _flag(_flag_series())
    assert setup.run_gain_pct is not None
    assert setup.run_gain_pct >= 15
    assert setup.flag_high is not None
    assert setup.flag_low is not None
    assert setup.pause_volume_ratio is not None
    assert setup.pause_volume_ratio < 1
    # A rising margin balance is reported, not a reason to drop the stock.
    assert setup.margin_change_20d_pct == 25


def test_momentum_flag_rejects_loose_pauses_heavy_volume_and_weak_runs() -> None:
    assert _flag(_flag_series(pause_width=12.0)) == []  # a 14%-ish swing is a shakeout, not a flag
    assert _flag(_flag_series(pause_volume=5_000_000)) == []  # volume did not dry up
    assert _flag(_flag_series(run_to=52.0)) == []  # +8% is not a strong run
    assert _flag(_flag_series(), _chips(large_holder_pct=40.0)) == []  # 大戶 share fell


async def test_the_job_runs_its_blocking_pass_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _ctx(tmp_path)
    _write_history(ctx.store, ctx.calendar, _AS_OF)
    seen: list[int] = []
    original = batch_module.calendar_for

    def spy(inner_ctx: IngestContext) -> TradingDayCalendar:
        seen.append(threading.get_ident())
        return original(inner_ctx)

    monkeypatch.setattr(batch_module, "calendar_for", spy)

    run = await job_strategy_watchlist(_RUN_DATE, ctx)

    assert run.status is RunStatus.SUCCESS
    assert seen
    assert threading.get_ident() not in seen
