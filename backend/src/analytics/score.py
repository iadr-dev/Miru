"""The lean multi-factor descriptive score — pure computation, no LLM.

**Governance framing (load-bearing for this whole module):** the composite and its band are a
*descriptive read of what the factors currently show*, never a trading instruction. No function
here may return, or be renamed to imply, a buy/sell/hold word, a price target, or a position
size. `ScoreResult`/`ScoreBand`/`FactorScore` (analytics/models.py) are the only shapes this
module produces, and none of their fields can hold that kind of value.

**Factor coverage this pass** (five-factor design):

- **Momentum / RS** — computable; `price_eod` is ingested daily. Measured as the verified 12-1
  convention: the trailing twelve-month total return excluding the most recent month, so a
  short-term reversal in the latest weeks does not contaminate the intermediate-term signal.
- **Trend quality** — computable; a Minervini-style trend-template count over `price_eod`.
- **Value guard** — computable when the financial-statement + market-cap datasets are ingested
  for the symbol: EV/EBITDA and FCF yield (primary), P/E and P/B (secondary). Each sub-metric
  degrades independently; the factor is unavailable only if none can be assembled.
- **Quality** — computable when the financial-statement datasets are ingested: ROE, debt/equity
  (a penalty direction), earnings variability (a penalty direction), plus ROIC and gross
  profitability where the more data-hungry line items are present. Sub-metric-by-sub-metric
  graceful degradation, same convention.
- **Revenue / flow (TW)** — partially available. The 三大法人 sub-signal is computable from
  `institutional_flow`; the 月營收 YoY sub-signal is unavailable (monthly revenue isn't ingested
  yet). The factor is reported from the chip sub-signal alone, with that gap disclosed in its
  description — never silently backfilled.

**Composite construction.** Momentum and the chip sub-signal rank cross-sectionally as winsorized
percentiles; value and quality are built the way real factor products build composites —
winsorize each raw metric, z-score it against a comparison universe (sector peers where an
ingested sector classification exists, else the whole universe with that disclosed), equal-weight
the available sub-metric z-scores within the factor, and map the combined z-score onto the same
0-100 axis the blend already speaks.

An unavailable factor is `FactorScore(percentile=None, unavailable_reason=...)` — it is dropped
from the composite blend (weights renormalized over the factors that *are* available) and its
name is listed in `ScoreResult.partial_factors`, never defaulted to a fake neutral value that
would silently bias the blend one way or the other.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date as date_

from analytics.fundamentals import FundamentalMetrics
from analytics.models import (
    FactorScore,
    ScoreBand,
    ScoreBreakdown,
    ScoreProfile,
    ScoreResult,
)
from analytics.normalization import combine_zscores, zscore, zscore_to_percentile
from schemas.capabilities import EODBar, InstitutionalFlowRow
from schemas.enums import Market

_NON_TRADING_CAVEAT = (
    "Descriptive factor analysis only — not a buy, sell, hold, or price recommendation; "
    "computed at last post-close and may not reflect intraday moves."
)
_REGIME_CAVEAT_NO_MARKET_DATA = (
    "Market-regime context (e.g. TAIEX vs its 200-day trend) is not evaluated in this pass — "
    "treat any momentum-driven factor with the same elevated reversal risk you would in any "
    "unassessed market condition."
)

# Horizon-aware weight profiles — weights sum to 1.0 within each profile before any
# renormalization for missing factors.
_WEIGHTS: dict[ScoreProfile, dict[str, float]] = {
    ScoreProfile.SWING: {
        "momentum": 0.35,
        "trend_quality": 0.25,
        "value_guard": 0.10,
        "quality": 0.10,
        "revenue_flow": 0.20,
    },
    ScoreProfile.POSITION: {
        "momentum": 0.15,
        "trend_quality": 0.15,
        "value_guard": 0.25,
        "quality": 0.30,
        "revenue_flow": 0.15,
    },
}

# 12-1 momentum: the trailing twelve-month total return measured up to one month ago, excluding
# the most recent month. Both windows are in trading days (~21/month, ~252/year) — the verified
# academic convention that avoids the short-term reversal contaminating the most recent weeks.
_MOMENTUM_LOOKBACK_DAYS = 252  # ~12 months — the far anchor
_MOMENTUM_SKIP_MONTH_DAYS = 21  # ~1 trading month excluded — the near anchor
_TREND_TEMPLATE_LOOKBACK = 252  # ~52 weeks, needed for the 52w-high/low + MA200-slope conditions
_TREND_TEMPLATE_MONTH_LOOKBACK = 22  # ~1 trading month, for the "MA200 rising" condition
_OVER_EXTENSION_THRESHOLD = 0.20  # > ~20% above the 50-day MA
_OVER_EXTENSION_CAP = 70.0  # momentum percentile is capped, not zeroed, when over-extended
_WINSORIZE_SIGMA = 3.0
_MIN_CROSS_SECTION_SIZE = 2  # smallest universe size a percentile rank is meaningful over
_FIFTY_TWO_WEEK_LOW_MULTIPLE = 1.30  # >= 30% above the 52-week low
_FIFTY_TWO_WEEK_HIGH_MULTIPLE = 0.75  # <= 25% below the 52-week high
_RS_PERCENTILE_THRESHOLD = 70.0  # trend-template condition #8: RS percentile >= 70
_TREND_TEMPLATE_CONDITION_COUNT = 8.0
_BULLISH_LEANING_THRESHOLD = 60.0
_BEARISH_LEANING_THRESHOLD = 40.0


def twelve_minus_one_momentum(closes: Sequence[float]) -> float | None:
    """The 12-1 momentum: total return from ~12 months ago to ~1 month ago (the most recent
    month excluded to avoid short-term-reversal contamination). `None` if there isn't a full
    twelve months of history — never a partial-window estimate silently swapped in."""
    required = _MOMENTUM_LOOKBACK_DAYS + 1
    if len(closes) < required:
        return None
    near = closes[-1 - _MOMENTUM_SKIP_MONTH_DAYS]
    far = closes[-1 - _MOMENTUM_LOOKBACK_DAYS]
    if far == 0:
        return None
    return (near - far) / far


def trend_template_score(
    closes: Sequence[float],
    ma_series: Mapping[str, Sequence[float | None]],
    *,
    rs_percentile: float | None = None,
) -> int | None:
    """Count of the Minervini trend-template conditions met (0-8). `ma_series` must contain
    `"ma50"`, `"ma150"`, `"ma200"` aligned index-for-index with `closes`. `None` if there isn't
    enough history for the 52-week conditions. The RS condition (#8) is counted as *not met*
    (not "unavailable") when `rs_percentile` isn't supplied — a conservative, disclosed choice,
    never an inflated count."""
    if len(closes) < _TREND_TEMPLATE_LOOKBACK:
        return None
    ma50 = ma_series["ma50"][-1]
    ma150 = ma_series["ma150"][-1]
    ma200 = ma_series["ma200"][-1]
    ma200_prior = (
        ma_series["ma200"][-_TREND_TEMPLATE_MONTH_LOOKBACK]
        if len(ma_series["ma200"]) >= _TREND_TEMPLATE_MONTH_LOOKBACK
        else None
    )
    if ma50 is None or ma150 is None or ma200 is None or ma200_prior is None:
        return None

    price = closes[-1]
    window = closes[-_TREND_TEMPLATE_LOOKBACK:]
    fifty_two_week_low = min(window)
    fifty_two_week_high = max(window)

    conditions = [
        price > ma150 and price > ma200,
        ma150 > ma200,
        ma200 > ma200_prior,
        ma50 > ma150 and ma50 > ma200,
        price > ma50,
        fifty_two_week_low > 0 and price >= _FIFTY_TWO_WEEK_LOW_MULTIPLE * fifty_two_week_low,
        fifty_two_week_high > 0 and price >= _FIFTY_TWO_WEEK_HIGH_MULTIPLE * fifty_two_week_high,
        rs_percentile is not None and rs_percentile >= _RS_PERCENTILE_THRESHOLD,
    ]
    return sum(1 for met in conditions if met)


def net_buy_20d_trend(
    flow_rows: Sequence[InstitutionalFlowRow],
    *,
    investors: tuple[str, ...] = ("Foreign_Investor", "Investment_Trust"),
) -> float | None:
    """Sum of (buy - sell) for the given investor categories over whatever trading days are
    present in `flow_rows` (callers pass the trailing-20-trading-day slice). `None` if no rows
    at all are present for this symbol/window — genuinely no data, not a zero."""
    matching = [row for row in flow_rows if row.investor in investors]
    if not matching:
        return None
    return float(sum(row.buy - row.sell for row in matching))


def winsorized_percentile(value: float, universe: Sequence[float]) -> float | None:
    """Cross-sectional percentile rank of `value` within `universe`, winsorizing the universe
    first (clip to +/- 3 standard deviations) so a single fat-tailed outlier can't compress
    everyone else's rank. `None` if the universe is too small for a meaningful cross-section."""
    if len(universe) < _MIN_CROSS_SECTION_SIZE:
        return None
    mean = statistics.fmean(universe)
    stdev = statistics.pstdev(universe)
    if stdev == 0:
        return 50.0
    low, high = mean - _WINSORIZE_SIGMA * stdev, mean + _WINSORIZE_SIGMA * stdev
    clipped = [min(max(v, low), high) for v in universe]
    clipped_value = min(max(value, low), high)
    rank = sum(1 for v in clipped if v <= clipped_value)
    return 100.0 * rank / len(clipped)


def apply_over_extension_cap(
    momentum_percentile: float | None,
    *,
    price: float | None,
    ma50: float | None,
) -> tuple[float | None, bool]:
    """A stock trading far above its 50-day MA has its momentum contribution capped rather than
    left to rank at the very top — a naive momentum score ranks the most stretched names
    highest, right before mean-reversion. Returns `(possibly-capped percentile, was_capped)`."""
    if momentum_percentile is None or price is None or ma50 is None or ma50 <= 0:
        return momentum_percentile, False
    extension = (price - ma50) / ma50
    if extension > _OVER_EXTENSION_THRESHOLD and momentum_percentile > _OVER_EXTENSION_CAP:
        return _OVER_EXTENSION_CAP, True
    return momentum_percentile, False


def band_from_composite(composite: float | None) -> ScoreBand:
    """Map the composite to a descriptive band (bullish-leaning / neutral / bearish-leaning) —
    never to an action word."""
    if composite is None:
        return ScoreBand.NEUTRAL
    if composite >= _BULLISH_LEANING_THRESHOLD:
        return ScoreBand.BULLISH_LEANING
    if composite <= _BEARISH_LEANING_THRESHOLD:
        return ScoreBand.BEARISH_LEANING
    return ScoreBand.NEUTRAL


def blend_composite(
    breakdown: ScoreBreakdown, profile: ScoreProfile
) -> tuple[float | None, tuple[str, ...]]:
    """Weighted blend over *available* factors only, renormalized so the available weights sum
    to 1.0 — computed over whichever factors are present, with the gap always disclosed rather
    than silently degraded. Returns `(composite_or_None, names_of_unavailable_factors)`."""
    weights = _WEIGHTS[profile]
    factors = {
        "momentum": breakdown.momentum,
        "trend_quality": breakdown.trend_quality,
        "value_guard": breakdown.value_guard,
        "quality": breakdown.quality,
        "revenue_flow": breakdown.revenue_flow,
    }
    available = {name: f for name, f in factors.items() if f.percentile is not None}
    partial = tuple(name for name in factors if name not in available)
    if not available:
        return None, partial

    total_weight = sum(weights[name] for name in available)
    if total_weight <= 0:
        return None, partial

    weighted_total = 0.0
    for name, factor in available.items():
        percentile = factor.percentile
        if percentile is None:  # pragma: no cover — excluded by the `available` filter above
            continue
        weighted_total += weights[name] * percentile
    composite = weighted_total / total_weight
    # A weighted average of values each already clamped to [0, 100] cannot mathematically exceed
    # that range, but summing several float weight*percentile terms can land a hair outside it
    # (e.g. 100.00000000000001) from ordinary floating-point representation error — clip rather
    # than let that render as a validation failure on `ScoreResult.composite`'s `le=100` bound.
    composite = min(max(composite, 0.0), 100.0)
    return composite, partial


def normalization_caveat(
    *, sector_relative: bool, sector_label: str | None, has_fundamentals: bool
) -> str:
    """Disclose exactly how the fundamental factors were standardized — sector-relative when an
    ingested classification placed the symbol among peers, whole-universe (explicitly coarser)
    when none was available, and a plain no-comparison note when no fundamental universe was
    supplied at all. Never silently presents a whole-universe z-score as sector-relative."""
    if not has_fundamentals:
        return _NO_FUNDAMENTAL_NORMALIZATION_CAVEAT
    if sector_relative and sector_label:
        return _SECTOR_RELATIVE_CAVEAT.format(sector=sector_label)
    return _WHOLE_UNIVERSE_CAVEAT


@dataclass(frozen=True)
class SectorContext:
    """How the fundamental factors were normalized for this run — whether the comparison universe
    was sector peers and, if so, which sector. Defaults describe the whole-universe fallback."""

    sector_relative: bool = False
    sector_label: str | None = None


def build_score_result(  # noqa: PLR0913 — one keyword-only field per result dimension it stitches
    *,
    symbol: str,
    market: Market,
    as_of: date_,
    profile: ScoreProfile,
    breakdown: ScoreBreakdown,
    sector_context: SectorContext | None = None,
) -> ScoreResult:
    """Assemble the final, fully-caveated `ScoreResult` — the one place composite + band +
    caveats are stitched together, so no call site can accidentally ship one without the
    others."""
    composite, partial_factors = blend_composite(breakdown, profile)
    band = band_from_composite(composite)
    has_fundamentals = (
        breakdown.value_guard.percentile is not None or breakdown.quality.percentile is not None
    )
    sector = sector_context or SectorContext()
    return ScoreResult(
        symbol=symbol,
        market=market,
        as_of=as_of,
        profile=profile,
        composite=composite,
        band=band,
        breakdown=breakdown,
        partial_factors=partial_factors,
        regime_caveat=_REGIME_CAVEAT_NO_MARKET_DATA,
        non_trading_caveat=_NON_TRADING_CAVEAT,
        normalization_caveat=normalization_caveat(
            sector_relative=sector.sector_relative,
            sector_label=sector.sector_label,
            has_fundamentals=has_fundamentals,
        ),
    )


def unavailable_factor(name: str, reason: str) -> FactorScore:
    """A factor that is genuinely not computable — explicit and reasoned, never a fabricated
    neutral placeholder."""
    return FactorScore(
        name=name,
        percentile=None,
        description=f"{name} is unavailable for this symbol/date.",
        unavailable_reason=reason,
    )


_VALUE_GUARD_UNAVAILABLE_REASON = (
    "No value sub-metric (EV/EBITDA, FCF yield, P/E, P/B) could be assembled — the financial-"
    "statement and/or market-capitalization line items this symbol needs are absent from the "
    "derived store; no value-guard figure is fabricated in its place."
)
_QUALITY_UNAVAILABLE_REASON = (
    "No quality sub-metric (ROE, debt/equity, earnings variability, ROIC, gross profitability) "
    "could be assembled — this symbol's financial-statement line items are absent from the "
    "derived store; no quality figure is fabricated in its place."
)


@dataclass(frozen=True)
class _SubMetric:
    """One fundamental sub-metric's contribution to a z-scored factor: the attribute on
    `FundamentalMetrics` to read, whether a higher raw value is stronger (penalty-direction
    metrics like debt/equity, EV/EBITDA, and earnings variability set this `False`), and the
    label surfaced in the factor description."""

    attr: str
    higher_is_better: bool
    label: str


# Equal-weight within each factor (the standard within-factor combination). Sign encodes
# direction so a lower-is-better metric still pushes a strong factor score upward.
_QUALITY_SUB_METRICS: tuple[_SubMetric, ...] = (
    _SubMetric("roe", higher_is_better=True, label="ROE"),
    _SubMetric("debt_to_equity", higher_is_better=False, label="debt/equity"),
    _SubMetric("earnings_variability", higher_is_better=False, label="earnings variability"),
    _SubMetric("roic", higher_is_better=True, label="ROIC"),
    _SubMetric("gross_profitability", higher_is_better=True, label="gross profitability"),
)
_VALUE_SUB_METRICS: tuple[_SubMetric, ...] = (
    _SubMetric("ev_ebitda", higher_is_better=False, label="EV/EBITDA"),
    _SubMetric("fcf_yield", higher_is_better=True, label="FCF yield"),
    _SubMetric("pe", higher_is_better=False, label="P/E"),
    _SubMetric("pb", higher_is_better=False, label="P/B"),
)

_SECTOR_RELATIVE_CAVEAT = "Value and quality factors are z-scored against sector peers ({sector})."
_WHOLE_UNIVERSE_CAVEAT = (
    "No ingested sector/industry classification was available, so value and quality factors are "
    "z-scored against the whole comparison universe rather than sector peers — a coarser "
    "normalization, disclosed here rather than presented as sector-relative."
)
_NO_FUNDAMENTAL_NORMALIZATION_CAVEAT = (
    "Value and quality were not normalized cross-sectionally — no fundamental comparison universe "
    "was supplied for this run."
)


@dataclass(frozen=True)
class FactorInputs:
    """The raw, already-computed per-symbol series + cross-sectional universes
    `compute_factor_breakdown` needs — bundled into one value so the function itself stays a
    single-parameter call, no I/O, no store dependency. A caller (typically an async
    orchestrator that read the derived Parquet store) is responsible for assembling this.

    `fundamentals` is this symbol's raw fundamental metrics; `quality_universe`/`value_universe`
    map each sub-metric name to the comparison universe's raw values for that sub-metric (sector
    peers where `sector_relative`, else the whole universe). All default empty/None so a
    price-only caller degrades value/quality to unavailable exactly as before."""

    closes: Sequence[float]
    ma_series: Mapping[str, Sequence[float | None]]
    momentum_universe: Sequence[float]
    flow_value: float | None
    flow_universe: Sequence[float]
    price: float | None
    ma50: float | None
    fundamentals: FundamentalMetrics | None = None
    quality_universe: Mapping[str, Sequence[float]] = field(default_factory=lambda: {})
    value_universe: Mapping[str, Sequence[float]] = field(default_factory=lambda: {})
    sector_relative: bool = False
    sector_label: str | None = None


def _fundamental_factor(
    name: str,
    metrics: FundamentalMetrics | None,
    sub_metrics: Sequence[_SubMetric],
    universe: Mapping[str, Sequence[float]],
    unavailable_reason: str,
) -> FactorScore:
    """Build one z-scored fundamental factor: winsorize + z-score each present sub-metric against
    its comparison universe, equal-weight-combine the available ones, map to 0-100. Each
    sub-metric that this symbol lacks (or that has too small a universe) is simply left out — the
    factor degrades sub-metric-by-sub-metric, and is unavailable only if none survive."""
    if metrics is None:
        return unavailable_factor(name, unavailable_reason)
    sub_scores: list[float | None] = []
    used: list[str] = []
    for sub in sub_metrics:
        raw = getattr(metrics, sub.attr)
        if raw is None:
            continue
        z = zscore(raw, universe.get(sub.attr, ()), higher_is_better=sub.higher_is_better)
        if z is None:
            continue
        sub_scores.append(z)
        used.append(sub.label)
    combined = combine_zscores(sub_scores)
    if combined is None:
        return unavailable_factor(name, unavailable_reason)
    annualized_note = (
        " (trailing figures rest on an annualized single quarter — full TTM not yet available)"
        if metrics.ttm_is_annualized
        else ""
    )
    return FactorScore(
        name=name,
        percentile=zscore_to_percentile(combined),
        description=(
            f"z-scored composite of {', '.join(used)}, mapped to a 0-100 cross-sectional "
            f"standing{annualized_note}"
        ),
    )


def compute_factor_breakdown(inputs: FactorInputs) -> ScoreBreakdown:
    """Build the five-factor breakdown from `inputs`. Pure computation — no I/O, no dependency
    on the ingest store."""
    closes = inputs.closes
    ma_series = inputs.ma_series
    raw_momentum = twelve_minus_one_momentum(closes)
    momentum_percentile = (
        winsorized_percentile(raw_momentum, inputs.momentum_universe)
        if raw_momentum is not None
        else None
    )
    momentum_percentile, was_capped = apply_over_extension_cap(
        momentum_percentile, price=inputs.price, ma50=inputs.ma50
    )
    momentum = (
        FactorScore(
            name="momentum",
            percentile=momentum_percentile,
            description=(
                "12-1 momentum (trailing 12-month return excluding the most recent month), "
                "ranked against the day's universe"
                + (" — capped for over-extension vs the 50-day MA (乖離過大)" if was_capped else "")
            ),
        )
        if momentum_percentile is not None
        else unavailable_factor(
            "momentum", "fewer than 12 months of price history ingested for this symbol"
        )
    )

    raw_trend = trend_template_score(closes, ma_series, rs_percentile=momentum_percentile)
    trend_quality = (
        FactorScore(
            name="trend_quality",
            percentile=(raw_trend / _TREND_TEMPLATE_CONDITION_COUNT) * 100.0,
            description=f"{raw_trend}/8 Minervini trend-template conditions met",
        )
        if raw_trend is not None
        else unavailable_factor(
            "trend_quality", "fewer than 252 trading days of price history ingested for this symbol"
        )
    )

    value_guard = _fundamental_factor(
        "value_guard",
        inputs.fundamentals,
        _VALUE_SUB_METRICS,
        inputs.value_universe,
        _VALUE_GUARD_UNAVAILABLE_REASON,
    )
    quality = _fundamental_factor(
        "quality",
        inputs.fundamentals,
        _QUALITY_SUB_METRICS,
        inputs.quality_universe,
        _QUALITY_UNAVAILABLE_REASON,
    )

    flow_percentile = (
        winsorized_percentile(inputs.flow_value, inputs.flow_universe)
        if inputs.flow_value is not None
        else None
    )
    revenue_flow = (
        FactorScore(
            name="revenue_flow",
            percentile=flow_percentile,
            description=(
                "三大法人 20-day net-buy trend percentile only — 月營收 YoY sub-signal not yet "
                "ingested, so this factor reflects the chip-flow half of the design, disclosed here"
            ),
        )
        if flow_percentile is not None
        else unavailable_factor(
            "revenue_flow", "no institutional-flow rows ingested for this symbol/window"
        )
    )

    return ScoreBreakdown(
        momentum=momentum,
        trend_quality=trend_quality,
        value_guard=value_guard,
        quality=quality,
        revenue_flow=revenue_flow,
    )


def eod_bars_to_closes(bars: Sequence[EODBar]) -> list[float]:
    """Bars must already be sorted ascending by `trade_date` — this is a pure reshape, not a
    sort, matching `analytics.indicators`'s "caller is responsible for ordering" contract."""
    return [bar.close for bar in bars]


__all__ = [
    "FactorInputs",
    "SectorContext",
    "apply_over_extension_cap",
    "band_from_composite",
    "blend_composite",
    "build_score_result",
    "compute_factor_breakdown",
    "eod_bars_to_closes",
    "net_buy_20d_trend",
    "normalization_caveat",
    "trend_template_score",
    "twelve_minus_one_momentum",
    "unavailable_factor",
    "winsorized_percentile",
]
