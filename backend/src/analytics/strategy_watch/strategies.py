"""The three strategy setups, evaluated on one symbol's end-of-day history.

A setup decides whether a stock goes on today's watchlist and records the levels the intraday check
compares against (the upper band to clear, the 20-day low to break, the close five sessions ago).
Pure functions over `PriceBar`s: no I/O, deterministic, so each threshold is unit-tested on its own.

Thresholds are the module constants below, chosen to match common practice rather than tuned to a
backtest: Bollinger 20/2, RSI 14, KD 9/3/3, a 120-session bandwidth history, a 1,000-lot liquidity
floor.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date as date_
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from analytics.indicators import PriceBar, accumulation_distribution, bollinger, kd, rsi, sma


class Strategy(StrEnum):
    VOLATILITY_SQUEEZE = "volatility_squeeze"
    OVERSOLD_BOUNCE = "oversold_bounce"
    AD_DIVERGENCE = "ad_divergence"


class Regime(StrEnum):
    """Market breadth on the last session: the share of the universe closing above its MA20."""

    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


BOLLINGER_WINDOW = 20
BOLLINGER_STD = 2.0
BANDWIDTH_HISTORY = 120
SQUEEZE_PERCENTILE = 0.05  # today's bandwidth is in the narrowest 5% of the last 120 sessions
TREND_MA = 60
MOMENTUM_LOOKBACK = 5
OVERSOLD_DROP_DAYS = 10
OVERSOLD_DROP_PCT = -15.0
OVERSOLD_RSI = 30.0
KD_OVERSOLD = 20.0
LOW_WINDOW = 20
NEAR_LOW_PCT = 3.0  # close within 3% of the 20-day low
LIQUIDITY_WINDOW = 20
MIN_AVG_VOLUME_SHARES = 1_000_000  # 1,000 張
BREADTH_RISK_ON_PCT = 60.0
BREADTH_RISK_OFF_PCT = 30.0
# Enough sessions for a 120-value bandwidth history behind a 20-session band.
MIN_HISTORY = BANDWIDTH_HISTORY + BOLLINGER_WINDOW


class Setup(BaseModel):
    """One stock on one strategy's watchlist, with the levels its intraday check reads. A level a
    strategy does not use stays `None`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    strategy: Strategy
    as_of: date_
    close: float
    avg_volume_20d: float
    ma20: float | None = None
    ma60: float | None = None
    rsi14: float | None = None
    k: float | None = None
    upper_band: float | None = None
    lower_band: float | None = None
    bandwidth_pct: float | None = None
    close_5d_ago: float | None = None
    drop_10d_pct: float | None = None
    low_20d: float | None = None
    ad_value: float | None = None
    ad_low_20d: float | None = None
    rsi_at_low: float | None = None


def regime_from_breadth(pct_above_ma20: float) -> Regime:
    if pct_above_ma20 >= BREADTH_RISK_ON_PCT:
        return Regime.RISK_ON
    if pct_above_ma20 < BREADTH_RISK_OFF_PCT:
        return Regime.RISK_OFF
    return Regime.NEUTRAL


def closes_above_ma20(bars: Sequence[PriceBar]) -> bool | None:
    """Whether the last close is above its 20-session average; `None` with too little history."""
    last_ma = sma(bars, BOLLINGER_WINDOW).values[-1] if bars else None
    return None if last_ma is None else bars[-1].close > last_ma


def evaluate_setups(
    symbol: str, as_of: date_, bars: Sequence[PriceBar], *, regime: Regime
) -> list[Setup]:
    """Every strategy whose setup holds on the last bar. `bars` ascend by date and end at `as_of`.

    A breakout setup (the squeeze) is skipped when breadth is risk-off: in a falling market most
    range breaks fail, which is the main reason practitioners filter breakouts by market regime."""
    if len(bars) < MIN_HISTORY:
        return []
    avg_volume = sum(b.volume for b in bars[-LIQUIDITY_WINDOW:]) / LIQUIDITY_WINDOW
    if avg_volume < MIN_AVG_VOLUME_SHARES:
        return []

    close = bars[-1].close
    bands = bollinger(bars, BOLLINGER_WINDOW, BOLLINGER_STD)
    rsi_values = rsi(bars, 14).values
    k_values = kd(bars).k
    ma60 = sma(bars, TREND_MA).values[-1]
    # Shared fields; each strategy copies this with its own levels (`model_copy` keeps one source).
    base = Setup(
        symbol=symbol,
        strategy=Strategy.VOLATILITY_SQUEEZE,
        as_of=as_of,
        close=close,
        avg_volume_20d=avg_volume,
        ma20=sma(bars, BOLLINGER_WINDOW).values[-1],
        ma60=ma60,
        rsi14=rsi_values[-1],
        k=k_values[-1],
    )
    candidates: list[tuple[Strategy, dict[str, float] | None]] = [
        (Strategy.OVERSOLD_BOUNCE, _oversold(bars, bands.lower, rsi_values[-1])),
        (Strategy.AD_DIVERGENCE, _ad_divergence(bars, rsi_values)),
    ]
    if regime is not Regime.RISK_OFF:
        squeeze = _squeeze(bars, bands.upper, bands.lower, bands.mid, ma60)
        candidates.insert(0, (Strategy.VOLATILITY_SQUEEZE, squeeze))
    setups = [
        base.model_copy(update={"strategy": strategy, **levels})
        for strategy, levels in candidates
        if levels is not None
    ]
    return setups


def _bandwidth(upper: float | None, lower: float | None, mid: float | None) -> float | None:
    if upper is None or lower is None or mid is None or mid <= 0:
        return None
    return (upper - lower) / mid * 100.0


def _squeeze(
    bars: Sequence[PriceBar],
    upper: Sequence[float | None],
    lower: Sequence[float | None],
    mid: Sequence[float | None],
    ma60: float | None,
) -> dict[str, float] | None:
    """Bandwidth in the narrowest decile of its own history, in an uptrend (close above MA60), and
    still inside the band: a close already above the upper band has broken out, not coiled."""
    widths = [_bandwidth(u, lo, m) for u, lo, m in zip(upper, lower, mid, strict=True)]
    history = [w for w in widths[-BANDWIDTH_HISTORY:] if w is not None]
    today = widths[-1]
    if today is None or len(history) < BANDWIDTH_HISTORY or ma60 is None:
        return None
    if bars[-1].close <= ma60:
        return None
    rank = sum(1 for w in history if w < today) / len(history)
    if rank > SQUEEZE_PERCENTILE:
        return None
    upper_band = upper[-1]
    if upper_band is None or bars[-1].close >= upper_band:
        return None
    return {
        "upper_band": upper_band,
        "bandwidth_pct": today,
        "close_5d_ago": bars[-1 - MOMENTUM_LOOKBACK].close,
    }


def _oversold(
    bars: Sequence[PriceBar], lower: Sequence[float | None], last_rsi: float | None
) -> dict[str, float] | None:
    """A steep drop (10 sessions, at least 15%) or a close under the lower band, with RSI < 30."""
    if last_rsi is None or last_rsi >= OVERSOLD_RSI:
        return None
    then = bars[-1 - OVERSOLD_DROP_DAYS].close
    drop = (bars[-1].close - then) / then * 100.0 if then > 0 else 0.0
    lower_band = lower[-1]
    below_band = lower_band is not None and bars[-1].close < lower_band
    if drop > OVERSOLD_DROP_PCT and not below_band:
        return None
    result: dict[str, float] = {"drop_10d_pct": drop}
    if lower_band is not None:
        result["lower_band"] = lower_band
    return result


def _ad_divergence(
    bars: Sequence[PriceBar], rsi_values: Sequence[float | None]
) -> dict[str, float] | None:
    """Price close to its 20-day low and lower than 20 sessions ago, while the A/D line is higher
    than 20 sessions ago: closes keep landing in the upper part of each day's range, so selling is
    not following price down. Records the levels a new intraday low is checked against."""
    window = bars[-LOW_WINDOW:]
    low_20d = min(b.low for b in window)
    if low_20d <= 0 or (bars[-1].close - low_20d) / low_20d * 100.0 > NEAR_LOW_PCT:
        return None
    if bars[-1].close >= window[0].close:
        return None
    ad = accumulation_distribution(bars).values
    ad_window = ad[-LOW_WINDOW:]
    ad_low = min(ad_window)
    if ad[-1] <= ad_window[0] or ad[-1] <= ad_low:
        return None
    low_index = len(bars) - LOW_WINDOW + min(range(LOW_WINDOW), key=lambda i: window[i].low)
    rsi_at_low = rsi_values[low_index]
    if rsi_at_low is None:
        return None
    return {
        "low_20d": low_20d,
        "ad_value": ad[-1],
        "ad_low_20d": ad_low,
        "rsi_at_low": rsi_at_low,
    }


__all__ = [
    "MIN_HISTORY",
    "Regime",
    "Setup",
    "Strategy",
    "closes_above_ma20",
    "evaluate_setups",
    "regime_from_breadth",
]
