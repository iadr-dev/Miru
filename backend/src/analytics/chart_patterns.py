"""Multi-bar chart-pattern detection over an end-of-day OHLC series.

Pure and deterministic, same contract as `patterns.py`/`structure.py`: no I/O, bars (and their
parallel `dates`) supplied in ascending order, never re-sorted. Where `patterns.py` names few-bar
candlestick shapes, this module names the multi-bar structures drawn on a chart: double top (M)
and double bottom (W), head-and-shoulders top and its inverse, ascending / descending /
symmetrical triangles, rising / falling wedges and the rectangle (box range).

**Descriptive, never advice.** A hit says "this geometry is present, in this state, with these
levels". It carries no price target, stop, bias or probability, and `family` is geometry only
(`reversal_top` / `reversal_bottom` / `consolidation`). The rules below are written once, here,
and every constant is named; nothing is tuned to fit a particular stock.

Rules
-----
*Pivots.* Built on the shared swing-pivot function `structure.support_resistance_levels`, called
with a wider span (`_CHART_PIVOT_SPAN`) so the pivots are swing-scale - the same definition, not
a second one. A pivot needs `_CHART_PIVOT_SPAN` bars after it, so a peak younger than that is not
yet a pivot and the shapes that need it cannot yet be reported (by design: nothing is guessed
from an unfinished swing). Troughs (reversal necklines) are the lowest low strictly between two
peaks, found directly on the bars.

*Height and noise.* Every shape has a `height` (peak minus neckline for reversals; the widest
gap between the boundaries for consolidations). A shape whose height is below
`_MIN_HEIGHT_FRACTION` of its price level is ignored as noise.

*Equality tolerance.* Two levels are "equal" (the two tops of an M, a flat boundary, the
containment band) when they differ by at most `_LEVEL_TOL_HEIGHT` of the shape's height, and never
by more than `_LEVEL_TOL_PRICE` of the price (`_level_tol`). A line is "flat" when it drifts at
most that tolerance across the window, and "sloping" otherwise. Head-and-shoulders shoulders use
the height fraction alone.

*Separation and size.* Consecutive structural peaks are at least `_MIN_SEPARATION_BARS` apart
(closer is one rounded extreme, not two); a consolidation spans at least `_MIN_PATTERN_BARS`;
no shape spans more than `_MAX_PATTERN_BARS`.

*Double top / double bottom.* Two peaks of the same kind at equal level (above), the highest
highs of the stretch between them (no higher high in between), and a trough between them.
Neckline: horizontal at the lowest low between the peaks (mirror image for the bottom, using
highs). Height: the higher peak minus the neckline. A top must be a top: no high in the
shape-width of bars before the first peak may exceed it (`_is_local_top`; same for the head).

*Head and shoulders (top, and inverse).* Three peaks with the middle one (head) above both
shoulders by at least `_HEAD_MARGIN_HEIGHT` of the height, shoulders equal within tolerance, no
higher high between the shoulders and head. Neckline: the straight line through the lowest low
between left shoulder and head and the lowest low between head and right shoulder (those two
points must be `_MIN_SEPARATION_BARS` apart, the line may not move more than
`_NECK_SLOPE_MAX_HEIGHT` of the height between them, and both shoulders must sit above it). Height:
head minus the neckline at the head's bar. The inverse is the exact mirror.

*Consolidations.* Over a window from one swing pivot to a later one, the upper boundary is the
two-point line through the first and last swing high in the window, the lower boundary likewise
through the swing lows - at least two touches per side, each side's touches at least
`_MIN_SEPARATION_BARS` apart, boundaries not crossed inside the window. Containment: no bar's high
rises above the upper line, and no low falls below the lower line, by more than the equality
tolerance. Classification by slopes: both flat = rectangle; flat upper + rising lower = ascending
triangle; falling upper + flat lower = descending triangle; falling upper + rising lower =
symmetrical triangle (every triangle therefore converges); both rising with the lower line
rising faster = rising wedge, both falling with the upper line falling faster = falling wedge (a
wedge also converges - boundaries sloping the same way but widening or parallel are a channel or an
expanding shape, which this module does not name). A swing pivot lying within the equality
tolerance of its boundary is reported as a touch. Consolidations that share bars are resolved
greedily - triangles and the rectangle before wedges (the loosest shape), then the widest.

*Status machine.* After the shape's last pivot the closes are scanned in order:
- `forming`: the shape is present and no close has moved beyond the confirmation level.
- `confirmed`: a CLOSE beyond the breakout level by `_CONFIRM_MARGIN` of that level. Breakout
  level: the neckline (reversals; for a top that means a close below, for a bottom above) or, for
  consolidations, either boundary (the side is reported in `broken_side`). The confirming bar is
  `confirmed_at`.
- `failed`: a close beyond the opposite extreme by the same margin - for a reversal the highest
  peak (head) of a top / lowest trough of a bottom, whether or not it was confirmed first (the
  structure broke); for a consolidation, after confirmation, the opposite boundary.
- Status is judged over at most `_TRACK_WIDTHS` shape-widths after the last pivot.
- A shape neither confirmed nor failed within its own width after the last pivot (for
  triangles: before the lines meet) has expired and is not reported.

*Left out: flag and pennant.* A flag needs a "pole" (a sharp prior move) and a "tight"
counter-trend channel. How sharp, how tight and how long have no agreed numeric definition, so
any rule here would be an invented threshold, which this codebase does not do (see
`patterns.py`). They are a disclosed gap, not a guess. Channels,
triple tops, rounded tops and cup-and-handle are likewise not defined.

Bounded work: every shape considers only pivots within `_MAX_PATTERN_BARS` of its first one, so
cost grows linearly with the number of bars; only the `_MAX_HITS` most recent hits are returned.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date as date_
from typing import Literal

from analytics.indicators import PriceBar
from analytics.models import (
    ChartPatternAnchor,
    ChartPatternFamily,
    ChartPatternHit,
    ChartPatternId,
    ChartPatternLine,
    ChartPatternPivot,
    ChartPatternPivotRole,
    SupportResistanceLevel,
)
from analytics.structure import support_resistance_levels

# Pivot span (bars judged each side) for chart patterns. The chart's own 支撐/壓力 lines use 3
# (about a week); a multi-week shape needs swings of at least about two trading weeks, so the same
# pivot definition is applied at 5 bars each side (one trading week on each side).
_CHART_PIVOT_SPAN = 5

# Ignore shapes shorter than 5% of their price: an ordinary Taiwan stock moves 1-3% in a day, so
# anything under about two normal days of range is indistinguishable from noise.
_MIN_HEIGHT_FRACTION = 0.05

# Levels are "equal" within a fifth of the shape's height. A single daily bar routinely spans a
# comparable share of a 5-10% shape, so a tighter tolerance would reject retests of one level.
_LEVEL_TOL_HEIGHT = 0.20

# ...but never by more than 3% of the price level, however tall the shape: a 60% swing must not
# excuse two tops 12% apart. 3% is about one to two ordinary daily ranges of a liquid TW stock.
_LEVEL_TOL_PRICE = 0.03

# A neckline may rise or fall by at most half the shape's height between its two points; steeper and
# it is a trend leg, not a line price is being held against.
_NECK_SLOPE_MAX_HEIGHT = 0.5

# A head must stand clear of its shoulders by a quarter of the height. Closer and the three peaks
# read as a triple top, not a head with shoulders.
_HEAD_MARGIN_HEIGHT = 0.25

# Two peaks closer than two trading weeks (10 bars) are one rounded extreme rather than two.
_MIN_SEPARATION_BARS = 10

# A consolidation must span at least four trading weeks (20 bars) to have a boundary worth drawing.
_MIN_PATTERN_BARS = 20

# No shape wider than about half a year (120 trading days): beyond that the swings belong to a
# different regime and the work per start pivot stays bounded.
_MAX_PATTERN_BARS = 120

# A confirming close must clear the level by 1% of it - roughly a few ticks on a TW stock - so a
# close that merely touches the line is not counted.
_CONFIRM_MARGIN = 0.01

# Resolution horizon after the last pivot, as a multiple of the shape's own width: a breakout much
# later than the shape took to form is no longer that shape's resolution.
_HORIZON_WIDTHS = 1.0

# Status is judged over at most two widths after the last pivot (the resolution horizon plus an
# equal stretch in which a confirmed shape can still fail); later closes belong to a new structure.
_TRACK_WIDTHS = 2.0

# Cap on the hits returned (most recent kept): a multi-year window completes dozens of shapes.
_MAX_HITS = 12

# Both boundaries slope the same way, so a wedge is the loosest consolidation: where it shares bars
# with a triangle or rectangle, the flatter-sided, more specific shape keeps them.
_WEDGES: frozenset[ChartPatternId] = frozenset({"rising_wedge", "falling_wedge"})

_MIN_BARS = 2 * _CHART_PIVOT_SPAN + 1
_MIN_TOUCHES = 2

type _Side = Literal["above", "below"]
type _Kind = Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class _Line:
    """Straight line through two (bar, price) points; `at(x)` extends it in both directions."""

    x0: int
    y0: float
    x1: int
    y1: float

    def at(self, x: int) -> float:
        if self.x1 == self.x0:
            return self.y0
        return self.y0 + (self.y1 - self.y0) * (x - self.x0) / (self.x1 - self.x0)


@dataclass(frozen=True, slots=True)
class _Point:
    idx: int
    price: float
    role: ChartPatternPivotRole


@dataclass(frozen=True, slots=True)
class _View:
    """The bars in 'top orientation'. For a top (`sign` +1) that is the bars as they are; for a
    bottom (`sign` -1) every price is negated and highs/lows swap, so one set of top rules serves
    both mirrors."""

    sign: int
    high: tuple[float, ...]
    low: tuple[float, ...]
    close: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _Shape:
    """A candidate in view space, before status resolution."""

    pattern: ChartPatternId
    points: tuple[_Point, ...]
    neck: _Line
    extreme: float
    height: float

    @property
    def start(self) -> int:
        return self.points[0].idx

    @property
    def end(self) -> int:
        return self.points[-1].idx


@dataclass(frozen=True, slots=True)
class _Box:
    """A consolidation candidate in real prices."""

    pattern: ChartPatternId
    upper: _Line
    lower: _Line
    start: int
    end: int
    height: float
    touches: tuple[tuple[int, float, _Kind], ...]


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: Literal["forming", "confirmed", "failed"]
    side: _Side | None
    confirm_idx: int | None
    fail_idx: int | None


def _margin(level: float) -> float:
    return _CONFIRM_MARGIN * abs(level)


def _level_tol(height: float, level: float) -> float:
    """Equality tolerance: a fifth of the height, never more than 3% of the price level."""
    return min(_LEVEL_TOL_HEIGHT * height, _LEVEL_TOL_PRICE * abs(level))


def _is_local_top(view: _View, start: int, end: int, top: float) -> bool:
    """No high in the shape-width of bars before `start` rises above `top` (data permitting)."""
    lo = max(0, start - (end - start))
    return all(h <= top for h in view.high[lo:start])


def _tall_enough(height: float, level: float) -> bool:
    return level != 0 and height > 0 and height / abs(level) >= _MIN_HEIGHT_FRACTION


def _lowest(values: Sequence[float], lo: int, hi: int) -> int:
    return min(range(lo, hi + 1), key=values.__getitem__)


def _make_view(bars: Sequence[PriceBar], sign: int) -> _View:
    if sign > 0:
        return _View(
            sign,
            tuple(b.high for b in bars),
            tuple(b.low for b in bars),
            tuple(b.close for b in bars),
        )
    return _View(
        sign,
        tuple(-b.low for b in bars),
        tuple(-b.high for b in bars),
        tuple(-b.close for b in bars),
    )


def _non_overlapping[T](
    items: Sequence[T], span: Callable[[T], tuple[int, int]], key: Callable[[T], tuple[int, ...]]
) -> list[T]:
    """Greedy: best-ranked first, dropping any later item that shares a bar with a kept one."""
    kept: list[T] = []
    for item in sorted(items, key=key):
        s, e = span(item)
        if all(e < span(k)[0] or s > span(k)[1] for k in kept):
            kept.append(item)
    return kept


@dataclass(frozen=True, slots=True)
class _Track:
    """Where the status machine looks: bars after `end`; a shape still unconfirmed after
    `horizon_end` has expired; nothing past `track_end` is considered."""

    n: int
    end: int
    horizon_end: int
    track_end: int


def _track(n: int, end: int, width: int, horizon_end: int | None = None) -> _Track:
    horizon = end + int(_HORIZON_WIDTHS * width)
    if horizon_end is not None:
        horizon = min(horizon, horizon_end)
    return _Track(n, end, horizon, end + int(_TRACK_WIDTHS * width))


def _run_machine(
    track: _Track,
    confirm_side: Callable[[int], _Side | None],
    fails: Callable[[int, _Side | None], bool],
) -> _Outcome | None:
    """Scan bars after `track.end` through the status machine. `None` = expired unresolved."""
    side: _Side | None = None
    confirm_idx: int | None = None
    for x in range(track.end + 1, min(track.n, track.track_end + 1)):
        if side is None and x > track.horizon_end:
            return None
        if fails(x, side):
            return _Outcome("failed", side, confirm_idx, x)
        if side is None:
            side = confirm_side(x)
            confirm_idx = x if side is not None else None
    return _Outcome("confirmed" if side else "forming", side, confirm_idx, None)


# -- reversal shapes (double top/bottom, head and shoulders) ---------------------------------


def _peaks(levels: Sequence[SupportResistanceLevel], sign: int) -> list[tuple[int, float]]:
    kind = "resistance" if sign > 0 else "support"
    return [(lv.bar_index, sign * lv.price) for lv in levels if lv.kind == kind]


def _double_shape(view: _View, p1: tuple[int, float], p2: tuple[int, float]) -> _Shape | None:
    (i1, h1), (i2, h2) = p1, p2
    top = max(h1, h2)
    if max(view.high[i1 : i2 + 1]) > top:
        return None
    t = _lowest(view.low, i1 + 1, i2 - 1)
    neck = view.low[t]
    height = top - neck
    if not _tall_enough(height, neck) or abs(h1 - h2) > _level_tol(height, top):
        return None
    if not _is_local_top(view, i1, i2, top):
        return None
    pts = (
        _Point(i1, h1, "peak"),
        _Point(t, neck, "trough"),
        _Point(i2, h2, "peak"),
    )
    pattern: ChartPatternId = "double_top" if view.sign > 0 else "double_bottom"
    return _Shape(pattern, pts, _Line(i1, neck, i2, neck), top, height)


def _double_shapes(view: _View, peaks: Sequence[tuple[int, float]]) -> list[_Shape]:
    shapes: list[_Shape] = []
    for a, p1 in enumerate(peaks):
        for p2 in peaks[a + 1 :]:
            gap = p2[0] - p1[0]
            if gap > _MAX_PATTERN_BARS:
                break
            if gap < _MIN_SEPARATION_BARS:
                continue
            shape = _double_shape(view, p1, p2)
            if shape is not None:
                shapes.append(shape)
    return shapes


def _hs_proportions_ok(
    view: _View,
    neck: _Line,
    height: float,
    peaks: tuple[tuple[int, float], tuple[int, float], tuple[int, float]],
) -> bool:
    """Head clear of the shoulders, shoulders level and above the neckline, neckline not a trend
    leg, and the head a top of its surroundings."""
    (il, hl), (_, hh), (ir, hr) = peaks
    return (
        hh - max(hl, hr) >= _HEAD_MARGIN_HEIGHT * height
        and abs(neck.y1 - neck.y0) <= _NECK_SLOPE_MAX_HEIGHT * height
        and abs(hl - hr) <= _LEVEL_TOL_HEIGHT * height
        and min(hl - neck.at(il), hr - neck.at(ir)) > 0
        and _is_local_top(view, il, ir, hh)
    )


def _hs_shape(
    view: _View, left: tuple[int, float], head: tuple[int, float], right: tuple[int, float]
) -> _Shape | None:
    (il, hl), (ih, hh), (ir, hr) = left, head, right
    peaks = (left, head, right)
    if hh <= max(hl, hr) or max(view.high[il : ir + 1]) > hh:
        return None
    t1 = _lowest(view.low, il + 1, ih - 1)
    t2 = _lowest(view.low, ih + 1, ir - 1)
    if t2 - t1 < _MIN_SEPARATION_BARS:
        return None
    neck = _Line(t1, view.low[t1], t2, view.low[t2])
    height = hh - neck.at(ih)
    if not _tall_enough(height, neck.at(ih)) or not _hs_proportions_ok(view, neck, height, peaks):
        return None
    pts = (
        _Point(il, hl, "left_shoulder"),
        _Point(t1, neck.y0, "neckline_point"),
        _Point(ih, hh, "head"),
        _Point(t2, neck.y1, "neckline_point"),
        _Point(ir, hr, "right_shoulder"),
    )
    pattern: ChartPatternId = "head_shoulders_top" if view.sign > 0 else "head_shoulders_bottom"
    return _Shape(pattern, pts, neck, hh, height)


def _hs_shapes(view: _View, peaks: Sequence[tuple[int, float]]) -> list[_Shape]:
    shapes: list[_Shape] = []
    for a, left in enumerate(peaks):
        for b in range(a + 1, len(peaks)):
            head = peaks[b]
            if head[0] - left[0] > _MAX_PATTERN_BARS:
                break
            if head[0] - left[0] < _MIN_SEPARATION_BARS or head[1] <= left[1]:
                continue
            shapes.extend(_hs_with_head(view, left, head, peaks[b + 1 :]))
    return shapes


def _hs_with_head(
    view: _View,
    left: tuple[int, float],
    head: tuple[int, float],
    rights: Sequence[tuple[int, float]],
) -> list[_Shape]:
    out: list[_Shape] = []
    for right in rights:
        if right[0] - left[0] > _MAX_PATTERN_BARS:
            break
        if right[0] - head[0] < _MIN_SEPARATION_BARS:
            continue
        shape = _hs_shape(view, left, head, right)
        if shape is not None:
            out.append(shape)
    return out


def _resolve_shape(view: _View, shape: _Shape) -> _Outcome | None:
    track = _track(len(view.close), shape.end, shape.end - shape.start)

    def confirm_side(x: int) -> _Side | None:
        level = shape.neck.at(x)
        return "below" if view.close[x] < level - _margin(level) else None

    def fails(x: int, _side: _Side | None) -> bool:
        return view.close[x] > shape.extreme + _margin(shape.extreme)

    return _run_machine(track, confirm_side, fails)


def _anchor(dates: Sequence[date_], idx: int, price: float) -> ChartPatternAnchor:
    return ChartPatternAnchor(bar_index=idx, trade_date=dates[idx], price=price)


def _real_role(role: ChartPatternPivotRole, sign: int) -> ChartPatternPivotRole:
    if sign > 0:
        return role
    if role == "peak":
        return "trough"
    if role == "trough":
        return "peak"
    return role


def _shape_hit(
    view: _View,
    shape: _Shape,
    outcome: _Outcome,
    bars: Sequence[PriceBar],
    dates: Sequence[date_],
) -> ChartPatternHit:
    sign = view.sign
    n = len(bars)
    pivots = tuple(
        ChartPatternPivot(
            bar_index=p.idx,
            trade_date=dates[p.idx],
            price=sign * p.price,
            role=_real_role(p.role, sign),
        )
        for p in shape.points
    )
    resolved = outcome.confirm_idx if outcome.confirm_idx is not None else outcome.fail_idx
    line_end = resolved if resolved is not None else n - 1
    start = shape.start
    line = ChartPatternLine(
        name="neckline",
        start=_anchor(dates, start, sign * shape.neck.at(start)),
        end=_anchor(dates, line_end, sign * shape.neck.at(line_end)),
    )
    family: ChartPatternFamily = "reversal_top" if sign > 0 else "reversal_bottom"
    side: _Side | None = None
    if outcome.side is not None:
        side = "below" if sign > 0 else "above"
    return ChartPatternHit(
        pattern=shape.pattern,
        family=family,
        status=outcome.status,
        pivots=pivots,
        lines=(line,),
        breakout_level=sign * shape.neck.at(line_end),
        broken_side=side,
        height=shape.height,
        window_start=_anchor(dates, start, sign * view.high[start]),
        window_end=_anchor(dates, shape.end, sign * view.high[shape.end]),
        confirmed_at=_close_anchor(bars, dates, outcome.confirm_idx),
        failed_at=_close_anchor(bars, dates, outcome.fail_idx),
    )


def _close_anchor(
    bars: Sequence[PriceBar], dates: Sequence[date_], idx: int | None
) -> ChartPatternAnchor | None:
    if idx is None:
        return None
    return _anchor(dates, idx, bars[idx].close)


def _reversal_hits(
    bars: Sequence[PriceBar],
    dates: Sequence[date_],
    levels: Sequence[SupportResistanceLevel],
    sign: int,
) -> list[ChartPatternHit]:
    view = _make_view(bars, sign)
    peaks = _peaks(levels, sign)
    hits: list[ChartPatternHit] = []
    for shapes in (_double_shapes(view, peaks), _hs_shapes(view, peaks)):
        for shape in _non_overlapping(
            shapes,
            lambda s: (s.start, s.end),
            lambda s: (-s.end, -s.start),
        ):
            outcome = _resolve_shape(view, shape)
            if outcome is not None:
                hits.append(_shape_hit(view, shape, outcome, bars, dates))
    return hits


# -- consolidation shapes (triangles, rectangle) ---------------------------------------------


type _Pivot = tuple[int, float, _Kind]


def _pivot_list(levels: Sequence[SupportResistanceLevel]) -> list[_Pivot]:
    return [(lv.bar_index, lv.price, "high" if lv.kind == "resistance" else "low") for lv in levels]


def _side_line(side: Sequence[_Pivot]) -> _Line | None:
    first, last = side[0], side[-1]
    if len(side) < _MIN_TOUCHES or last[0] - first[0] < _MIN_SEPARATION_BARS:
        return None
    return _Line(first[0], first[1], last[0], last[1])


def _converging(d_up: float, d_low: float, tol: float) -> ChartPatternId | None:
    """Name a shape whose boundaries close in on each other (`d_up < d_low`)."""
    if d_up < -tol and d_low > tol:
        return "symmetrical_triangle"
    if d_up > tol:
        return "rising_wedge"
    if d_low < -tol:
        return "falling_wedge"
    return None


def _classify(upper: _Line, lower: _Line, s: int, e: int, height: float) -> ChartPatternId | None:
    tol = _level_tol(height, upper.at(s))
    d_up = upper.at(e) - upper.at(s)
    d_low = lower.at(e) - lower.at(s)
    up_flat, low_flat = abs(d_up) <= tol, abs(d_low) <= tol
    if up_flat and low_flat:
        return "rectangle"
    if up_flat and d_low > tol:
        return "ascending_triangle"
    if low_flat and d_up < -tol:
        return "descending_triangle"
    return _converging(d_up, d_low, tol) if d_up < d_low else None


def _contained(
    bars: Sequence[PriceBar], upper: _Line, lower: _Line, span: tuple[int, int], tol: float
) -> bool:
    s, e = span
    for x in range(s, e + 1):
        if bars[x].high > upper.at(x) + tol or bars[x].low < lower.at(x) - tol:
            return False
    return True


def _box_for_window(bars: Sequence[PriceBar], window: Sequence[_Pivot]) -> _Box | None:
    s, e = window[0][0], window[-1][0]
    highs = [p for p in window if p[2] == "high"]
    lows = [p for p in window if p[2] == "low"]
    if len(highs) < _MIN_TOUCHES or len(lows) < _MIN_TOUCHES:
        return None
    upper, lower = _side_line(highs), _side_line(lows)
    if upper is None or lower is None:
        return None
    gap_s, gap_e = upper.at(s) - lower.at(s), upper.at(e) - lower.at(e)
    height = max(gap_s, gap_e)
    mid = (upper.at(s) + lower.at(s)) / 2
    if min(gap_s, gap_e) <= 0 or not _tall_enough(height, mid):
        return None
    pattern = _classify(upper, lower, s, e, height)
    tol = _level_tol(height, mid)
    if pattern is None or not _contained(bars, upper, lower, (s, e), tol):
        return None
    touches = tuple(
        p for p in window if abs(p[1] - (upper if p[2] == "high" else lower).at(p[0])) <= tol
    )
    return _Box(pattern, upper, lower, s, e, height, touches)


def _boxes(bars: Sequence[PriceBar], pivots: Sequence[_Pivot]) -> list[_Box]:
    boxes: list[_Box] = []
    for a, first in enumerate(pivots):
        for b in range(a + 1, len(pivots)):
            width = pivots[b][0] - first[0]
            if width > _MAX_PATTERN_BARS:
                break
            if width < _MIN_PATTERN_BARS:
                continue
            box = _box_for_window(bars, pivots[a : b + 1])
            if box is not None:
                boxes.append(box)
    return boxes


def _apex_limit(box: _Box) -> int:
    """Last bar before the boundaries meet (effectively unbounded for parallel lines)."""
    slope_gap = (box.upper.at(box.end) - box.upper.at(box.start)) - (
        box.lower.at(box.end) - box.lower.at(box.start)
    )
    if slope_gap >= 0:
        return box.end + _MAX_PATTERN_BARS
    gap_end = box.upper.at(box.end) - box.lower.at(box.end)
    per_bar = -slope_gap / (box.end - box.start)
    return box.end + int(gap_end / per_bar) - 1


def _resolve_box(bars: Sequence[PriceBar], box: _Box) -> _Outcome | None:
    track = _track(len(bars), box.end, box.end - box.start, _apex_limit(box))

    def confirm_side(x: int) -> _Side | None:
        up, low = box.upper.at(x), box.lower.at(x)
        if bars[x].close > up + _margin(up):
            return "above"
        if bars[x].close < low - _margin(low):
            return "below"
        return None

    def fails(x: int, side: _Side | None) -> bool:
        up, low = box.upper.at(x), box.lower.at(x)
        if side is None or up <= low:
            return False
        if side == "above":
            return bars[x].close < low - _margin(low)
        return bars[x].close > up + _margin(up)

    return _run_machine(track, confirm_side, fails)


def _drawn_line(
    name: Literal["upper", "lower"], line: _Line, span: tuple[int, int], dates: Sequence[date_]
) -> ChartPatternLine:
    return ChartPatternLine(
        name=name,
        start=_anchor(dates, span[0], line.at(span[0])),
        end=_anchor(dates, span[1], line.at(span[1])),
    )


def _box_hit(
    box: _Box,
    outcome: _Outcome,
    bars: Sequence[PriceBar],
    dates: Sequence[date_],
) -> ChartPatternHit:
    resolved = outcome.confirm_idx if outcome.confirm_idx is not None else outcome.fail_idx
    line_end = resolved if resolved is not None else len(bars) - 1
    upper_line = _drawn_line("upper", box.upper, (box.start, line_end), dates)
    lower_line = _drawn_line("lower", box.lower, (box.start, line_end), dates)
    pivots = tuple(
        ChartPatternPivot(
            bar_index=idx,
            trade_date=dates[idx],
            price=price,
            role="upper_touch" if kind == "high" else "lower_touch",
        )
        for idx, price, kind in box.touches
    )
    level: float | None = None
    if outcome.side is not None and outcome.confirm_idx is not None:
        boundary = box.upper if outcome.side == "above" else box.lower
        level = boundary.at(outcome.confirm_idx)
    return ChartPatternHit(
        pattern=box.pattern,
        family="consolidation",
        status=outcome.status,
        pivots=pivots,
        lines=(upper_line, lower_line),
        breakout_level=level,
        broken_side=outcome.side,
        height=box.height,
        window_start=_anchor(dates, box.start, bars[box.start].close),
        window_end=_anchor(dates, box.end, bars[box.end].close),
        confirmed_at=_close_anchor(bars, dates, outcome.confirm_idx),
        failed_at=_close_anchor(bars, dates, outcome.fail_idx),
    )


def _box_hits(
    bars: Sequence[PriceBar],
    dates: Sequence[date_],
    levels: Sequence[SupportResistanceLevel],
) -> list[ChartPatternHit]:
    hits: list[ChartPatternHit] = []
    candidates = _boxes(bars, _pivot_list(levels))
    for box in _non_overlapping(
        candidates,
        lambda b: (b.start, b.end),
        lambda b: (b.pattern in _WEDGES, -(b.end - b.start), -b.end),
    ):
        outcome = _resolve_box(bars, box)
        if outcome is not None:
            hits.append(_box_hit(box, outcome, bars, dates))
    return hits


def scan_chart_patterns(
    bars: Sequence[PriceBar], dates: Sequence[date_]
) -> tuple[ChartPatternHit, ...]:
    """Every chart pattern present in `bars` (ascending by date, `dates` parallel to it), oldest
    first, at most `_MAX_HITS` of the most recent. Too few bars or mismatched inputs return
    `()` - never a fabricated shape."""
    if len(bars) < _MIN_BARS or len(bars) != len(dates):
        return ()
    levels = support_resistance_levels(bars, span=_CHART_PIVOT_SPAN).levels
    hits = [
        *_reversal_hits(bars, dates, levels, 1),
        *_reversal_hits(bars, dates, levels, -1),
        *_box_hits(bars, dates, levels),
    ]
    hits.sort(key=lambda h: (h.window_end.bar_index, h.window_start.bar_index, h.pattern))
    return tuple(hits[-_MAX_HITS:])


__all__ = ["scan_chart_patterns"]
