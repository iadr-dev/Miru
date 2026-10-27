"""The event-study statistics over synthetic price panels with known returns."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from analytics.event_study import (
    MIN_SAMPLE_SIZE,
    PRE_EVENT_DAYS,
    ClosePanel,
    StudyEvent,
    run_event_study,
    summarize_sample,
)
from analytics.event_study.models import MIN_PROXY_BREADTH

_START = date(2024, 1, 1)  # a Monday: the synthetic calendar skips weekends


def _calendar(n_days: int, *, skip: frozenset[int] = frozenset()) -> tuple[date, ...]:
    """`n_days` weekdays from `_START`; `skip` drops those weekday indexes (a holiday gap)."""
    days: list[date] = []
    cursor = _START
    index = 0
    while len(days) < n_days:
        if cursor.weekday() < 5:
            if index not in skip:
                days.append(cursor)
            index += 1
        cursor += timedelta(days=1)
    return tuple(days)


def _panel(
    dates: tuple[date, ...],
    series: dict[str, list[float]],
    *,
    market_growth: float = 0.0,
    n_market: int = MIN_PROXY_BREADTH + 5,
    volume: float = 1_000.0,
) -> ClosePanel:
    """The named series plus `n_market` filler stocks that all grow `market_growth` per day, so
    the market proxy (median stock) has a known return of (1 + g) ** days - 1."""
    symbols = list(series)
    columns = [np.array(series[s], dtype=np.float64) for s in symbols]
    for i in range(n_market):
        symbols.append(f"{9000 + i}")
        columns.append(100.0 * (1.0 + market_growth) ** np.arange(len(dates)))
    close = np.column_stack(columns)
    return ClosePanel(
        dates=dates,
        symbols=tuple(symbols),
        close=close,
        volume=np.full(close.shape, volume, dtype=np.float64),
    )


def _flat_then(n: int, jump_at: int, jump: float, base: float = 100.0) -> list[float]:
    return [base if i < jump_at else base * (1.0 + jump) for i in range(n)]


def test_forward_return_is_close_n_over_close_zero_minus_one() -> None:
    dates = _calendar(40)
    closes = [100.0 + i for i in range(40)]  # day i closes at 100 + i
    panel = _panel(dates, {"2330": closes})
    event = StudyEvent("2330", None, dates[10])

    result = run_event_study([event], panel, horizons=(5, 20), min_sample=1)

    outcome = result.outcomes[0]
    assert outcome.day0 == dates[10]
    assert outcome.returns[5] == pytest.approx(115.0 / 110.0 - 1.0)
    assert outcome.returns[20] == pytest.approx(130.0 / 110.0 - 1.0)


def test_nothing_before_day_zero_enters_a_forward_return() -> None:
    dates = _calendar(40)
    base = [100.0] * 40
    spiked = [100.0] * 40
    spiked[9] = 500.0  # the day before day 0 spikes; day 0 and later are flat
    event_date = dates[10]

    flat = run_event_study(
        [StudyEvent("1101", None, event_date)],
        _panel(dates, {"1101": base}),
        horizons=(5,),
        min_sample=1,
    )
    with_spike = run_event_study(
        [StudyEvent("1101", None, event_date)],
        _panel(dates, {"1101": spiked}),
        horizons=(5,),
        min_sample=1,
    )

    assert flat.outcomes[0].returns[5] == 0.0
    assert with_spike.outcomes[0].returns[5] == 0.0


def test_pre_event_drift_uses_the_five_trading_days_before_day_zero() -> None:
    dates = _calendar(30)
    closes = [100.0] * 30
    closes[10 - PRE_EVENT_DAYS] = 80.0
    panel = _panel(dates, {"1101": closes})

    result = run_event_study(
        [StudyEvent("1101", None, dates[10])], panel, horizons=(5,), min_sample=1
    )

    assert result.outcomes[0].pre_event == pytest.approx(100.0 / 80.0 - 1.0)


def test_an_event_on_a_weekend_uses_the_next_trading_day_as_day_zero() -> None:
    dates = _calendar(30)
    saturday = next(d + timedelta(days=1) for d in dates if d.weekday() == 4 and d > dates[8])
    closes = [100.0 + i for i in range(30)]
    panel = _panel(dates, {"1101": closes})

    result = run_event_study(
        [StudyEvent("1101", None, saturday)], panel, horizons=(5,), min_sample=1
    )

    monday = dates[dates.index(saturday - timedelta(days=1)) + 1]
    assert result.outcomes[0].day0 == monday


def test_horizons_count_trading_days_across_a_calendar_gap() -> None:
    # day 12 is a holiday that the calendar does not have: +5 trading days from day 10 lands on
    # the sixth weekday after it, not on the fifth.
    dates = _calendar(40, skip=frozenset({12}))
    closes = {d: 100.0 + i for i, d in enumerate(dates)}
    panel = _panel(dates, {"1101": [closes[d] for d in dates]})
    day0 = dates[10]

    result = run_event_study([StudyEvent("1101", None, day0)], panel, horizons=(5,), min_sample=1)

    assert result.outcomes[0].returns[5] == pytest.approx(closes[dates[15]] / closes[dates[10]] - 1)
    assert (dates[15] - dates[10]).days == 8  # 5 trading days spanned a weekend and the holiday


def test_a_horizon_past_the_latest_price_is_censored_for_that_horizon_only() -> None:
    dates = _calendar(30)
    panel = _panel(dates, {"1101": [100.0 + i for i in range(30)]})
    event = StudyEvent("1101", None, dates[22])  # 7 trading days remain

    result = run_event_study([event], panel, horizons=(5, 20, 60), min_sample=1)

    outcome = result.outcomes[0]
    assert outcome.status == "measured"
    assert 5 in outcome.returns
    assert outcome.skipped == {20: "censored", 60: "censored"}
    by_horizon = {h.horizon_days: h for h in result.horizons}
    assert (by_horizon[5].n_events, by_horizon[5].n_censored) == (1, 0)
    assert (by_horizon[20].n_events, by_horizon[20].n_censored) == (0, 1)
    assert (by_horizon[60].n_events, by_horizon[60].n_censored) == (0, 1)


def test_events_without_prices_are_excluded_and_counted() -> None:
    dates = _calendar(30)
    halted = [100.0] * 30
    halted[10] = float("nan")  # no close on day 0
    gap_later = [100.0] * 30
    gap_later[15] = float("nan")  # no close on day 0 + 5
    panel = _panel(dates, {"1101": halted, "1102": gap_later, "1103": [100.0] * 30})
    events = [
        StudyEvent("1101", None, dates[10]),
        StudyEvent("1102", None, dates[10]),
        StudyEvent("1103", None, dates[10]),
        StudyEvent("9999", None, dates[10]),  # a symbol the panel has never seen
    ]

    result = run_event_study(events, panel, horizons=(5,), min_sample=1)

    assert result.counts.candidates == 4
    assert result.counts.missing_price == 2  # 1101 and the unknown symbol: no day-0 close
    assert result.counts.measured == 2
    horizon = result.horizons[0]
    assert (horizon.n_events, horizon.n_missing_price) == (1, 1)  # 1102 lacks the day-5 close


def test_events_outside_the_price_window_are_counted_not_measured() -> None:
    dates = _calendar(30)
    panel = _panel(dates, {"1101": [100.0] * 30})
    events = [
        StudyEvent("1101", None, dates[0] - timedelta(days=30)),
        StudyEvent("1101", None, dates[-1] + timedelta(days=3)),
    ]

    result = run_event_study(events, panel, horizons=(5,), min_sample=1)

    assert result.counts.outside_price_window == 1
    assert result.counts.not_yet_effective == 1
    assert result.counts.measured == 0


def test_a_sample_below_the_minimum_reports_counts_and_no_statistics() -> None:
    dates = _calendar(30)
    panel = _panel(dates, {f"{1100 + i}": [100.0 + i] * 30 for i in range(5)})
    events = [StudyEvent(f"{1100 + i}", None, dates[10]) for i in range(5)]

    result = run_event_study(events, panel, horizons=(5,))  # default minimum: 30

    horizon = result.horizons[0]
    assert horizon.n_events == 5
    assert horizon.insufficient_sample is True
    assert horizon.stats is None
    assert horizon.excess_mean is None
    assert horizon.excess_median is None
    assert result.pre_event.insufficient_sample is True
    assert MIN_SAMPLE_SIZE == 30


def test_a_sample_at_the_minimum_reports_every_statistic() -> None:
    dates = _calendar(30)
    # 30 stocks, stock k gains k percentage points over the window
    symbols = {f"{1100 + k}": [100.0] * 11 + [100.0 + k] * 19 for k in range(30)}
    panel = _panel(dates, symbols)
    events = [StudyEvent(s, None, dates[10]) for s in symbols]

    result = run_event_study(events, panel, horizons=(5,))

    horizon = result.horizons[0]
    assert horizon.insufficient_sample is False
    assert horizon.n_events == 30
    assert horizon.n_symbols == 30
    stats = horizon.stats
    assert stats is not None
    assert stats.mean == pytest.approx(0.145)  # mean of 0..29 percent
    assert stats.median == pytest.approx(0.145)
    assert stats.p25 == pytest.approx(0.0725)
    assert stats.p75 == pytest.approx(0.2175)
    assert stats.stdev == pytest.approx(float(np.std(np.arange(30) / 100.0, ddof=1)))
    assert stats.share_positive == pytest.approx(29 / 30)  # stock 0 is flat, not positive


def test_excess_return_subtracts_the_median_stock_return_of_the_same_window() -> None:
    dates = _calendar(30)
    growth = 0.01  # the filler stocks, hence the market proxy, gain 1% a day
    symbols = {f"{1100 + k}": [100.0] * 30 for k in range(30)}  # the event stocks stay flat
    panel = _panel(dates, symbols, market_growth=growth)
    events = [StudyEvent(s, None, dates[10]) for s in symbols]

    result = run_event_study(events, panel, horizons=(5,))

    market_5d = (1.0 + growth) ** 5 - 1.0
    horizon = result.horizons[0]
    assert horizon.stats is not None
    assert horizon.stats.mean == pytest.approx(0.0)
    assert horizon.excess_mean == pytest.approx(-market_5d)
    assert horizon.excess_median == pytest.approx(-market_5d)
    assert horizon.n_with_proxy == 30
    assert result.outcomes[0].excess[5] == pytest.approx(-market_5d)


def test_without_enough_priced_stocks_there_is_no_excess_return() -> None:
    dates = _calendar(30)
    symbols = {f"{1100 + k}": [100.0] * 30 for k in range(30)}
    # 30 event stocks + 50 fillers = 80 priced stocks: below the breadth floor
    thin = _panel(dates, symbols, n_market=MIN_PROXY_BREADTH - 50)
    events = [StudyEvent(s, None, dates[10]) for s in symbols]

    result = run_event_study(events, thin, horizons=(5,), min_sample=1)

    assert result.horizons[0].stats is not None
    assert result.horizons[0].excess_mean is None
    assert result.outcomes[0].excess == {}


def test_by_year_groups_by_day_zero_year_and_applies_the_minimum_per_year() -> None:
    dates = _calendar(520)  # two calendar years of weekdays
    year_one = dates[10].year
    year_two = dates[-1].year
    assert year_one != year_two
    symbols = {f"{1100 + k}": [100.0 + k] * 520 for k in range(35)}
    panel = _panel(dates, symbols)
    late = next(d for d in dates if d.year == year_two)
    events = [StudyEvent(s, None, dates[10]) for s in symbols] + [
        StudyEvent(s, None, late) for s in list(symbols)[:3]
    ]

    result = run_event_study(events, panel, horizons=(5,))

    rows = {row.year: row for row in result.by_year}
    assert rows[year_one].n_events == 35
    assert rows[year_one].insufficient_sample is False
    assert rows[year_one].mean == pytest.approx(0.0)
    assert rows[year_two].n_events == 3
    assert rows[year_two].insufficient_sample is True
    assert rows[year_two].mean is None


def test_clustering_is_visible_in_the_distinct_event_day_counts() -> None:
    dates = _calendar(40)
    symbols = {f"{1100 + k}": [100.0] * 40 for k in range(40)}
    panel = _panel(dates, symbols)
    # 40 events but on only 2 distinct days: 25 and 15
    events = [StudyEvent(s, None, dates[10]) for s in list(symbols)[:25]] + [
        StudyEvent(s, None, dates[12]) for s in list(symbols)[25:]
    ]

    horizon = run_event_study(events, panel, horizons=(5,), min_sample=1).horizons[0]

    assert horizon.n_events == 40
    assert horizon.n_event_days == 2
    assert horizon.max_events_on_one_day == 25


def test_min_volume_drops_thin_day_zero_events_and_counts_them() -> None:
    dates = _calendar(30)
    panel = _panel(dates, {"1101": [100.0] * 30, "1102": [100.0] * 30}, volume=500.0)
    vol = panel.volume.copy()
    vol[10, panel.symbols.index("1102")] = 5_000.0
    panel = ClosePanel(panel.dates, panel.symbols, panel.close, vol)
    events = [StudyEvent("1101", None, dates[10]), StudyEvent("1102", None, dates[10])]

    result = run_event_study(events, panel, horizons=(5,), min_sample=1, min_volume=1_000)

    assert result.counts.low_volume == 1
    assert result.counts.measured == 1
    assert result.horizons[0].n_symbols == 1


def test_an_empty_panel_measures_nothing() -> None:
    empty = ClosePanel((), (), np.empty((0, 0)), np.empty((0, 0)))

    result = run_event_study(
        [StudyEvent("1101", None, date(2024, 1, 2))], empty, horizons=(5,), min_sample=1
    )

    assert result.counts.outside_price_window == 1
    assert result.latest_price_date is None
    assert result.horizons[0].n_events == 0


def test_summarize_sample_matches_numpy() -> None:
    values = [-0.10, -0.02, 0.0, 0.03, 0.05, 0.20]

    summary = summarize_sample(values)

    arr = np.array(values)
    assert summary.mean == pytest.approx(arr.mean())
    assert summary.median == pytest.approx(np.median(arr))
    assert summary.stdev == pytest.approx(arr.std(ddof=1))
    assert (summary.p25, summary.p75) == pytest.approx(tuple(np.percentile(arr, [25, 75])))
    assert summary.share_positive == pytest.approx(3 / 6)
