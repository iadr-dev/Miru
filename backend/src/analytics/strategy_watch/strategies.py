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
    CHIP_ACCUMULATION = "chip_accumulation"
    MOMENTUM_FLAG = "momentum_flag"


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
# 籌碼集中盤整 (chip accumulation during a consolidation): "price is the result, the chips are the
# cause" — the setup is institutions and large holders gathering while the price is still in its
# range, before a breakout.
RANGE_WINDOW = 20
MAX_RANGE_PCT = 12.0  # the 20-session high-low range, as % of its low
MAX_RUNUP_PCT = 10.0  # not already up this much over the range window (no chasing)
MIN_CHIP_STREAK = 3  # 外資 or 投信 net buying on at least 3 sessions in a row
HOLDER_WEEKS = 4  # 千張大戶 share and holder count compared with 4 weekly snapshots back
# 強勢整理 (momentum flag): a strong run, then a short tight pause on drying volume while the 籌碼
# keep concentrating — a continuation setup (bull flag), not a base.
FLAG_RUN_WINDOW = 20  # sessions before the pause the run is measured over
FLAG_MIN_RUN_PCT = 15.0
FLAG_MIN_PAUSE = 3
FLAG_MAX_PAUSE = 7
FLAG_MAX_WIDTH_PCT = 7.0
FLAG_MAX_RETRACE = 0.5  # the pause holds the upper half of the run
FLAG_VOLUME_LOOKBACK = 10  # pause volume is compared with the sessions just before it


class ChipInputs(BaseModel):
    """One stock's 籌碼 history, computed by the batch from the stored flow, margin and weekly
    shareholding-distribution rows. A missing input is `None` and fails the setup."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    foreign_net_20d: float | None = None
    trust_net_20d: float | None = None
    foreign_streak: int = 0
    trust_streak: int = 0
    large_holder_pct: float | None = None
    large_holder_pct_before: float | None = None
    holders: int | None = None
    holders_before: int | None = None
    margin_change_20d_pct: float | None = None


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
    range_high_20d: float | None = None
    range_low_20d: float | None = None
    range_width_pct: float | None = None
    change_20d_pct: float | None = None
    foreign_net_20d: float | None = None
    trust_net_20d: float | None = None
    large_holder_pct: float | None = None
    large_holder_change_4w: float | None = None
    holders_change_4w_pct: float | None = None
    margin_change_20d_pct: float | None = None
    flag_high: float | None = None
    flag_low: float | None = None
    flag_sessions: float | None = None
    run_gain_pct: float | None = None
    pause_volume_ratio: float | None = None
    # How long the stock has been on this strategy's list (a slow setup builds over weeks).
    first_listed: date_ | None = None
    days_on_list: int = 1


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
    symbol: str,
    as_of: date_,
    bars: Sequence[PriceBar],
    *,
    regime: Regime,
    chips: ChipInputs | None = None,
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
    if chips is not None:
        candidates.append((Strategy.CHIP_ACCUMULATION, _chip_accumulation(bars, chips)))
        flag = _momentum_flag(bars, chips, base.ma20, ma60)
        candidates.append((Strategy.MOMENTUM_FLAG, flag))
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


def _consolidation(bars: Sequence[PriceBar]) -> tuple[float, float, float, float] | None:
    """(high, low, width %, 20-session change %) when the last 20 sessions are a tight range the
    close has not broken out of and the stock has not already run up."""
    window = bars[-RANGE_WINDOW:]
    high = max(b.high for b in window)
    low = min(b.low for b in window)
    then = bars[-1 - RANGE_WINDOW].close
    if low <= 0 or then <= 0:
        return None
    width = (high - low) / low * 100.0
    change = (bars[-1].close - then) / then * 100.0
    # Broken out = the last close is above the range the earlier sessions set (its own bar's high
    # always contains its close, so it cannot be part of that comparison).
    prior_high = max(b.high for b in window[:-1])
    if width > MAX_RANGE_PCT or change >= MAX_RUNUP_PCT or bars[-1].close > prior_high:
        return None
    return high, low, width, change


def _chips_gathering(chips: ChipInputs) -> bool:
    """Every 籌碼 sign pointing the same way: institutions net buying over the window and on
    several sessions in a row (not one-day noise), the 千張大戶 share up and the shareholder count
    down over four weeks (shares moving from many hands to few), and the margin balance falling
    (short-term retail money leaving)."""
    institutions = (chips.foreign_net_20d or 0.0) + (chips.trust_net_20d or 0.0)
    holders_now, holders_then = chips.holders, chips.holders_before
    big_now, big_then = chips.large_holder_pct, chips.large_holder_pct_before
    return (
        chips.foreign_net_20d is not None
        and institutions > 0
        and max(chips.foreign_streak, chips.trust_streak) >= MIN_CHIP_STREAK
        and holders_now is not None
        and holders_then is not None
        and holders_now < holders_then
        and big_now is not None
        and big_then is not None
        and big_now > big_then
        and chips.margin_change_20d_pct is not None
        and chips.margin_change_20d_pct < 0
    )


def _chip_accumulation(bars: Sequence[PriceBar], chips: ChipInputs) -> dict[str, float] | None:
    """籌碼集中盤整: a consolidation not yet broken while the 籌碼 gather (both helpers above)."""
    consolidation = _consolidation(bars)
    if consolidation is None or not _chips_gathering(chips):
        return None
    high, low, width, change = consolidation
    # `_chips_gathering` proved these are set.
    big_now = chips.large_holder_pct or 0.0
    big_then = chips.large_holder_pct_before or 0.0
    holders_now, holders_then = chips.holders or 0, chips.holders_before or 1
    return {
        "range_high_20d": high,
        "range_low_20d": low,
        "range_width_pct": width,
        "change_20d_pct": change,
        "foreign_net_20d": chips.foreign_net_20d or 0.0,
        "trust_net_20d": chips.trust_net_20d or 0.0,
        "large_holder_pct": big_now,
        "large_holder_change_4w": big_now - big_then,
        "holders_change_4w_pct": (holders_now - holders_then) / holders_then * 100.0,
        "margin_change_20d_pct": chips.margin_change_20d_pct or 0.0,
    }


def _flag_pause(bars: Sequence[PriceBar]) -> tuple[int, float, float] | None:
    """The longest pause of 3-7 sessions at the end of `bars` inside a band at most 7% wide that the
    last close has not broken out of: (sessions, high, low)."""
    for length in range(FLAG_MAX_PAUSE, FLAG_MIN_PAUSE - 1, -1):
        pause = bars[-length:]
        high = max(b.high for b in pause)
        low = min(b.low for b in pause)
        earlier_high = max(b.high for b in pause[:-1])
        if low > 0 and (high - low) / low * 100.0 <= FLAG_MAX_WIDTH_PCT:
            if bars[-1].close > earlier_high:
                return None  # already breaking out of the pause
            return length, high, low
    return None


def _flag_shape(
    bars: Sequence[PriceBar], ma20: float | None, ma60: float | None
) -> dict[str, float] | None:
    """The price half of 強勢整理: a strong run (at least 15% off the low before the pause) in an
    aligned trend (close > MA20 > MA60), then a short tight pause that keeps the upper half of the
    run on lighter volume than the sessions before it."""
    pause = _flag_pause(bars)
    trend_ok = ma20 is not None and ma60 is not None and bars[-1].close > ma20 > ma60
    if pause is None or not trend_ok:
        return None
    length, high, low = pause
    before = bars[-length - FLAG_RUN_WINDOW : -length]
    run_low = min((b.low for b in before), default=0.0)
    if len(before) < FLAG_RUN_WINDOW or run_low <= 0:
        return None
    run_high = max(high, *(b.high for b in before))
    run_gain = (run_high - run_low) / run_low * 100.0
    retrace_floor = run_high - FLAG_MAX_RETRACE * (run_high - run_low)
    lead = before[-FLAG_VOLUME_LOOKBACK:]
    lead_volume = sum(b.volume for b in lead) / len(lead)
    pause_volume = sum(b.volume for b in bars[-length:]) / length
    shaped = (
        run_gain >= FLAG_MIN_RUN_PCT
        and low >= retrace_floor
        and lead_volume > 0
        and pause_volume < lead_volume
    )
    if not shaped:
        return None
    return {
        "flag_high": high,
        "flag_low": low,
        "flag_sessions": float(length),
        "run_gain_pct": run_gain,
        "pause_volume_ratio": pause_volume / lead_volume,
    }


def _momentum_flag(
    bars: Sequence[PriceBar], chips: ChipInputs, ma20: float | None, ma60: float | None
) -> dict[str, float] | None:
    """強勢整理: the flag shape while 大戶 share rises, holders fall and 外資 net buy."""
    shape = _flag_shape(bars, ma20, ma60)
    if shape is None or not _chips_concentrating(chips):
        return None
    big_now = chips.large_holder_pct or 0.0
    holders_then = chips.holders_before or 1
    return {
        **shape,
        "foreign_net_20d": chips.foreign_net_20d or 0.0,
        "trust_net_20d": chips.trust_net_20d or 0.0,
        "large_holder_pct": big_now,
        "large_holder_change_4w": big_now - (chips.large_holder_pct_before or 0.0),
        "holders_change_4w_pct": ((chips.holders or 0) - holders_then) / holders_then * 100.0,
        "margin_change_20d_pct": chips.margin_change_20d_pct or 0.0,
    }


def _chips_concentrating(chips: ChipInputs) -> bool:
    """The flag's 籌碼 test: 外資 net buying over 20 sessions, 大戶 share up and holders down over
    four weeks. Margin is not a condition here; a fast-rising balance is reported as a caution."""
    return (
        chips.foreign_net_20d is not None
        and chips.foreign_net_20d > 0
        and chips.holders is not None
        and chips.holders_before is not None
        and chips.holders < chips.holders_before
        and chips.large_holder_pct is not None
        and chips.large_holder_pct_before is not None
        and chips.large_holder_pct > chips.large_holder_pct_before
    )


__all__ = [
    "MIN_HISTORY",
    "ChipInputs",
    "Regime",
    "Setup",
    "Strategy",
    "closes_above_ma20",
    "evaluate_setups",
    "regime_from_breadth",
]
