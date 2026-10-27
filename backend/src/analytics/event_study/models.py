"""Plain value types for the event study: events, the close-price panel, per-event outcomes and
the per-horizon summaries. Frozen dataclasses (no I/O, no pydantic) so the statistics module
stays a set of pure functions over numpy arrays."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_
from enum import StrEnum
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

type FloatArray = npt.NDArray[np.float64]

# Forward windows reported by default: one trading week, one month, one quarter.
DEFAULT_HORIZONS: Final[tuple[int, ...]] = (5, 20, 60)
# The pre-event context window: trading days before day 0.
PRE_EVENT_DAYS: Final[int] = 5
# A horizon (or a year inside it) with fewer events than this reports counts only, never statistics.
MIN_SAMPLE_SIZE: Final[int] = 30
# The market proxy needs at least this many stocks priced at both ends of a window.
MIN_PROXY_BREADTH: Final[int] = 100
# Plain equities carry codes of at most this many characters (warrants and ETFs are longer).
PROXY_MAX_SYMBOL_LENGTH: Final[int] = 4

PROXY_ID: Final[str] = "median_all_tw_stocks"


class EventType(StrEnum):
    DISPOSITION_START = "disposition_start"
    DISPOSITION_END = "disposition_end"
    ATTENTION_LISTED = "attention_listed"
    CAPITAL_REDUCTION_RESUME = "capital_reduction_resume"
    BUYBACK_START = "buyback_start"
    BUYBACK_END = "buyback_end"


@dataclass(frozen=True, slots=True)
class StudyEvent:
    """One dated event for one symbol. `event_date` is the calendar date the definition names;
    day 0 is the first trading day on or after it (resolved against the price calendar)."""

    symbol: str
    name: str | None
    event_date: date_


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Events extracted from stored rows plus what was dropped on the way, so the output can say
    how many candidates never reached the price step."""

    events: tuple[StudyEvent, ...]
    rows_seen: int = 0
    excluded_non_equity: int = 0
    excluded_extended: int = 0  # a release that was followed straight away by a new period
    excluded_unparsed: int = 0  # a matching filing whose date range could not be read
    excluded_outside_filter: int = 0  # event date outside the caller's date range


@dataclass(frozen=True, slots=True)
class ClosePanel:
    """Daily closes (and volumes) for the stored TW price window: `close[t, s]` is the close of
    `symbols[s]` on `dates[t]`, NaN when that symbol has no row that day. `dates` are the days the
    store actually holds prices for (the trading calendar every offset is counted on)."""

    dates: tuple[date_, ...]
    symbols: tuple[str, ...]
    close: FloatArray
    volume: FloatArray

    @property
    def latest_date(self) -> date_ | None:
        return self.dates[-1] if self.dates else None


Reason = Literal["censored", "missing_price"]
Status = Literal[
    "measured", "not_yet_effective", "outside_price_window", "missing_price", "low_volume"
]


@dataclass(frozen=True, slots=True)
class EventOutcome:
    """What one event did. `day0` is None when the event never reached a priced day 0.
    `returns`/`excess` map horizon -> fraction (0.05 = +5%); a horizon that could not be measured
    is absent from both and listed in `skipped` with the reason."""

    event: StudyEvent
    status: Status
    day0: date_ | None = None
    pre_event: float | None = None
    pre_event_excess: float | None = None
    returns: dict[int, float] = field(default_factory=dict[int, float])
    excess: dict[int, float] = field(default_factory=dict[int, float])
    skipped: dict[int, Reason] = field(default_factory=dict[int, Reason])


@dataclass(frozen=True, slots=True)
class SampleSummary:
    """Statistics of one return sample. All return figures are fractions."""

    mean: float
    median: float
    stdev: float
    p25: float
    p75: float
    share_positive: float


@dataclass(frozen=True, slots=True)
class HorizonSummary:
    """One horizon for one event type. `stats` is None exactly when `insufficient_sample` is
    true: the counts are still reported, no statistic is."""

    horizon_days: int
    n_events: int
    n_symbols: int
    n_event_days: int  # distinct day-0 dates: events sharing a day share the market's move
    max_events_on_one_day: int
    n_censored: int  # horizon ran past the latest stored price
    n_missing_price: int  # no close at day 0 + horizon
    insufficient_sample: bool
    stats: SampleSummary | None = None
    excess_mean: float | None = None
    excess_median: float | None = None
    n_with_proxy: int = 0


@dataclass(frozen=True, slots=True)
class YearSummary:
    horizon_days: int
    year: int
    n_events: int
    insufficient_sample: bool
    mean: float | None = None
    median: float | None = None
    excess_mean: float | None = None
    excess_median: float | None = None


@dataclass(frozen=True, slots=True)
class OutcomeCounts:
    candidates: int
    measured: int
    not_yet_effective: int
    outside_price_window: int
    missing_price: int
    low_volume: int


@dataclass(frozen=True, slots=True)
class StudyResult:
    outcomes: tuple[EventOutcome, ...]
    horizons: tuple[HorizonSummary, ...]
    pre_event: HorizonSummary
    by_year: tuple[YearSummary, ...]
    counts: OutcomeCounts
    latest_price_date: date_ | None
    window_start: date_ | None
    window_end: date_ | None
