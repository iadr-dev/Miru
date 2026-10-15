"""Pure technical-indicator functions over an OHLCV time series.

No I/O, no LLM, no randomness — every function is a deterministic transform of a sequence of
end-of-day bars into a typed result. Callers (a future `get_indicators` tool, the score
factors below) read the derived store themselves and pass the resulting bars in; this module
never touches the store, never the network.

**Scope** (moving averages, RSI, MACD, Bollinger Bands, ATR, OBV, a same-day VWAP proxy, and the
stochastic **KD** oscillator) covers the indicator set that is fully computable from `price_eod`
alone. Deliberately **not** built here, and why:

- **The candlestick pattern library** lives in its own module (`analytics/patterns.py`) rather
  than here: patterns are boolean shape-recognizers over a short trailing window, a different kind
  of thing from these series-returning numeric transforms, and sizeable enough to warrant their own
  file. Listed here as the deliberate split, not a silent omission.
- **Structure signals (量能, 支撐/壓力, N 波回檔)** — these build on top of the base indicators
  above (e.g. 支撐/壓力 needs swing-pivot detection over the OHLC series) and are themselves a
  distinct, sizeable feature; left for a follow-up pass once the base library has shipped and
  been used by the score.

All series-returning indicators are **left-padded with `None`** for the initial warm-up window
where there isn't enough history yet — never a fabricated number standing in for "not
computable yet". Bars must be supplied in ascending `trade_date` order; callers are responsible
for that ordering — this module never re-sorts, so a caller bug that hands in an unsorted or
misaligned series produces visibly wrong output rather than a silently "corrected" one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from analytics.models import (
    AccumulationDistributionResult,
    AtrResult,
    BollingerResult,
    EmaResult,
    KdResult,
    MacdResult,
    MovingAverageBundle,
    ObvResult,
    RsiResult,
    SmaResult,
    VwapResult,
)


@dataclass(frozen=True)
class PriceBar:
    """The minimal OHLCV shape every indicator needs — deliberately narrower than
    `schemas.capabilities.EODBar` (no symbol/market) so this module has zero dependency on the
    schema of a specific capability; a caller maps its `EODBar` rows into this before calling."""

    high: float
    low: float
    close: float
    volume: int
    open: float | None = None
    turnover: float | None = None


def bars_from_close(closes: Sequence[float]) -> tuple[PriceBar, ...]:
    """Test/adapter convenience: build bars from a bare close-price series, using the close as
    a stand-in for high/low/open where a caller only has closes (e.g. hand-checking a known SMA
    fixture) — volume defaults to 0, so OBV/VWAP over such bars is not meaningful."""
    return tuple(PriceBar(high=c, low=c, close=c, volume=0) for c in closes)


def _closes(bars: Sequence[PriceBar]) -> list[float]:
    return [b.close for b in bars]


_WINDOW_MUST_BE_POSITIVE = "window must be >= 1"
_PERIOD_MUST_BE_POSITIVE = "period must be >= 1"


def sma(bars: Sequence[PriceBar], window: int) -> SmaResult:
    """Simple moving average over `close`, warm-up-padded with `None`."""
    if window < 1:
        raise ValueError(_WINDOW_MUST_BE_POSITIVE)
    closes = _closes(bars)
    values: list[float | None] = []
    running_sum = 0.0
    for i, price in enumerate(closes):
        running_sum += price
        if i >= window:
            running_sum -= closes[i - window]
        values.append(running_sum / window if i >= window - 1 else None)
    return SmaResult(window=window, values=tuple(values))


def ema(bars: Sequence[PriceBar], window: int) -> EmaResult:
    """Exponential moving average, seeded at bar `window - 1` with the SMA of the first
    `window` closes (the conventional seeding — avoids biasing an EMA-of-EMA chain like MACD's
    signal line toward the very first price)."""
    if window < 1:
        raise ValueError(_WINDOW_MUST_BE_POSITIVE)
    closes = _closes(bars)
    values: list[float | None] = [None] * len(closes)
    if len(closes) < window:
        return EmaResult(window=window, values=tuple(values))
    alpha = 2.0 / (window + 1.0)
    seed = sum(closes[:window]) / window
    values[window - 1] = seed
    prior = seed
    for i in range(window, len(closes)):
        prior = alpha * closes[i] + (1 - alpha) * prior
        values[i] = prior
    return EmaResult(window=window, values=tuple(values))


def moving_average_bundle(bars: Sequence[PriceBar]) -> MovingAverageBundle:
    """MA5/10/20/60/120/240 together — 均線排列, read as a stack rather than one line at a time."""
    return MovingAverageBundle(
        ma5=sma(bars, 5),
        ma10=sma(bars, 10),
        ma20=sma(bars, 20),
        ma60=sma(bars, 60),
        ma120=sma(bars, 120),
        ma240=sma(bars, 240),
    )


def rsi(bars: Sequence[PriceBar], period: int = 14) -> RsiResult:
    """Wilder-smoothed RSI. `values[i]` undefined (`None`) until `period` prior changes exist
    (i.e. first defined value is at index `period`)."""
    if period < 1:
        raise ValueError(_PERIOD_MUST_BE_POSITIVE)
    closes = _closes(bars)
    n = len(closes)
    values: list[float | None] = [None] * n
    if n <= period:
        return RsiResult(period=period, values=tuple(values))

    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    values[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        values[i + 1] = _rsi_from_averages(avg_gain, avg_loss)

    return RsiResult(period=period, values=tuple(values))


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss <= 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def kd(
    bars: Sequence[PriceBar],
    period: int = 9,
    k_smoothing: int = 3,
    d_smoothing: int = 3,
) -> KdResult:
    """Stochastic oscillator (KD), TW-standard 9/3/3.

    For each bar `i` with a full trailing `period`-day window, the raw stochastic value is
    `RSV[i] = (close[i] - lowest_low) / (highest_high - lowest_low) * 100` over that window.
    `%K` is a recursive smoothing of RSV and `%D` the same smoothing of `%K`:
    `K[i] = K[i-1]*(k_smoothing-1)/k_smoothing + RSV[i]/k_smoothing` (the 9/3/3 form uses the 1/3
    weight `k_smoothing == 3` gives). Both are seeded at **50** — the neutral mid-scale value that
    is the standard TW-charting seed — for the step before the first defined RSV, so K and D become
    defined on the same bar RSV first is (index `period - 1`), with no extra smoothing warm-up.

    A degenerate flat window (highest_high == lowest_low, no movement over the window) leaves RSV
    genuinely undefined; it takes the neutral 50 rather than a fabricated directional 0 or 100, the
    same neutral value the smoothing is seeded with."""
    if period < 1:
        raise ValueError(_PERIOD_MUST_BE_POSITIVE)
    if k_smoothing < 1 or d_smoothing < 1:
        raise ValueError("smoothing factors must be >= 1")
    n = len(bars)
    k_values: list[float | None] = [None] * n
    d_values: list[float | None] = [None] * n
    if n < period:
        return KdResult(period=period, k=tuple(k_values), d=tuple(d_values))

    prev_k = 50.0
    prev_d = 50.0
    for i in range(period - 1, n):
        window = bars[i - period + 1 : i + 1]
        highest_high = max(bar.high for bar in window)
        lowest_low = min(bar.low for bar in window)
        span = highest_high - lowest_low
        rsv = 50.0 if span == 0 else (bars[i].close - lowest_low) / span * 100.0
        k = prev_k * (k_smoothing - 1) / k_smoothing + rsv / k_smoothing
        d = prev_d * (d_smoothing - 1) / d_smoothing + k / d_smoothing
        k_values[i] = k
        d_values[i] = d
        prev_k = k
        prev_d = d
    return KdResult(period=period, k=tuple(k_values), d=tuple(d_values))


def macd(
    bars: Sequence[PriceBar],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> MacdResult:
    """12/26/9 MACD: `dif` = EMA(fast) - EMA(slow); `dem` = EMA(dif, signal); `histogram` =
    `dif - dem`. Each is `None` until both underlying EMAs are defined at that index."""
    fast_ema = ema(bars, fast).values
    slow_ema = ema(bars, slow).values
    n = len(bars)
    dif: list[float | None] = [None] * n
    for i in range(n):
        f, s = fast_ema[i], slow_ema[i]
        if f is not None and s is not None:
            dif[i] = f - s

    dif_bars = bars_from_close([d for d in dif if d is not None])
    dif_signal_raw = ema(dif_bars, signal).values
    # Re-align the signal-line EMA (computed over only the defined `dif` values) back onto the
    # full-length index space, since `dif` itself has a `None` prefix.
    dem: list[float | None] = [None] * n
    signal_iter = iter(dif_signal_raw)
    for i in range(n):
        if dif[i] is not None:
            dem[i] = next(signal_iter, None)

    histogram: list[float | None] = [
        None if (d is None or m is None) else d - m for d, m in zip(dif, dem, strict=True)
    ]
    return MacdResult(dif=tuple(dif), dem=tuple(dem), histogram=tuple(histogram))


def bollinger(bars: Sequence[PriceBar], window: int = 20, num_std: float = 2.0) -> BollingerResult:
    """20-period / 2-std-dev Bollinger Bands (population std-dev over the window)."""
    if window < 1:
        raise ValueError(_WINDOW_MUST_BE_POSITIVE)
    closes = _closes(bars)
    n = len(closes)
    mid: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(window - 1, n):
        window_slice = closes[i - window + 1 : i + 1]
        mean = sum(window_slice) / window
        variance = sum((x - mean) ** 2 for x in window_slice) / window
        std = variance**0.5
        mid[i] = mean
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return BollingerResult(window=window, mid=tuple(mid), upper=tuple(upper), lower=tuple(lower))


def atr(bars: Sequence[PriceBar], period: int = 14) -> AtrResult:
    """Wilder average true range. Needs `high`/`low`/`close`; the first true-range term
    (bar 0) has no prior close, so it falls back to that bar's own high-low range."""
    if period < 1:
        raise ValueError(_PERIOD_MUST_BE_POSITIVE)
    n = len(bars)
    values: list[float | None] = [None] * n
    if n == 0:
        return AtrResult(period=period, values=tuple(values))

    true_ranges: list[float] = []
    prev_close: float | None = None
    for bar in bars:
        high_low = bar.high - bar.low
        if prev_close is None:
            true_ranges.append(high_low)
        else:
            true_ranges.append(max(high_low, abs(bar.high - prev_close), abs(bar.low - prev_close)))
        prev_close = bar.close

    if n < period:
        return AtrResult(period=period, values=tuple(values))

    seed = sum(true_ranges[:period]) / period
    values[period - 1] = seed
    prior = seed
    for i in range(period, n):
        prior = (prior * (period - 1) + true_ranges[i]) / period
        values[i] = prior
    return AtrResult(period=period, values=tuple(values))


def obv(bars: Sequence[PriceBar]) -> ObvResult:
    """On-balance volume: cumulative volume signed by the day's close-to-close direction.
    Defined from bar 0 (0.0) — no warm-up window, unlike the other indicators here."""
    values: list[float] = []
    running = 0.0
    prev_close: float | None = None
    for bar in bars:
        if prev_close is not None:
            if bar.close > prev_close:
                running += bar.volume
            elif bar.close < prev_close:
                running -= bar.volume
        values.append(running)
        prev_close = bar.close
    return ObvResult(values=tuple(values))


def accumulation_distribution(bars: Sequence[PriceBar]) -> AccumulationDistributionResult:
    """Chaikin A/D line: each bar adds `volume * ((close - low) - (high - close)) / (high - low)`,
    the share of the day's range the close sits in, weighted by volume. Unlike OBV it credits a
    close near the high even on a down day, which is what lets it diverge from price. A bar with
    no range (high == low) adds nothing rather than dividing by zero."""
    values: list[float] = []
    running = 0.0
    for bar in bars:
        span = bar.high - bar.low
        if span > 0:
            running += bar.volume * ((bar.close - bar.low) - (bar.high - bar.close)) / span
        values.append(running)
    return AccumulationDistributionResult(values=tuple(values))


def vwap(bars: Sequence[PriceBar]) -> VwapResult:
    """A same-day, per-bar `turnover / volume` proxy — **not** the textbook intraday
    cumulative VWAP (this EOD-only store has no intraday trade prints to accumulate over).
    `turnover / volume` is the exact same-day average traded price when `turnover` is present;
    absent that, the bar falls back to the typical price `(high+low+close)/3` as a reasonable
    same-day proxy. `values[i] is None` only when `volume == 0` (no trades that day — nothing
    to average, never fabricated as a plain `close` echo)."""
    values: list[float | None] = []
    for bar in bars:
        if bar.volume <= 0:
            values.append(None)
        elif bar.turnover is not None:
            values.append(bar.turnover / bar.volume)
        else:
            values.append((bar.high + bar.low + bar.close) / 3.0)
    return VwapResult(values=tuple(values))


__all__ = [
    "PriceBar",
    "accumulation_distribution",
    "atr",
    "bars_from_close",
    "bollinger",
    "ema",
    "kd",
    "macd",
    "moving_average_bundle",
    "obv",
    "rsi",
    "sma",
    "vwap",
]
