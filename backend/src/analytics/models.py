"""Frozen result models for the derived-compute layer: indicator series + the multi-factor
descriptive score. Every model is immutable, carries only normalized numeric fields, and (for
the score models) is shaped so it cannot be mistaken for a trading instruction — field names
describe *what the factors currently show*, never an action.
"""

from __future__ import annotations

from datetime import date as date_
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from analytics.symbol_universe import SymbolType
from schemas.enums import Market

# -- indicator series -----------------------------------------------------------------------


class SmaResult(BaseModel):
    """Simple moving average. `values[i] is None` for `i` inside the warm-up window (fewer
    than `window` bars seen yet) — never a fabricated number standing in for "not enough
    history"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    window: int = Field(ge=1)
    values: tuple[float | None, ...]


class EmaResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    window: int = Field(ge=1)
    values: tuple[float | None, ...]


class MovingAverageBundle(BaseModel):
    """MA5/10/20/60/120/240 together — 均線排列 (the moving-average stack) is read as a set, not
    one line at a time. MA120/240 were added for the K線 chart's overlay chips
    (`docs/web/stock-detail/技術分析.md`); the score/screener callers that predate them only ever
    read `ma5..ma60`, so their existing behavior is unaffected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ma5: SmaResult
    ma10: SmaResult
    ma20: SmaResult
    ma60: SmaResult
    ma120: SmaResult
    ma240: SmaResult


class RsiResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    period: int = Field(ge=1)
    values: tuple[float | None, ...]


class KdResult(BaseModel):
    """Stochastic oscillator %K / %D. Both series are `None` for `i` inside the warm-up window
    (fewer than `period` bars seen yet) — never a fabricated number standing in for "not enough
    history". `k`/`d` share the same length and warm-up prefix."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: int = Field(ge=1)
    k: tuple[float | None, ...]
    d: tuple[float | None, ...]


class MacdResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dif: tuple[float | None, ...]
    dem: tuple[float | None, ...]
    histogram: tuple[float | None, ...]


class BollingerResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    window: int = Field(ge=1)
    mid: tuple[float | None, ...]
    upper: tuple[float | None, ...]
    lower: tuple[float | None, ...]


class AtrResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    period: int = Field(ge=1)
    values: tuple[float | None, ...]


class ObvResult(BaseModel):
    """On-balance volume — always defined from bar 0 (no warm-up window), so no `None` entries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: tuple[float, ...]


class VwapResult(BaseModel):
    """A same-day `turnover / volume` proxy (falling back to the typical price when turnover
    is absent) — not the textbook intraday-cumulative VWAP, which this EOD-only store cannot
    compute. See `analytics/indicators.py` docstring for the exact fallback rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: tuple[float | None, ...]


class SupportResistanceLevel(BaseModel):
    """One 支撐/壓力 price level — a swing pivot's price plus which bar index it pivoted at, so
    a caller can place the level's h-line/label from `bar_index` alone rather than re-deriving
    an offset from `len(bars)`. `kind` is a plain descriptive label ("support"/"resistance"),
    never a trading instruction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    price: float
    bar_index: int = Field(ge=0)


class SupportResistanceResult(BaseModel):
    """支撐/壓力 levels detected over the supplied bar window — see
    `analytics/structure.py::support_resistance_levels` for the swing-pivot detection rule.
    Levels are ordered by `bar_index` ascending (oldest pivot first)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    levels: tuple[SupportResistanceLevel, ...] = Field(default_factory=tuple)


class CandlestickPatternHit(BaseModel):
    """One completed candlestick-pattern match (`analytics/patterns.py`) at a given bar.
    `direction` is a plain descriptive label — `+1` (看多/bullish-leaning), `-1`
    (看空/bearish-leaning), or `0` (doji — no directional read) — never a buy/sell instruction
    (governance §7 non-trading)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bar_index: int = Field(ge=0)
    pattern: str
    direction: int = Field(ge=-1, le=1)


# -- multi-factor descriptive score ----------------------------------------------------------


class ScoreProfile(StrEnum):
    """Which of the two horizon-aware weight profiles produced this score."""

    SWING = "swing"
    POSITION = "position"


class ScoreBand(StrEnum):
    """A descriptive label of what the current factor mix shows — never an instruction. No
    band value may ever be a buy/sell/hold word; this enum is the single place that mapping is
    defined, so it can't drift per call site."""

    BULLISH_LEANING = "偏多"
    NEUTRAL = "中性"
    BEARISH_LEANING = "偏空"


class FactorScore(BaseModel):
    """One factor's contribution to the composite. `percentile is None` means the factor is
    genuinely unavailable for this symbol/date (its source dataset isn't ingested yet, or
    there isn't enough history) — it is never defaulted to a fake neutral value that would
    silently bias the blend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    percentile: float | None = Field(default=None, ge=0.0, le=100.0)
    description: str = Field(min_length=1)
    unavailable_reason: str | None = None


class StockScoreBreakdown(BaseModel):
    """The 5-factor breakdown for a plain equity. Company-fundamentals-based (value/quality) and
    price-technical (momentum/trend) factors — meaningless for an ETF, which has no earnings/
    balance sheet of its own (see `EtfScoreBreakdown` for the ETF-specific factor set)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    momentum: FactorScore
    trend_quality: FactorScore
    value_guard: FactorScore
    quality: FactorScore
    revenue_flow: FactorScore


class UsStockScoreBreakdown(BaseModel):
    """The US-market counterpart to `StockScoreBreakdown` — same `momentum`/`trend_quality`/
    `value_guard`/`quality` factors (already market-agnostic, computed straight from price/
    financials), but `analyst_revision` in place of `revenue_flow`: US has no 三大法人
    institutional-flow/月營收 equivalent ingested anywhere, so rather than let that TW-shaped slot
    degrade to permanently unavailable, the fifth factor is a genuinely US-native signal — the
    analyst estimate-revision factor (IBES/Zacks-style: EPS-trend delta + revision breadth from
    `AnalystConsensus`), a well-documented growth/sentiment factor with no TW equivalent either
    (TW's `AnalystConsensus` payload never populates the `_extended` EPS-trend fields this factor
    reads). A separate model (not a repurposed `revenue_flow` field) so the two breakdowns can
    never be confused for one another at the type level.

    A sixth factor, `growth`, rounds out the standard "Value + Momentum + Quality + Growth"
    long-short combo (the longest-track-record practitioner factor set): forward EPS/revenue
    growth estimates (`AnalystConsensus.current_year_growth_estimate`/`next_year_growth_estimate`)
    blended with analyst price-target upside (`mean_target` vs. the symbol's own current price) —
    both already-ingested `AnalystConsensus` fields with no TW equivalent, so `growth` has no
    TW-side counterpart either, same as `analyst_revision`.

    A seventh factor, `liquidity`, is the standard Size/Liquidity risk-overlay factor (average
    daily dollar volume — price x average share volume, computed straight from already-ingested
    `us_price_eod`, ranked cross-sectionally): a real, professionally-recognized US equity factor
    with no TW equivalent slot to displace (TW's `StockScoreBreakdown` has never modeled
    liquidity as its own factor), so it is purely additive rather than replacing anything."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    momentum: FactorScore
    trend_quality: FactorScore
    value_guard: FactorScore
    quality: FactorScore
    analyst_revision: FactorScore
    growth: FactorScore
    liquidity: FactorScore


class EtfScoreBreakdown(BaseModel):
    """The 4-factor breakdown for an ETF — deliberately NOT `StockScoreBreakdown`'s value/
    quality/trend-template factors, which are either meaningless (an ETF has no ROE/EV-EBITDA of
    its own) or actively misleading (the Minervini trend template misreads a stretched
    leveraged/inverse ETF as bullish exactly when its daily-rebalancing decay risk peaks) for a
    fund rather than an operating company.

    - `momentum`: same 12-1 calculation as the stock breakdown — meaningful as fund performance.
    - `premium_discount`: TWSE's own published 預估折溢價幅度 (estimated premium/discount to
      NAV) — the textbook ETF-quality metric with no stock equivalent.
    - `cost`: SITCA-disclosed expense ratio percentile (lower cost ranks higher) — every ETF has
      one; no stock does.
    - `management_activity`: holdings-turnover-derived, ACTIVE ETFs only — genuinely unavailable
      (not fabricated) for passive/leveraged/inverse ETFs, which have no holdings data ingested.
    - `liquidity_flow`: 三大法人 institutional net-buy trend — the same chip sub-signal the stock
      breakdown's `revenue_flow` uses, without the (ETF-inapplicable) 月營收 half.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    momentum: FactorScore
    premium_discount: FactorScore
    cost: FactorScore
    management_activity: FactorScore
    liquidity_flow: FactorScore


class ScoreResult(BaseModel):
    """The composite's full, transparent output: the number, the band, and the factor
    breakdown behind it — the number is never surfaced without its basis (governance's "show
    the work" rule). Nothing here is, or may become, buy/sell/hold/price-target/position-sizing
    language.

    `breakdown` is `StockScoreBreakdown` for a plain equity, `EtfScoreBreakdown` for any ETF
    (plain/active/leveraged/inverse) — the two shapes have different factor names by design
    (see each breakdown's own docstring for why the factor sets genuinely differ, not just a
    naming variation). Callers branch on `isinstance(result.breakdown, EtfScoreBreakdown)` (or
    check `symbol_type`) rather than assuming one fixed shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    profile: ScoreProfile
    symbol_type: SymbolType
    composite: float | None = Field(default=None, ge=0.0, le=100.0)
    band: ScoreBand
    breakdown: StockScoreBreakdown | UsStockScoreBreakdown | EtfScoreBreakdown
    partial_factors: tuple[str, ...] = Field(
        default=(),
        description="Names of factors that were unavailable and excluded from the composite — "
        "disclosed explicitly, never a silent degradation.",
    )
    regime_caveat: str = Field(min_length=1)
    non_trading_caveat: str = Field(min_length=1)
    normalization_caveat: str = Field(
        min_length=1,
        description="How the cross-sectional factors were standardized — in particular whether "
        "value/quality were compared against sector peers or, absent an ingested sector "
        "classification, the whole universe (disclosed, never silently presented as "
        "sector-relative).",
    )


class StockSwot(BaseModel):
    """The four SWOT quadrants for the AI-generated stock summary — every bullet is grounded in
    one of `ScoreResult.breakdown`'s factors (see `analytics/summary_generation.py`'s prompt),
    never fabricated beyond what the score already discloses. Any quadrant may be empty (e.g. no
    factor supported a "threats" bullet that day) — an empty tuple, never a filler sentence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strengths: tuple[str, ...] = Field(default=())
    weaknesses: tuple[str, ...] = Field(default=())
    opportunities: tuple[str, ...] = Field(default=())
    threats: tuple[str, ...] = Field(default=())


class StockSummaryResult(BaseModel):
    """The AI-generated headline + narrative + SWOT for one symbol, strictly grounded in the
    `ScoreResult` it was generated from — `as_of` is that score's own date (the cache key), not
    "today", so a summary generated against yesterday's still-cached score is never silently
    treated as freshly computed. Like `ScoreResult`, nothing here is, or may become, buy/sell/
    hold/price-target/position-sizing language (enforced both by the generation prompt and by
    `agent.guardrails.scan_structured_payload` before this model is constructed)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    headline: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    swot: StockSwot
    non_trading_caveat: str = Field(min_length=1)


# -- 趨勢評分 (trend score) result models -----------------------------------------------------


class TrendDimensionResult(BaseModel):
    """Frozen pydantic mirror of `analytics.trend_score.TrendDimension` (a plain dataclass there
    by design — that module is pure computation with zero pydantic dependency). Same "never
    fabricate, always disclose" shape as `FactorScore`: `value is None` means genuinely
    unavailable (data not ingested / not enough history), never a fabricated neutral default."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: float | None = Field(default=None, ge=0.0, le=100.0)
    description: str = Field(min_length=1)
    unavailable_reason: str | None = None


class TrendScoreResult(BaseModel):
    """The 趨勢評分 composite's full, transparent output — the number, the six-dimension
    breakdown behind it, and which dimensions were unavailable. Mirrors `ScoreResult`'s "show the
    work" discipline; nothing here is, or may become, buy/sell/hold/price-target language.

    Unlike `ScoreResult` (a same-day snapshot cache), this table's read pattern includes a date
    RANGE for a chart (see `analytics/trend_score_store.py::get_trend_score_history`), so
    `TrendScoreResult` rows persist as real history rather than being overwritten daily — the
    same symbol's `as_of` genuinely differs day to day, unlike `ScoreResult`'s single-latest-row
    cache."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    composite: float | None = Field(default=None, ge=0.0, le=100.0)
    dimensions: tuple[TrendDimensionResult, ...] = Field(default_factory=tuple)
    unavailable_dimensions: tuple[str, ...] = Field(default=())
    non_trading_caveat: str = Field(min_length=1)


__all__ = [
    "AtrResult",
    "BollingerResult",
    "CandlestickPatternHit",
    "EmaResult",
    "EtfScoreBreakdown",
    "FactorScore",
    "KdResult",
    "MacdResult",
    "MovingAverageBundle",
    "ObvResult",
    "RsiResult",
    "ScoreBand",
    "ScoreProfile",
    "ScoreResult",
    "SmaResult",
    "StockScoreBreakdown",
    "StockSummaryResult",
    "StockSwot",
    "SupportResistanceLevel",
    "SupportResistanceResult",
    "TrendDimensionResult",
    "TrendScoreResult",
    "UsStockScoreBreakdown",
    "VwapResult",
]
