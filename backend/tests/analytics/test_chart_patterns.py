"""Chart-pattern detection tests over synthetic piecewise-linear series (every expected result is
checkable by eye from the key points) plus frozen real bars. See `analytics/chart_patterns.py` for
the rules; the tolerance constants are imported so the tests move with them rather than copying
numbers."""

from __future__ import annotations

import csv
import itertools
import random
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from analytics import chart_patterns as cp
from analytics.indicators import PriceBar
from analytics.models import ChartPatternHit
from governance.textscan import scan_structured_payload
from tools.technical_tools import GetTechnicalIndicatorsTool

type Points = list[tuple[int, float]]

_START = date(2025, 1, 1)


def _bars(
    points: Points, *, wick: float = 0.003, jitter: float = 0.0, seed: int = 7
) -> tuple[list[PriceBar], list[date]]:
    """Closes interpolated linearly between the key points; each bar's high/low sit `wick` away
    from the close; `jitter` adds seeded noise to the closes (never to the key points' order)."""
    rng = random.Random(seed)
    closes: list[float] = []
    for (x0, y0), (x1, y1) in itertools.pairwise(points):
        closes.extend(y0 + (y1 - y0) * (x - x0) / (x1 - x0) for x in range(x0, x1))
    closes.append(points[-1][1])
    if jitter:
        closes = [c * (1 + rng.uniform(-jitter, jitter)) for c in closes]
    bars = [
        PriceBar(high=c * (1 + wick), low=c * (1 - wick), close=c, volume=1, open=c) for c in closes
    ]
    return bars, [_START + timedelta(days=i) for i in range(len(bars))]


def _scan(points: Points, **kw: float) -> tuple[ChartPatternHit, ...]:
    bars, dates = _bars(points, **kw)
    return cp.scan_chart_patterns(bars, dates)


def _of(hits: tuple[ChartPatternHit, ...], pattern: str) -> list[ChartPatternHit]:
    return [h for h in hits if h.pattern == pattern]


def _mirror(points: Points) -> Points:
    """Reflect prices about 100: a top shape becomes the matching bottom shape."""
    return [(x, 200.0 - y) for x, y in points]


# -- double top / double bottom --------------------------------------------------------------


def _m(top2: float = 110.0, tail: Points | None = None, top1: float = 110.0) -> Points:
    base: Points = [(0, 90), (20, top1), (40, 100), (60, top2)]
    return base + (tail if tail is not None else [(70, 104)])


@pytest.mark.parametrize("mirror", [False, True], ids=["double_top", "double_bottom"])
class TestDouble:
    def _pts(self, pts: Points, mirror: bool) -> Points:
        return _mirror(pts) if mirror else pts

    def _id(self, mirror: bool) -> str:
        return "double_bottom" if mirror else "double_top"

    def test_clean_shape_is_forming_with_pivots_and_lines(self, mirror: bool) -> None:
        hits = _of(_scan(self._pts(_m(), mirror)), self._id(mirror))

        assert len(hits) == 1
        hit = hits[0]
        assert hit.status == "forming"
        assert hit.family == ("reversal_bottom" if mirror else "reversal_top")
        assert [p.bar_index for p in hit.pivots] == [20, 40, 60]
        assert [p.role for p in hit.pivots] == (
            ["trough", "peak", "trough"] if mirror else ["peak", "trough", "peak"]
        )
        (neckline,) = hit.lines
        assert neckline.name == "neckline"
        assert neckline.start.price == pytest.approx(neckline.end.price)  # horizontal
        assert hit.breakout_level == pytest.approx(neckline.end.price)
        assert hit.confirmed_at is None and hit.failed_at is None and hit.broken_side is None
        assert hit.height > 0
        assert (hit.window_start.bar_index, hit.window_end.bar_index) == (20, 60)

    def test_tops_within_tolerance_are_accepted(self, mirror: bool) -> None:
        # 1.5 apart on a ~10.6 high shape: inside a fifth of the height.
        hits = _scan(self._pts(_m(top2=111.5), mirror))

        assert len(_of(hits, self._id(mirror))) == 1

    def test_tops_just_outside_tolerance_are_rejected(self, mirror: bool) -> None:
        hits = _scan(self._pts(_m(top2=113.0), mirror))

        assert _of(hits, self._id(mirror)) == []

    def test_too_small_height_is_ignored_as_noise(self, mirror: bool) -> None:
        pts: Points = [(0, 99), (20, 102), (40, 100), (60, 102), (70, 101)]

        assert _of(_scan(self._pts(pts, mirror)), self._id(mirror)) == []

    def test_second_peak_not_yet_a_pivot_is_incomplete(self, mirror: bool) -> None:
        # The series ends 3 bars after the second top: a pivot needs span bars on both sides.
        pts: Points = [(0, 90), (20, 110), (40, 100), (60, 110), (63, 108)]

        assert _of(_scan(self._pts(pts, mirror)), self._id(mirror)) == []

    def test_close_beyond_neckline_confirms_with_the_confirming_bar(self, mirror: bool) -> None:
        hits = _of(_scan(self._pts(_m(tail=[(80, 85)]), mirror)), self._id(mirror))

        assert len(hits) == 1
        hit = hits[0]
        assert hit.status == "confirmed"
        assert hit.broken_side == ("above" if mirror else "below")
        assert hit.confirmed_at is not None
        assert hit.confirmed_at.bar_index > 60
        # The confirming close is beyond the neckline by the stated margin.
        margin = cp._CONFIRM_MARGIN * hit.breakout_level  # type: ignore[operator]
        if mirror:
            assert hit.confirmed_at.price > hit.breakout_level + margin  # type: ignore[operator]
        else:
            assert hit.confirmed_at.price < hit.breakout_level - margin  # type: ignore[operator]

    def test_return_beyond_the_extreme_after_confirmation_fails(self, mirror: bool) -> None:
        tail: Points = [(72, 92), (95, 116)]
        hits = _of(_scan(self._pts(_m(tail=tail), mirror)), self._id(mirror))

        assert len(hits) == 1
        assert hits[0].status == "failed"
        assert hits[0].confirmed_at is not None and hits[0].failed_at is not None
        assert hits[0].failed_at.bar_index > hits[0].confirmed_at.bar_index

    def test_structure_broken_before_confirmation_fails(self, mirror: bool) -> None:
        tail: Points = [(70, 104), (85, 116)]
        hits = _of(_scan(self._pts(_m(tail=tail), mirror)), self._id(mirror))

        assert len(hits) == 1
        assert hits[0].status == "failed"
        assert hits[0].confirmed_at is None and hits[0].failed_at is not None

    def test_noisy_shape_still_detected(self, mirror: bool) -> None:
        hits = _scan(self._pts(_m(), mirror), jitter=0.002)

        assert len(_of(hits, self._id(mirror))) == 1


def test_peaks_closer_than_the_minimum_separation_are_one_extreme() -> None:
    pts: Points = [(0, 90), (20, 110), (26, 100), (32, 110), (45, 100)]

    assert _of(_scan(pts), "double_top") == []


# -- head and shoulders ----------------------------------------------------------------------


def _hs(right: float = 110.0, head: float = 125.0, tail: Points | None = None) -> Points:
    base: Points = [(0, 90), (15, 110), (28, 98), (45, head), (62, 98), (75, right)]
    return base + (tail if tail is not None else [(85, 104)])


@pytest.mark.parametrize("mirror", [False, True], ids=["top", "inverse"])
class TestHeadAndShoulders:
    def _pts(self, pts: Points, mirror: bool) -> Points:
        return _mirror(pts) if mirror else pts

    def _id(self, mirror: bool) -> str:
        return "head_shoulders_bottom" if mirror else "head_shoulders_top"

    def test_clean_shape_forming_with_neckline_through_the_two_troughs(self, mirror: bool) -> None:
        hits = _of(_scan(self._pts(_hs(), mirror)), self._id(mirror))

        assert len(hits) == 1
        hit = hits[0]
        assert hit.status == "forming"
        assert hit.family == ("reversal_bottom" if mirror else "reversal_top")
        assert [p.role for p in hit.pivots] == [
            "left_shoulder",
            "neckline_point",
            "head",
            "neckline_point",
            "right_shoulder",
        ]
        assert [p.bar_index for p in hit.pivots] == [15, 28, 45, 62, 75]
        assert hit.lines[0].name == "neckline"

    def test_shoulders_within_tolerance_accepted(self, mirror: bool) -> None:
        assert len(_of(_scan(self._pts(_hs(right=113.0), mirror)), self._id(mirror))) == 1

    def test_shoulders_just_outside_tolerance_rejected(self, mirror: bool) -> None:
        assert _of(_scan(self._pts(_hs(right=118.0), mirror)), self._id(mirror)) == []

    def test_head_not_clear_of_shoulders_rejected(self, mirror: bool) -> None:
        assert _of(_scan(self._pts(_hs(head=113.0), mirror)), self._id(mirror)) == []

    def test_too_small_height_is_ignored(self, mirror: bool) -> None:
        pts: Points = [(0, 99), (15, 101), (28, 100), (45, 103), (62, 100), (75, 101), (85, 100)]

        assert _of(_scan(self._pts(pts, mirror)), self._id(mirror)) == []

    def test_incomplete_without_the_right_shoulder(self, mirror: bool) -> None:
        pts: Points = [(0, 90), (15, 110), (28, 98), (45, 125), (62, 98), (68, 103)]

        assert _of(_scan(self._pts(pts, mirror)), self._id(mirror)) == []

    def test_close_beyond_neckline_confirms(self, mirror: bool) -> None:
        hits = _of(_scan(self._pts(_hs(tail=[(95, 85)]), mirror)), self._id(mirror))

        assert len(hits) == 1
        assert hits[0].status == "confirmed"
        assert hits[0].confirmed_at is not None

    def test_close_beyond_the_head_fails(self, mirror: bool) -> None:
        hits = _of(_scan(self._pts(_hs(tail=[(85, 104), (100, 130)]), mirror)), self._id(mirror))

        assert len(hits) == 1
        assert hits[0].status == "failed"

    def test_steep_neckline_is_not_a_neckline(self, mirror: bool) -> None:
        pts: Points = [(0, 90), (15, 110), (28, 85), (45, 125), (62, 112), (75, 118), (85, 114)]

        assert _of(_scan(self._pts(pts, mirror)), self._id(mirror)) == []


# -- triangles and rectangle -----------------------------------------------------------------

_ASCENDING: Points = [(0, 95), (10, 110), (22, 98), (34, 110), (44, 102), (56, 110), (66, 106)]


@pytest.mark.parametrize(
    ("pattern", "points"),
    [
        ("ascending_triangle", [*_ASCENDING, (75, 108)]),
        ("descending_triangle", _mirror([*_ASCENDING, (75, 108)])),
        (
            "symmetrical_triangle",
            [(0, 100), (8, 114), (20, 100), (32, 109), (44, 103), (50, 106), (56, 104.5)],
        ),
        (
            "rectangle",
            [(0, 100), (10, 110), (22, 100), (34, 110), (46, 100), (58, 110), (70, 100), (76, 104)],
        ),
    ],
)
class TestConsolidation:
    def test_clean_shape_is_forming_with_two_boundaries(self, pattern: str, points: Points) -> None:
        hits = _of(_scan(points), pattern)

        assert len(hits) == 1
        hit = hits[0]
        assert hit.family == "consolidation"
        assert hit.status == "forming"
        assert [line.name for line in hit.lines] == ["upper", "lower"]
        roles = {p.role for p in hit.pivots}
        assert roles == {"upper_touch", "lower_touch"}
        assert sum(p.role == "upper_touch" for p in hit.pivots) >= 2
        assert sum(p.role == "lower_touch" for p in hit.pivots) >= 2
        assert hit.breakout_level is None and hit.broken_side is None
        upper, lower = hit.lines
        assert upper.start.price > lower.start.price
        assert upper.end.price > lower.end.price

    def test_noisy_shape_still_detected(self, pattern: str, points: Points) -> None:
        assert len(_of(_scan(points, jitter=0.002), pattern)) == 1

    def test_too_small_height_is_ignored(self, pattern: str, points: Points) -> None:
        squashed = [(x, 100 + (y - 100) * 0.2) for x, y in points]

        assert _of(_scan(squashed), pattern) == []

    def test_incomplete_before_the_second_touch_on_a_side(
        self, pattern: str, points: Points
    ) -> None:
        # Cut well before the structure has two swing highs and two swing lows.
        assert _of(_scan(points[:3]), pattern) == []

    def test_close_beyond_a_boundary_confirms_and_reports_the_side(
        self, pattern: str, points: Points
    ) -> None:
        last_x, _ = points[-1]
        up = _scan([*points, (last_x + 14, 126)])
        down = _scan([*points, (last_x + 14, 78)])

        up_hits, down_hits = _of(up, pattern), _of(down, pattern)
        assert [h.status for h in up_hits] == ["confirmed"]
        assert up_hits[0].broken_side == "above"
        assert up_hits[0].confirmed_at is not None and up_hits[0].breakout_level is not None
        assert [h.status for h in down_hits] == ["confirmed"]
        assert down_hits[0].broken_side == "below"


# Series that end on a swing high well before the boundaries meet, so there is room to confirm and
# then fail before the apex (after it the boundaries no longer mean anything).
_ROOMY_ASCENDING: Points = [
    (0, 95),
    (10, 110),
    (22, 98),
    (34, 110),
    (44, 102),
    (56, 110),
    (64, 106),
]


@pytest.mark.parametrize(
    ("pattern", "points"),
    [
        ("ascending_triangle", _ROOMY_ASCENDING),
        ("descending_triangle", _mirror(_ROOMY_ASCENDING)),
        (
            "symmetrical_triangle",
            [(0, 100), (8, 120), (21, 100), (34, 116), (47, 103), (60, 112), (66, 108)],
        ),
        (
            "rectangle",
            [(0, 100), (10, 110), (22, 100), (34, 110), (46, 100), (58, 110), (70, 100), (76, 104)],
        ),
    ],
)
def test_closing_back_through_the_opposite_boundary_fails(pattern: str, points: Points) -> None:
    last_x, _ = points[-1]
    whipsaw = [*points, (last_x + 3, 135), (last_x + 6, 128), (last_x + 10, 70)]

    hits = _of(_scan(whipsaw), pattern)

    assert [h.status for h in hits] == ["failed"]
    assert hits[0].confirmed_at is not None and hits[0].failed_at is not None
    assert hits[0].broken_side == "above"


def test_wedge_rising_both_boundaries_is_not_named() -> None:
    pts: Points = [(0, 100), (10, 106), (22, 100), (34, 114), (46, 108), (58, 122), (66, 116)]
    names = {h.pattern for h in _scan(pts)}

    assert names.isdisjoint(
        {"ascending_triangle", "descending_triangle", "symmetrical_triangle", "rectangle"}
    )


def test_unresolved_consolidation_expires_after_its_own_width() -> None:
    # Rectangle, then price sits mid-range for far longer than the rectangle took to form.
    pts: Points = [
        (0, 100),
        (10, 110),
        (22, 100),
        (34, 110),
        (46, 100),
        (58, 110),
        (70, 100),
        (76, 105),
        (200, 105.5),
    ]

    assert _of(_scan(pts), "rectangle") == []


# -- shared contract -------------------------------------------------------------------------


def test_monotonic_and_flat_series_have_no_hits() -> None:
    rising = [PriceBar(high=c * 1.003, low=c * 0.997, close=c, volume=1) for c in range(100, 400)]
    flat = [PriceBar(high=100.3, low=99.7, close=100.0, volume=1) for _ in range(300)]
    dates = [_START + timedelta(days=i) for i in range(400)]

    assert cp.scan_chart_patterns(rising, dates[: len(rising)]) == ()
    assert cp.scan_chart_patterns(flat, dates[: len(flat)]) == ()


def test_too_few_bars_or_mismatched_dates_return_nothing() -> None:
    bars, dates = _bars(_m())

    assert cp.scan_chart_patterns(bars[:5], dates[:5]) == ()
    assert cp.scan_chart_patterns(bars, dates[:-1]) == ()
    assert cp.scan_chart_patterns([], []) == ()


def test_scan_is_deterministic() -> None:
    bars, dates = _bars(_hs(tail=[(95, 85)]), jitter=0.002)

    assert cp.scan_chart_patterns(bars, dates) == cp.scan_chart_patterns(bars, dates)


def test_window_edge_first_peak_needs_span_bars_before_it() -> None:
    span = cp._CHART_PIVOT_SPAN
    late: Points = [(0, 100), (span + 1, 110), (24, 100), (44, 110), (56, 104)]
    early: Points = [(0, 100), (span - 1, 110), (24, 100), (44, 110), (56, 104)]

    assert len(_of(_scan(late), "double_top")) == 1
    assert _of(_scan(early), "double_top") == []


def test_window_edge_last_peak_needs_span_bars_after_it() -> None:
    span = cp._CHART_PIVOT_SPAN
    ok: Points = [(0, 90), (20, 110), (40, 100), (60, 110), (60 + span + 1, 105)]
    short: Points = [(0, 90), (20, 110), (40, 100), (60, 110), (60 + span - 1, 105)]

    assert len(_of(_scan(ok), "double_top")) == 1
    assert _of(_scan(short), "double_top") == []


def test_hits_are_ordered_oldest_first_and_capped() -> None:
    rng = random.Random(3)
    price, closes = 100.0, []
    for _ in range(900):
        price *= 1 + rng.uniform(-0.03, 0.03)
        closes.append(price)
    bars = [PriceBar(high=c * 1.01, low=c * 0.99, close=c, volume=1) for c in closes]
    dates = [_START + timedelta(days=i) for i in range(len(bars))]

    hits = cp.scan_chart_patterns(bars, dates)

    assert len(hits) <= cp._MAX_HITS
    ends = [h.window_end.bar_index for h in hits]
    assert ends == sorted(ends)


def test_three_years_of_bars_scan_quickly() -> None:
    rng = random.Random(11)
    price, closes = 100.0, []
    for _ in range(780):
        price *= 1 + rng.uniform(-0.025, 0.025)
        closes.append(price)
    bars = [PriceBar(high=c * 1.01, low=c * 0.99, close=c, volume=1) for c in closes]
    dates = [_START + timedelta(days=i) for i in range(len(bars))]

    started = time.perf_counter()
    cp.scan_chart_patterns(bars, dates)

    assert time.perf_counter() - started < 1.0


def test_every_hit_carries_consistent_dates_and_levels() -> None:
    bars, dates = _bars(_hs(tail=[(95, 85)]))
    for hit in cp.scan_chart_patterns(bars, dates):
        for pivot in hit.pivots:
            assert pivot.trade_date == dates[pivot.bar_index]
        for line in hit.lines:
            assert line.start.trade_date == dates[line.start.bar_index]
            assert line.end.bar_index >= line.start.bar_index
        assert hit.window_start.bar_index <= hit.window_end.bar_index
        assert hit.height > 0


# -- frozen real bars ------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"

# (pattern, status, window start, window end, confirmed on, failed on). Hand-checked against the
# raw bars: 2330 inverse head-and-shoulders (lows 1435 / 1375 / 1415 are strict 5-bar pivots, the
# neckline highs 1525 and 1515 are the highest highs between them, the first close above the
# neckline plus 1% is 2025-12-29), 2330 double top (peaks 2535 / 2590 around the 2180 low, no close
# under 2158 since), 3711 double top (251.5 / 248 around the 206 low; first close above 254.0 is
# 2026-01-02) and 3711 head-and-shoulders top (first close above 729 plus 1% is 2026-10-05).
_EXPECTED: dict[str, list[tuple[str, str, str, str, str | None, str | None]]] = {
    "2330": [
        ("head_shoulders_bottom", "confirmed", "2025-10-23", "2025-12-18", "2025-12-29", None),
        ("descending_triangle", "confirmed", "2026-06-11", "2026-08-13", "2026-09-07", None),
        ("head_shoulders_bottom", "confirmed", "2026-06-26", "2026-08-19", "2026-09-07", None),
        ("head_shoulders_top", "failed", "2026-06-03", "2026-08-28", None, "2026-10-05"),
        ("double_top", "forming", "2026-06-23", "2026-10-06", None, None),
    ],
    "3711": [
        ("double_top", "failed", "2025-11-04", "2025-12-12", None, "2026-01-02"),
        ("symmetrical_triangle", "confirmed", "2026-02-05", "2026-03-23", "2026-03-26", None),
        ("head_shoulders_top", "failed", "2026-05-27", "2026-09-10", None, "2026-10-05"),
        ("ascending_triangle", "confirmed", "2026-07-30", "2026-09-15", "2026-09-22", None),
    ],
    "6669": [
        ("double_bottom", "failed", "2025-11-24", "2025-12-18", None, "2026-01-13"),
        ("ascending_triangle", "failed", "2025-11-12", "2026-01-08", "2026-01-12", "2026-05-08"),
        ("double_top", "confirmed", "2025-12-08", "2026-01-08", "2026-01-13", None),
        ("double_bottom", "confirmed", "2026-01-21", "2026-02-06", "2026-02-25", None),
        ("head_shoulders_top", "failed", "2026-04-27", "2026-07-06", None, "2026-08-04"),
        ("head_shoulders_bottom", "failed", "2026-06-11", "2026-07-17", "2026-07-22", "2026-09-02"),
        ("double_top", "failed", "2026-05-25", "2026-07-24", None, "2026-08-04"),
    ],
}


def _real(symbol: str) -> tuple[list[PriceBar], list[date]]:
    with (_FIXTURES / f"chart_patterns_{symbol}.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    bars = [
        PriceBar(
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=int(r["volume"]),
        )
        for r in rows
    ]
    return bars, [date.fromisoformat(r["trade_date"]) for r in rows]


@pytest.mark.parametrize("symbol", sorted(_EXPECTED))
def test_real_bars_produce_the_hand_checked_hits(symbol: str) -> None:
    bars, dates = _real(symbol)

    hits = cp.scan_chart_patterns(bars, dates)

    got = [
        (
            h.pattern,
            h.status,
            h.window_start.trade_date.isoformat(),
            h.window_end.trade_date.isoformat(),
            h.confirmed_at.trade_date.isoformat() if h.confirmed_at else None,
            h.failed_at.trade_date.isoformat() if h.failed_at else None,
        )
        for h in hits
    ]
    assert got == _EXPECTED[symbol]


def test_real_hit_levels_match_the_raw_bars() -> None:
    bars, dates = _real("2330")
    hit = cp.scan_chart_patterns(bars, dates)[0]  # the 2025 inverse head-and-shoulders

    by_date = {p.trade_date.isoformat(): p for p in hit.pivots}
    assert by_date["2025-10-23"].price == 1435.0
    assert by_date["2025-11-24"].price == 1375.0
    assert by_date["2025-12-18"].price == 1415.0
    assert by_date["2025-10-31"].price == 1525.0  # neckline highs
    assert by_date["2025-12-11"].price == 1515.0
    assert hit.confirmed_at is not None and hit.confirmed_at.price == 1530.0


# -- governance ------------------------------------------------------------------------------


@pytest.mark.governance
def test_hits_carry_no_trading_language_or_vendor_names() -> None:
    hits: list[ChartPatternHit] = []
    for symbol in _EXPECTED:
        hits.extend(cp.scan_chart_patterns(*_real(symbol)))
    hits.extend(_scan(_hs(tail=[(95, 85)])))
    assert hits

    for hit in hits:
        cleaned, changed = scan_structured_payload(hit.model_dump(mode="json"))
        assert changed is False
        assert cleaned == hit.model_dump(mode="json")


@pytest.mark.governance
def test_hits_have_no_target_stop_bias_or_probability_fields() -> None:
    fields = set(ChartPatternHit.model_fields)

    assert fields.isdisjoint(
        {"target", "target_price", "stop", "stop_loss", "direction", "probability", "signal"}
    )


@pytest.mark.governance
def test_tool_description_is_clean_and_states_the_descriptive_rule() -> None:
    description = GetTechnicalIndicatorsTool.description

    cleaned, changed = scan_structured_payload(description)

    assert changed is False
    assert cleaned == description
    assert "chart_patterns" in description
    assert "never a buy/sell instruction" in description
