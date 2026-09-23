"""The 趨勢評分 (trend score) composite — a second, independent descriptive score from
`analytics/score.py`'s multi-factor composite. Where that score answers "how does this symbol
rank against today's whole universe on momentum/quality/value", this one answers "how strong is
this symbol's OWN trend right now, across technical, chip, credit, concentration, rotation, and
market-co-movement evidence" — a single-symbol read, not a cross-sectional rank, so most
dimensions here are bounded scoring rules over the symbol's own series rather than
percentile-against-the-universe.

**Governance framing (same load-bearing rule as `analytics/score.py`):** every dimension here is
a descriptive read of what already-ingested data currently shows, never a forecast and never a
buy/sell/hold word. In particular this module deliberately produces no next-day/forward-looking
prediction — the user explicitly asked for a *trend strength* read grounded in TW-market-specific
signals (chip flow, margin/credit, broker concentration, sector rotation, TAIEX co-movement), not
a price forecast, precisely because a forward directional call is what this codebase's
non-trading rule exists to keep out of any score.

**Six dimensions, weighted 技術20% / 籌碼35% / 資金信用15% / 分點集中10% / 板塊輪動10% /
大盤連動10%** (sums to 1.0; renormalized over whichever dimensions are available for this
symbol/date, exactly like `analytics/score.py::blend_composite` — a dimension unavailable for
lack of ingested data is disclosed and dropped, never defaulted to a fake neutral value):

- **技術面 (technical)** — a deterministic 0-100 combination of MA-stack alignment, KD position,
  MACD histogram sign/direction, and Bollinger %b, computed straight from `analytics/indicators.py`
  (already the score/technical-tab's own library). Bounded-oscillator arithmetic, not a
  cross-sectional percentile — nothing here needs a comparison universe.
- **籌碼動能 (institutional flow)** — reuses `net_buy_20d_trend()` (`analytics/score.py`), ranked
  cross-sectionally via `winsorized_percentile` against the day's flow universe — the *same* chip
  sub-signal `analytics/score.py`'s `revenue_flow`/`liquidity_flow` factors already use, so this
  dimension needs the caller to supply that day's flow universe (from the same batch pass that
  already builds it for the main score).
- **資金信用 (margin/credit trend)** — NEW: day-over-day 融資餘額/融券餘額 delta read against the
  symbol's own price direction (see `margin_credit_trend_score`'s own docstring for the exact
  rule) — a single-symbol read, no comparison universe needed.
- **分點集中度 (broker concentration)** — NEW: `concentration_days_pct`/`main_force_net`, factored
  out of `tools/market_tools.py::GetChipFlowsTool` into `concentration_days_pct` below so both the
  existing 分點 tool and this module share one implementation rather than two copies of the same
  arithmetic.
- **板塊輪動 (sector rotation)** — NEW: how this symbol's own industry's `get_sector_rotation` rank
  moved over the rotation window (improving rank = more money rotating toward its sector = higher
  score) — see `sector_rotation_trend_score`.
- **大盤連動 (TAIEX co-movement)** — NEW: rolling Pearson correlation between the symbol's daily
  returns and TAIEX's daily returns over a trailing window — see `taiex_correlation_score`. Higher
  score = trending WITH the broad market (a confirming tailwind, descriptive co-movement only,
  never a claim about what TAIEX or the symbol will do next).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from analytics.indicators import PriceBar, bollinger, kd, macd, moving_average_bundle
from analytics.score import winsorized_percentile
from schemas.capabilities import ChipBrokerRow, MarginShortRow

# Weights sum to 1.0 before any renormalization for unavailable dimensions — set per the user's
# explicit instruction: technical is a smaller share (20%) than institutional chip flow (35%),
# reflecting that TW retail/institutional chip behavior is read as more informative of trend
# strength than price-derived oscillators alone.
TECH_WEIGHT = 0.20
CHIP_WEIGHT = 0.35
MARGIN_WEIGHT = 0.15
CONCENTRATION_WEIGHT = 0.10
ROTATION_WEIGHT = 0.10
TAIEX_CORR_WEIGHT = 0.10

DIMENSION_WEIGHTS: dict[str, float] = {
    "technical": TECH_WEIGHT,
    "chip_flow": CHIP_WEIGHT,
    "margin_credit": MARGIN_WEIGHT,
    "broker_concentration": CONCENTRATION_WEIGHT,
    "sector_rotation": ROTATION_WEIGHT,
    "taiex_correlation": TAIEX_CORR_WEIGHT,
}

_TAIEX_CORR_MIN_POINTS = 10  # fewer daily-return pairs than this makes a Pearson r unstable
_ROTATION_MIN_RANK = 1  # the best possible daily industry rank (1 = most net inflow that day)
_MIN_TREND_ROWS = 2  # need today + one prior row to compute any day-over-day delta


@dataclass(frozen=True)
class TrendDimension:
    """One dimension's contribution — mirrors `analytics.models.FactorScore`'s shape (a bounded
    0-100 `value`, a human-readable `description`, and an explicit `unavailable_reason` instead of
    a fabricated neutral default) so the composite blend and the disclosure convention match the
    existing multi-factor score exactly."""

    name: str
    value: float | None
    description: str
    unavailable_reason: str | None = None


def _unavailable(name: str, reason: str) -> TrendDimension:
    return TrendDimension(
        name=name, value=None, description=f"{name} unavailable", unavailable_reason=reason
    )


# ---------------------------------------------------------------------------
# 技術面 (technical) — deterministic scoring over already-computed indicators, no universe needed.
# ---------------------------------------------------------------------------

_MA_STACK_WINDOWS: tuple[int, ...] = (5, 10, 20, 60)


def _ma_stack_score(closes: Sequence[float], ma_values: dict[int, float | None]) -> float | None:
    """Counts how many of MA5>MA10>MA20>MA60 (bullish stack) or the reverse (bearish stack) hold,
    plus whether price sits above/below the stack — a plain "how aligned is the moving-average
    ladder" read, scored 0-100 by how many of the 5 pairwise/price-position checks agree in one
    direction. Never a forecast — purely today's snapshot of the MA ladder's own shape."""
    price = closes[-1] if closes else None
    values = [ma_values.get(w) for w in _MA_STACK_WINDOWS]
    if price is None or any(v is None for v in values):
        return None
    ma5, ma10, ma20, ma60 = (v for v in values if v is not None)
    checks = [ma5 > ma10, ma10 > ma20, ma20 > ma60, price > ma5]
    bullish_count = sum(1 for c in checks if c)
    return 100.0 * bullish_count / len(checks)


def _kd_score(k: float | None, d: float | None) -> float | None:
    """%K/%D themselves already sit on a natural 0-100 scale — read directly as the score, with a
    slight lift when %K is above %D (short-term momentum confirming the level), capped at 100."""
    if k is None or d is None:
        return None
    return min(100.0, k + (5.0 if k > d else -5.0 if k < d else 0.0))


def _macd_score(histogram: float | None, prior_histogram: float | None) -> float | None:
    """MACD histogram has no natural bound, so it is scored by sign + whether it is expanding
    (widening in its own direction = strengthening trend) rather than by its raw magnitude, which
    varies wildly across symbols/price levels."""
    if histogram is None:
        return None
    expanding = prior_histogram is not None and abs(histogram) > abs(prior_histogram)
    if histogram > 0:
        return 75.0 if expanding else 60.0
    if histogram < 0:
        return 25.0 if expanding else 40.0
    return 50.0


def _bollinger_pct_b_score(
    close: float | None, upper: float | None, lower: float | None
) -> float | None:
    """%b = (close - lower) / (upper - lower), already a natural 0-1 read of where price sits in
    its own band — rescaled to 0-100 directly, no additional transform."""
    if close is None or upper is None or lower is None or upper == lower:
        return None
    pct_b = (close - lower) / (upper - lower)
    return min(100.0, max(0.0, pct_b * 100.0))


def technical_trend_score(bars: Sequence[PriceBar]) -> TrendDimension:
    """The 技術面 dimension: MA-stack alignment (40%), KD (20%), MACD histogram sign/expansion
    (20%), and Bollinger %b (20%) equal-ish-weighted into one 0-100 sub-score. Each sub-signal
    degrades independently (missing history for one indicator does not zero out the others) —
    unavailable only when every sub-signal is unavailable."""
    if not bars:
        return _unavailable("technical", "no price bars available for this symbol")
    closes = [b.close for b in bars]
    ma_bundle = moving_average_bundle(bars)
    ma_values = {
        5: ma_bundle.ma5.values[-1] if ma_bundle.ma5.values else None,
        10: ma_bundle.ma10.values[-1] if ma_bundle.ma10.values else None,
        20: ma_bundle.ma20.values[-1] if ma_bundle.ma20.values else None,
        60: ma_bundle.ma60.values[-1] if ma_bundle.ma60.values else None,
    }
    kd_result = kd(bars)
    macd_result = macd(bars)
    bb_result = bollinger(bars)

    sub_scores: list[tuple[float, float | None]] = [
        (0.40, _ma_stack_score(closes, ma_values)),
        (
            0.20,
            _kd_score(
                kd_result.k[-1] if kd_result.k else None, kd_result.d[-1] if kd_result.d else None
            ),
        ),
        (
            0.20,
            _macd_score(
                macd_result.histogram[-1] if macd_result.histogram else None,
                macd_result.histogram[-2] if len(macd_result.histogram) > 1 else None,
            ),
        ),
        (
            0.20,
            _bollinger_pct_b_score(
                closes[-1] if closes else None,
                bb_result.upper[-1] if bb_result.upper else None,
                bb_result.lower[-1] if bb_result.lower else None,
            ),
        ),
    ]
    available = [(w, v) for w, v in sub_scores if v is not None]
    if not available:
        return _unavailable(
            "technical", "not enough price history for MA/KD/MACD/Bollinger to compute"
        )
    total_weight = sum(w for w, _ in available)
    blended = sum(w * v for w, v in available) / total_weight
    return TrendDimension(
        name="technical",
        value=min(100.0, max(0.0, blended)),
        description="MA排列/KD/MACD/BB %b 綜合（技術面現況，非預測）",
    )


# ---------------------------------------------------------------------------
# 籌碼動能 (institutional flow) — cross-sectional percentile, reusing analytics.score's own signal.
# ---------------------------------------------------------------------------


def chip_flow_trend_score(
    flow_value: float | None, flow_universe: Sequence[float]
) -> TrendDimension:
    """The 籌碼動能 dimension: the same `net_buy_20d_trend()` value `analytics/score.py`'s
    `revenue_flow`/`liquidity_flow` factors already compute, ranked cross-sectionally — a caller
    passes the day's already-built flow universe (from the same batch pass), never recomputes
    it."""
    if flow_value is None:
        return _unavailable(
            "chip_flow", "no institutional-flow rows ingested for this symbol/window"
        )
    percentile = winsorized_percentile(flow_value, flow_universe)
    if percentile is None:
        return _unavailable("chip_flow", "fewer than 2 symbols in today's flow universe")
    return TrendDimension(
        name="chip_flow",
        value=percentile,
        description="三大法人 20日淨買超趨勢百分位",
    )


# ---------------------------------------------------------------------------
# 資金信用 (margin/credit trend) — day-over-day balance delta vs price direction.
# ---------------------------------------------------------------------------


def margin_credit_trend_score(
    rows: Sequence[MarginShortRow], *, price_change_pct: float | None
) -> TrendDimension:
    """融資融劵餘額變化 read against price direction: rising 融資餘額 (margin-purchase balance)
    alongside a rising price reads as leveraged retail chasing the move (a real, if fragile,
    confirming signal — scored positively but capped below the strongest band, since leveraged
    buying is a weaker confirmation than institutional flow); falling 融券餘額 (short balance)
    alongside a falling price reads as short-covering pressure building. Requires at least 2 rows
    (today + one prior day) to compute a delta — a single day's balance alone has no trend."""
    if len(rows) < _MIN_TREND_ROWS:
        return _unavailable(
            "margin_credit", "fewer than 2 days of margin/short balance rows ingested"
        )
    ordered = sorted(rows, key=lambda r: r.trade_date)
    latest, prior = ordered[-1], ordered[-2]
    margin_delta = latest.margin_purchase_today_balance - prior.margin_purchase_today_balance
    short_delta = latest.short_sale_today_balance - prior.short_sale_today_balance

    if price_change_pct is None:
        return _unavailable(
            "margin_credit", "no price-change reference to score the balance delta against"
        )

    # Both deltas point the same direction as price: confirming leverage flow. Opposite: a
    # caution flag (leverage building against the move). Neutral (near-zero delta): mid-scale.
    score = 50.0
    if margin_delta > 0 and price_change_pct > 0:
        score = 70.0
    elif margin_delta < 0 and price_change_pct < 0:
        score = 65.0  # deleveraging alongside a decline — orderly, mildly confirming
    elif margin_delta > 0 and price_change_pct < 0:
        score = 25.0  # leverage building against a falling price — a caution flag
    elif margin_delta < 0 and price_change_pct > 0:
        score = 55.0

    if short_delta < 0 and price_change_pct < 0:
        score = min(100.0, score + 10.0)  # short-covering pressure building under a decline

    return TrendDimension(
        name="margin_credit",
        value=min(100.0, max(0.0, score)),
        description="融資融劵餘額變化與股價方向比對",
    )


# ---------------------------------------------------------------------------
# 分點集中度 (broker concentration) — factored out of market_tools.py::GetChipFlowsTool.
# ---------------------------------------------------------------------------


def concentration_days_pct(rows: Sequence[ChipBrokerRow], overall_net: int) -> float | None:
    """集中天數% — the % of trading days within the fetched range where that day's own net
    buy/sell stayed same-signed as the range's overall net. Shared verbatim with
    `tools/market_tools.py::GetChipFlowsTool` (that tool's own `_concentration_days_pct` should
    delegate here rather than duplicate this arithmetic) — one implementation, two callers."""
    by_date: dict[object, int] = {}
    for row in rows:
        by_date[row.trade_date] = by_date.get(row.trade_date, 0) + row.buy - row.sell
    if not by_date or overall_net == 0:
        return None
    same_signed = sum(1 for net in by_date.values() if (net > 0) == (overall_net > 0) and net != 0)
    return 100.0 * same_signed / len(by_date)


def broker_concentration_trend_score(
    rows: Sequence[ChipBrokerRow], *, main_force_net: int, overall_net: int
) -> TrendDimension:
    """The 分點集中度 dimension: `concentration_days_pct` directly as the score (higher
    consistency of same-direction days = a more concentrated, higher-conviction flow), with a
    modest boost when `main_force_net` (top-broker combined net) agrees in sign with the overall
    net — concentrated AND directionally consistent scores highest."""
    if not rows:
        return _unavailable(
            "broker_concentration", "no 分點 (broker-branch) rows ingested for this symbol/window"
        )
    pct = concentration_days_pct(rows, overall_net)
    if pct is None:
        return _unavailable(
            "broker_concentration", "overall net flow was zero — no dominant direction to score"
        )
    agrees = (main_force_net > 0) == (overall_net > 0)
    score = min(100.0, pct + (5.0 if agrees and overall_net != 0 else 0.0))
    return TrendDimension(
        name="broker_concentration",
        value=score,
        description="分點集中天數% + 主力籌碼方向一致性",
    )


# ---------------------------------------------------------------------------
# 板塊輪動 (sector rotation) — this symbol's own industry's rank-bump trend.
# ---------------------------------------------------------------------------


def sector_rotation_trend_score(industry_ranks: Sequence[int]) -> TrendDimension:
    """The 板塊輪動 dimension: whether this symbol's own industry's daily rank (1 = most net
    institutional inflow that day, from `get_sector_rotation`'s own rank-bump data) improved or
    worsened across the rotation window — an improving (numerically decreasing) rank means more
    money is rotating toward this symbol's sector, scored higher. `industry_ranks` must be in
    chronological order (oldest first), matching `get_sector_rotation`'s own row order."""
    if len(industry_ranks) < _MIN_TREND_ROWS:
        return _unavailable(
            "sector_rotation",
            "fewer than 2 days of sector-rotation rank data for this symbol's industry",
        )
    first, last = industry_ranks[0], industry_ranks[-1]
    # A rank improving from e.g. 20 -> 5 is a strong positive move; scored by how much of the
    # possible improvement (toward rank 1) was realized, floored at the worst-case (rank worsened
    # all the way) mapping to a low but non-zero score.
    if first == last:
        return TrendDimension(name="sector_rotation", value=50.0, description="產業輪動排名持平")
    improvement = first - last  # positive = rank improved (moved toward 1)
    span = max(first, _ROTATION_MIN_RANK) or 1
    normalized = 50.0 + 50.0 * max(-1.0, min(1.0, improvement / span))
    return TrendDimension(
        name="sector_rotation",
        value=min(100.0, max(0.0, normalized)),
        description=f"同產業資金流入排名由第{first}到第{last}",
    )


# ---------------------------------------------------------------------------
# 大盤連動 (TAIEX co-movement) — rolling Pearson correlation of daily returns.
# ---------------------------------------------------------------------------


def _daily_returns(closes: Sequence[float]) -> list[float]:
    return [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] != 0
    ]


def taiex_correlation_score(
    symbol_closes: Sequence[float], taiex_closes: Sequence[float]
) -> TrendDimension:
    """The 大盤連動 dimension: Pearson correlation of daily returns between the symbol and TAIEX
    over the trailing window both series cover, mapped from [-1, 1] onto [0, 100] (higher =
    trending WITH the broad market — a confirming tailwind, purely descriptive co-movement, never
    a claim about either series' next move). `symbol_closes`/`taiex_closes` must be aligned
    same-length, same-trading-day series; a caller mismatches these at its own risk since this is
    a pure numeric transform with no date bookkeeping of its own."""
    if len(symbol_closes) != len(taiex_closes) or len(symbol_closes) < _TAIEX_CORR_MIN_POINTS + 1:
        return _unavailable(
            "taiex_correlation",
            f"fewer than {_TAIEX_CORR_MIN_POINTS + 1} aligned trading days of symbol+TAIEX "
            "price history",
        )
    symbol_returns = _daily_returns(symbol_closes)
    taiex_returns = _daily_returns(taiex_closes)
    if len(symbol_returns) < _TAIEX_CORR_MIN_POINTS or len(taiex_returns) < _TAIEX_CORR_MIN_POINTS:
        return _unavailable(
            "taiex_correlation", "not enough non-zero-close trading days to compute returns"
        )
    try:
        correlation = statistics.correlation(symbol_returns, taiex_returns)
    except statistics.StatisticsError:
        return _unavailable("taiex_correlation", "return series had zero variance over this window")
    score = (correlation + 1.0) * 50.0
    return TrendDimension(
        name="taiex_correlation",
        value=min(100.0, max(0.0, score)),
        description=f"與大盤(TAIEX)日報酬相關係數 {correlation:.2f}",
    )


# ---------------------------------------------------------------------------
# Composite blend — same renormalize-over-available pattern as analytics.score.blend_composite.
# ---------------------------------------------------------------------------


def blend_trend_composite(
    dimensions: Sequence[TrendDimension],
) -> tuple[float | None, tuple[str, ...]]:
    """Weighted blend over available dimensions only, renormalized so the available weights sum
    to 1.0 — mirrors `analytics.score.blend_composite` exactly. Returns
    `(composite_or_None, names_of_unavailable_dimensions)`."""
    available = [d for d in dimensions if d.value is not None]
    unavailable = tuple(d.name for d in dimensions if d.value is None)
    if not available:
        return None, unavailable
    total_weight = sum(DIMENSION_WEIGHTS[d.name] for d in available)
    if total_weight <= 0:
        return None, unavailable
    weighted_total = sum(DIMENSION_WEIGHTS[d.name] * (d.value or 0.0) for d in available)
    composite = weighted_total / total_weight
    return min(max(composite, 0.0), 100.0), unavailable


__all__ = [
    "CHIP_WEIGHT",
    "CONCENTRATION_WEIGHT",
    "DIMENSION_WEIGHTS",
    "MARGIN_WEIGHT",
    "ROTATION_WEIGHT",
    "TAIEX_CORR_WEIGHT",
    "TECH_WEIGHT",
    "TrendDimension",
    "blend_trend_composite",
    "broker_concentration_trend_score",
    "chip_flow_trend_score",
    "concentration_days_pct",
    "margin_credit_trend_score",
    "sector_rotation_trend_score",
    "taiex_correlation_score",
    "technical_trend_score",
]
