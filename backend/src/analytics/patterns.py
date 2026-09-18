"""Candlestick pattern recognizers over an end-of-day OHLC series.

Each function answers a single yes/no: does this named pattern **complete as of the most recent
bar**? They are pure and deterministic — no I/O, no store, no network — taking an ascending-by-date
`PriceBar` sequence and reading only the trailing bars that pattern needs (most need 2-3, the
V-shape needs 5). Insufficient history, or a bar missing its `open`, is never an error: it returns
`False`, matching the graceful-`False` convention the indicator-event layer uses. Bars must be
supplied in ascending `trade_date` order; like the indicator library, this module never re-sorts.

**Scope — only the unambiguous, OHLC-rule-clear patterns are built here:** doji, bullish/bearish
engulfing, bullish/bearish continuation ("cannon", 多方炮/空方炮), V / inverse-V reversal, and
three-rising/three-falling ("soldiers"-family, 紅三兵/黑三兵). Each has a single agreed shape that
translates directly to an inequality over open/high/low/close.

**Deliberately NOT built, and why:** the poetic-named patterns from the original screenshot
inspiration — 一手遮天, 樂極生悲, 芝麻開花, 直上青雲, 彈劍長嘯, 噴薄而出, 一劍蒼生, 一葉知秋,
仙人指路, 末路紅三兵, 疊疊多方炮/空方炮 — have no clear, agreed-upon OHLC rule. Inventing a
threshold for one of them would be fabricating a definition, which this codebase does not do; they
are disclosed here as a known gap rather than shipped as guesses.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from analytics.indicators import PriceBar
from analytics.models import CandlestickPatternHit

# A doji's real body is negligible next to the day's range. 10% of the high-low span is a
# conservative cutoff for "open and close are essentially equal": below it the candle reads as
# indecision (a cross/plus shape), above it there is a directional body worth naming.
_DOJI_BODY_RATIO = 0.10

# For a cannon (多方炮/空方炮) the middle candle is a small "rest" bar between two strong ones. Its
# body must be under half the average body of the two outer candles — 50% is the point at which the
# middle stops reading as small relative to its neighbours and becomes a candle of comparable weight
# (which would make the three-bar shape a different, non-cannon sequence).
_CANNON_MIDDLE_RATIO = 0.50

# The V / inverse-V reversal is judged over a short trailing window: long enough to show a genuine
# down-then-up (or up-then-down) leg around a pivot, short enough to stay a local reversal rather
# than a multi-week swing. Five bars gives at least one bar on each side of an interior pivot.
_V_WINDOW = 5

_MIN_ENGULFING = 2
_MIN_CANNON = 3
_MIN_SOLDIERS = 3


def _body(bar: PriceBar) -> float | None:
    """The real-body size `|close - open|`, or `None` when the bar carries no `open` (so a
    body-dependent pattern degrades to `False` rather than guessing)."""
    if bar.open is None:
        return None
    return abs(bar.close - bar.open)


def _is_meaningful_body(bar: PriceBar) -> bool:
    """The bar has a real directional body — i.e. it is NOT a doji. Used by the soldiers-family
    patterns, which require three genuine bodies rather than three crosses."""
    return not is_doji((bar,))


def is_doji(bars: Sequence[PriceBar]) -> bool:
    """Single-bar: the real body is negligible relative to the day's total range (open ≈ close).
    A zero-range bar (high == low, typically a no-trade/limit day) is not treated as a doji — there
    is no range to judge the body against, so it is not asserted."""
    if not bars:
        return False
    bar = bars[-1]
    body = _body(bar)
    if body is None:
        return False
    span = bar.high - bar.low
    if span <= 0:
        return False
    return body <= _DOJI_BODY_RATIO * span


def is_bullish_engulfing(bars: Sequence[PriceBar]) -> bool:
    """Two-bar reversal: yesterday a down (red) candle, today an up (green) candle whose real body
    fully engulfs yesterday's — today opens at or below yesterday's close and closes at or above
    yesterday's open. Both bars must carry a real body."""
    if len(bars) < _MIN_ENGULFING:
        return False
    prev, today = bars[-2], bars[-1]
    if prev.open is None or today.open is None:
        return False
    prev_body = _body(prev)
    today_body = _body(today)
    if prev_body is None or today_body is None or prev_body <= 0 or today_body <= 0:
        return False
    prev_bearish = prev.close < prev.open
    today_bullish = today.close > today.open
    engulfs = today.open <= prev.close and today.close >= prev.open
    return prev_bearish and today_bullish and engulfs


def is_bearish_engulfing(bars: Sequence[PriceBar]) -> bool:
    """Two-bar reversal mirror: yesterday an up (green) candle, today a down (red) candle whose
    real body fully engulfs yesterday's — today opens at or above yesterday's close and closes at
    or below yesterday's open."""
    if len(bars) < _MIN_ENGULFING:
        return False
    prev, today = bars[-2], bars[-1]
    if prev.open is None or today.open is None:
        return False
    prev_body = _body(prev)
    today_body = _body(today)
    if prev_body is None or today_body is None or prev_body <= 0 or today_body <= 0:
        return False
    prev_bullish = prev.close > prev.open
    today_bearish = today.close < today.open
    engulfs = today.open >= prev.close and today.close <= prev.open
    return prev_bullish and today_bearish and engulfs


def is_bullish_cannon(bars: Sequence[PriceBar]) -> bool:
    """Three-bar bullish continuation (多方炮): two strong up candles bracketing a small-bodied
    middle candle, with the third closing above the first's close. The middle body must be under
    half the average of the two outer bodies (see `_CANNON_MIDDLE_RATIO`)."""
    if len(bars) < _MIN_CANNON:
        return False
    first, middle, third = bars[-3], bars[-2], bars[-1]
    first_body = _body(first)
    middle_body = _body(middle)
    third_body = _body(third)
    if first_body is None or middle_body is None or third_body is None:
        return False
    if first.open is None or third.open is None:
        return False
    outer_bullish = first.close > first.open and third.close > third.open
    if not outer_bullish or first_body <= 0 or third_body <= 0:
        return False
    small_middle = middle_body <= _CANNON_MIDDLE_RATIO * (first_body + third_body) / 2.0
    continues_up = third.close > first.close
    return small_middle and continues_up


def is_bearish_cannon(bars: Sequence[PriceBar]) -> bool:
    """Three-bar bearish continuation (空方炮): two strong down candles bracketing a small-bodied
    middle candle, with the third closing below the first's close."""
    if len(bars) < _MIN_CANNON:
        return False
    first, middle, third = bars[-3], bars[-2], bars[-1]
    first_body = _body(first)
    middle_body = _body(middle)
    third_body = _body(third)
    if first_body is None or middle_body is None or third_body is None:
        return False
    if first.open is None or third.open is None:
        return False
    outer_bearish = first.close < first.open and third.close < third.open
    if not outer_bearish or first_body <= 0 or third_body <= 0:
        return False
    small_middle = middle_body <= _CANNON_MIDDLE_RATIO * (first_body + third_body) / 2.0
    continues_down = third.close < first.close
    return small_middle and continues_down


def is_v_reversal(bars: Sequence[PriceBar]) -> bool:
    """A V-shaped reversal over the trailing window: an interior pivot low with lows strictly
    declining into it and strictly rising out of it — a clear down-leg then a clear up-leg around a
    single lowest point. `False` if the window is short, the lowest bar sits at an edge (no leg on
    one side), or either side is not strictly monotonic."""
    if len(bars) < _V_WINDOW:
        return False
    window = bars[-_V_WINDOW:]
    lows = [bar.low for bar in window]
    pivot = min(range(len(lows)), key=lambda idx: lows[idx])
    if pivot == 0 or pivot == len(lows) - 1:
        return False
    down_leg = all(lows[i] > lows[i + 1] for i in range(pivot))
    up_leg = all(lows[i] < lows[i + 1] for i in range(pivot, len(lows) - 1))
    return down_leg and up_leg


def is_inverse_v_reversal(bars: Sequence[PriceBar]) -> bool:
    """An inverse-V (Λ) reversal mirror: an interior pivot high with highs strictly rising into it
    and strictly declining out of it — a clear up-leg then a clear down-leg around a single highest
    point."""
    if len(bars) < _V_WINDOW:
        return False
    window = bars[-_V_WINDOW:]
    highs = [bar.high for bar in window]
    pivot = max(range(len(highs)), key=lambda idx: highs[idx])
    if pivot == 0 or pivot == len(highs) - 1:
        return False
    up_leg = all(highs[i] < highs[i + 1] for i in range(pivot))
    down_leg = all(highs[i] > highs[i + 1] for i in range(pivot, len(highs) - 1))
    return up_leg and down_leg


def is_three_rising(bars: Sequence[PriceBar]) -> bool:
    """Three-rising ("紅三兵"): three consecutive up candles, each with a real body (not a doji)
    and each closing higher than the last."""
    if len(bars) < _MIN_SOLDIERS:
        return False
    trio = bars[-_MIN_SOLDIERS:]
    if any(bar.open is None for bar in trio):
        return False
    bullish = all(bar.close > bar.open for bar in trio if bar.open is not None)
    bodies = all(_is_meaningful_body(bar) for bar in trio)
    rising = all(trio[i].close < trio[i + 1].close for i in range(len(trio) - 1))
    return bullish and bodies and rising


def is_three_falling(bars: Sequence[PriceBar]) -> bool:
    """Three-falling ("黑三兵"): three consecutive down candles, each with a real body (not a doji)
    and each closing lower than the last."""
    if len(bars) < _MIN_SOLDIERS:
        return False
    trio = bars[-_MIN_SOLDIERS:]
    if any(bar.open is None for bar in trio):
        return False
    bearish = all(bar.close < bar.open for bar in trio if bar.open is not None)
    bodies = all(_is_meaningful_body(bar) for bar in trio)
    falling = all(trio[i].close > trio[i + 1].close for i in range(len(trio) - 1))
    return bearish and bodies and falling


# `(recognizer, direction, min_window)` — `direction` is the plain descriptive label this
# pattern gets when it completes (+1 看多/bullish-leaning, -1 看空/bearish-leaning, 0 for the
# doji's genuine indecision read), `min_window` the trailing bar count the recognizer needs
# (mirrors each function's own length guard) so `scan_patterns` only calls a recognizer once
# enough history exists rather than relying on the recognizer's own early-`False` return.
_PatternRecognizer = Callable[[Sequence[PriceBar]], bool]
_PATTERN_TABLE: tuple[tuple[str, _PatternRecognizer, int, int], ...] = (
    ("doji", is_doji, 0, 1),  # min_window=1: is_doji reads only bars[-1]
    ("bullish_engulfing", is_bullish_engulfing, 1, _MIN_ENGULFING),
    ("bearish_engulfing", is_bearish_engulfing, -1, _MIN_ENGULFING),
    ("bullish_cannon", is_bullish_cannon, 1, _MIN_CANNON),
    ("bearish_cannon", is_bearish_cannon, -1, _MIN_CANNON),
    ("v_reversal", is_v_reversal, 1, _V_WINDOW),
    ("inverse_v_reversal", is_inverse_v_reversal, -1, _V_WINDOW),
    ("three_rising", is_three_rising, 1, _MIN_SOLDIERS),
    ("three_falling", is_three_falling, -1, _MIN_SOLDIERS),
)


def scan_patterns(bars: Sequence[PriceBar]) -> tuple[CandlestickPatternHit, ...]:
    """Runs every recognizer in this module against each bar index (as-of that bar, using only
    its trailing window) and returns every completed match, oldest first. A single bar can
    complete more than one pattern (e.g. a doji that is also the pivot of a V-reversal) — every
    match is reported, never deduplicated to "the" pattern for that bar."""
    hits: list[CandlestickPatternHit] = []
    for i in range(len(bars)):
        for name, recognizer, direction, min_window in _PATTERN_TABLE:
            if i + 1 < min_window:
                continue
            # Every recognizer only reads its own trailing `min_window` bars (each function's
            # own `bars[-k:]` slicing) — bounding the window passed in here keeps this scan
            # O(n), not O(n^2), for a multi-year daily series.
            window = bars[i + 1 - min_window : i + 1]
            if recognizer(window):
                hits.append(CandlestickPatternHit(bar_index=i, pattern=name, direction=direction))
    return tuple(hits)


__all__ = [
    "is_bearish_cannon",
    "is_bearish_engulfing",
    "is_bullish_cannon",
    "is_bullish_engulfing",
    "is_doji",
    "is_inverse_v_reversal",
    "is_three_falling",
    "is_three_rising",
    "is_v_reversal",
    "scan_patterns",
]
