"""Event-study statistics over a close-price panel: pure functions, no I/O.

Day 0 is the first stored trading day on or after the event date. A forward return for horizon N
is `close(day N) / close(day 0) - 1`, counting N trading days on the stored calendar, so nothing
before day 0's close enters a forward figure. The pre-event drift is `close(day 0) /
close(day -5) - 1`, shown for context only. Excess return subtracts the same window's return of
the market proxy: the median return across every plain-equity stock priced at both ends of the
window, so it follows the typical stock rather than the largest ones.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_
from math import isfinite

import numpy as np

from analytics.event_study.models import (
    MIN_PROXY_BREADTH,
    MIN_SAMPLE_SIZE,
    PRE_EVENT_DAYS,
    PROXY_MAX_SYMBOL_LENGTH,
    ClosePanel,
    EventOutcome,
    FloatArray,
    HorizonSummary,
    OutcomeCounts,
    Reason,
    SampleSummary,
    Status,
    StudyEvent,
    StudyResult,
    YearSummary,
)


class _Proxy:
    """The median return of plain-equity stocks between two panel rows, memoised per window."""

    def __init__(self, panel: ClosePanel) -> None:
        self._close = panel.close
        self._stocks = np.array(
            [len(s) <= PROXY_MAX_SYMBOL_LENGTH for s in panel.symbols], dtype=np.bool_
        )
        self._cache: dict[tuple[int, int], float | None] = {}

    def median_return(self, start: int, end: int) -> float | None:
        key = (start, end)
        if key not in self._cache:
            self._cache[key] = self._compute(start, end)
        return self._cache[key]

    def _compute(self, start: int, end: int) -> float | None:
        first = self._close[start, self._stocks]
        last = self._close[end, self._stocks]
        valid = np.isfinite(first) & np.isfinite(last) & (first > 0)
        if int(valid.sum()) < MIN_PROXY_BREADTH:
            return None
        return float(np.median(last[valid] / first[valid] - 1.0))


def _price(close: FloatArray, row: int, col: int) -> float | None:
    value = float(close[row, col])
    return value if isfinite(value) and value > 0 else None


def _window_return(
    close: FloatArray, col: int, start: int, end: int, proxy: _Proxy
) -> tuple[float | None, float | None]:
    """(stock return, stock return minus proxy) between two panel rows; None where unpriced."""
    first = _price(close, start, col)
    last = _price(close, end, col)
    if first is None or last is None:
        return None, None
    ret = last / first - 1.0
    market = proxy.median_return(start, end)
    return ret, (ret - market if market is not None else None)


def _unmeasured(event: StudyEvent, status: Status, day0: date_ | None = None) -> EventOutcome:
    return EventOutcome(event=event, status=status, day0=day0)


@dataclass(frozen=True, slots=True)
class _Run:
    """What every event of one study is measured against."""

    panel: ClosePanel
    col_of: dict[str, int]
    proxy: _Proxy
    horizons: Sequence[int]
    min_volume: int | None


def _forward_windows(
    run: _Run, col: int, row0: int
) -> tuple[dict[int, float], dict[int, float], dict[int, Reason]]:
    """(returns, excess returns, why a horizon was skipped) for one event's day-0 row."""
    returns: dict[int, float] = {}
    excess: dict[int, float] = {}
    skipped: dict[int, Reason] = {}
    for horizon in run.horizons:
        row_n = row0 + horizon
        if row_n >= len(run.panel.dates):
            skipped[horizon] = "censored"
            continue
        ret, over = _window_return(run.panel.close, col, row0, row_n, run.proxy)
        if ret is None:
            skipped[horizon] = "missing_price"
            continue
        returns[horizon] = ret
        if over is not None:
            excess[horizon] = over
    return returns, excess, skipped


def _measure_event(event: StudyEvent, run: _Run) -> EventOutcome:
    dates = run.panel.dates
    if not dates or event.event_date < dates[0]:
        return _unmeasured(event, "outside_price_window")
    row0 = bisect_left(dates, event.event_date)
    if row0 >= len(dates):
        return _unmeasured(event, "not_yet_effective")
    col = run.col_of.get(event.symbol)
    if col is None or _price(run.panel.close, row0, col) is None:
        return _unmeasured(event, "missing_price", dates[row0])
    if run.min_volume is not None:
        volume = float(run.panel.volume[row0, col])
        if not (isfinite(volume) and volume >= run.min_volume):
            return _unmeasured(event, "low_volume", dates[row0])

    returns, excess, skipped = _forward_windows(run, col, row0)
    pre, pre_excess = (None, None)
    if row0 - PRE_EVENT_DAYS >= 0:
        pre, pre_excess = _window_return(
            run.panel.close, col, row0 - PRE_EVENT_DAYS, row0, run.proxy
        )
    return EventOutcome(
        event=event,
        status="measured",
        day0=dates[row0],
        pre_event=pre,
        pre_event_excess=pre_excess,
        returns=returns,
        excess=excess,
        skipped=skipped,
    )


def summarize_sample(values: Sequence[float]) -> SampleSummary:
    arr = np.asarray(values, dtype=np.float64)
    p25, p75 = (float(q) for q in np.percentile(arr, [25, 75]))
    return SampleSummary(
        mean=float(arr.mean()),
        median=float(np.median(arr)),
        stdev=float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        p25=p25,
        p75=p75,
        share_positive=float((arr > 0).mean()),
    )


def _mean_median(values: Sequence[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    arr = np.asarray(values, dtype=np.float64)
    return float(arr.mean()), float(np.median(arr))


type _Obs = tuple[EventOutcome, float, float | None]


def _horizon_summary(
    horizon: int,
    observations: Sequence[_Obs],
    *,
    censored: int,
    missing: int,
    min_sample: int,
) -> HorizonSummary:
    days = Counter(o.day0 for o, _, _ in observations)
    n_events = len(observations)
    insufficient = n_events < min_sample
    excess = [] if insufficient else [e for _, _, e in observations if e is not None]
    excess_mean, excess_median = _mean_median(excess)
    return HorizonSummary(
        horizon_days=horizon,
        n_events=n_events,
        n_symbols=len({o.event.symbol for o, _, _ in observations}),
        n_event_days=len(days),
        max_events_on_one_day=max(days.values(), default=0),
        n_censored=censored,
        n_missing_price=missing,
        insufficient_sample=insufficient,
        stats=None if insufficient else summarize_sample([r for _, r, _ in observations]),
        excess_mean=excess_mean,
        excess_median=excess_median,
        n_with_proxy=len(excess),
    )


def _forward_observations(outcomes: Sequence[EventOutcome], horizon: int) -> list[_Obs]:
    return [
        (o, o.returns[horizon], o.excess.get(horizon)) for o in outcomes if horizon in o.returns
    ]


def _year_summaries(
    outcomes: Sequence[EventOutcome], horizon: int, min_sample: int
) -> list[YearSummary]:
    by_year: dict[int, list[_Obs]] = {}
    for obs in _forward_observations(outcomes, horizon):
        if obs[0].day0 is not None:
            by_year.setdefault(obs[0].day0.year, []).append(obs)
    rows: list[YearSummary] = []
    for year in sorted(by_year):
        observations = by_year[year]
        if len(observations) < min_sample:
            rows.append(YearSummary(horizon, year, len(observations), insufficient_sample=True))
            continue
        mean, median = _mean_median([r for _, r, _ in observations])
        excess_mean, excess_median = _mean_median([e for _, _, e in observations if e is not None])
        rows.append(
            YearSummary(
                horizon,
                year,
                len(observations),
                insufficient_sample=False,
                mean=mean,
                median=median,
                excess_mean=excess_mean,
                excess_median=excess_median,
            )
        )
    return rows


def _counts(outcomes: Sequence[EventOutcome]) -> OutcomeCounts:
    tally = Counter(o.status for o in outcomes)
    return OutcomeCounts(
        candidates=len(outcomes),
        measured=tally["measured"],
        not_yet_effective=tally["not_yet_effective"],
        outside_price_window=tally["outside_price_window"],
        missing_price=tally["missing_price"],
        low_volume=tally["low_volume"],
    )


def run_event_study(
    events: Sequence[StudyEvent],
    panel: ClosePanel,
    *,
    horizons: Sequence[int],
    min_sample: int = MIN_SAMPLE_SIZE,
    min_volume: int | None = None,
) -> StudyResult:
    """Measure every event against the panel and summarise per horizon, per year and the
    pre-event window. An event is dropped from a horizon only when that horizon cannot be
    measured for it (past the latest stored price, or no close on that day); it stays in the
    others."""
    col_of = {symbol: i for i, symbol in enumerate(panel.symbols)}
    proxy = _Proxy(panel)
    run = _Run(panel, col_of, proxy, horizons, min_volume)
    outcomes = tuple(_measure_event(e, run) for e in events)
    measured = [o for o in outcomes if o.status == "measured"]

    summaries: list[HorizonSummary] = []
    by_year: list[YearSummary] = []
    for horizon in horizons:
        censored = sum(1 for o in measured if o.skipped.get(horizon) == "censored")
        missing = sum(1 for o in measured if o.skipped.get(horizon) == "missing_price")
        summaries.append(
            _horizon_summary(
                horizon,
                _forward_observations(measured, horizon),
                censored=censored,
                missing=missing,
                min_sample=min_sample,
            )
        )
        by_year.extend(_year_summaries(measured, horizon, min_sample))

    pre_obs: list[_Obs] = [
        (o, o.pre_event, o.pre_event_excess) for o in measured if o.pre_event is not None
    ]
    pre = _horizon_summary(
        -PRE_EVENT_DAYS,
        pre_obs,
        censored=0,
        missing=len(measured) - len(pre_obs),
        min_sample=min_sample,
    )
    return StudyResult(
        outcomes=outcomes,
        horizons=tuple(summaries),
        pre_event=pre,
        by_year=tuple(by_year),
        counts=_counts(outcomes),
        latest_price_date=panel.latest_date,
        window_start=panel.dates[0] if panel.dates else None,
        window_end=panel.latest_date,
    )
