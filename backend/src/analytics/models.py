"""Frozen result models for the derived-compute layer: indicator series + the multi-factor
descriptive score. Every model is immutable, carries only normalized numeric fields, and (for
the score models) is shaped so it cannot be mistaken for a trading instruction — field names
describe *what the factors currently show*, never an action.
"""

from __future__ import annotations

from datetime import date as date_
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

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
    """MA5/10/20/60 together — 均線排列 (the moving-average stack) is read as a set, not one
    line at a time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ma5: SmaResult
    ma10: SmaResult
    ma20: SmaResult
    ma60: SmaResult


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


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    momentum: FactorScore
    trend_quality: FactorScore
    value_guard: FactorScore
    quality: FactorScore
    revenue_flow: FactorScore


class ScoreResult(BaseModel):
    """The composite's full, transparent output: the number, the band, and the factor
    breakdown behind it — the number is never surfaced without its basis (governance's "show
    the work" rule). Nothing here is, or may become, buy/sell/hold/price-target/position-sizing
    language."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    as_of: date_
    profile: ScoreProfile
    composite: float | None = Field(default=None, ge=0.0, le=100.0)
    band: ScoreBand
    breakdown: ScoreBreakdown
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


__all__ = [
    "AtrResult",
    "BollingerResult",
    "EmaResult",
    "FactorScore",
    "KdResult",
    "MacdResult",
    "MovingAverageBundle",
    "ObvResult",
    "RsiResult",
    "ScoreBand",
    "ScoreBreakdown",
    "ScoreProfile",
    "ScoreResult",
    "SmaResult",
    "VwapResult",
]
