"""Platform-wide derived scores and cached AI summaries."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.models._common import MARKET_TW_US_CHECK as _MARKET_TW_US_CHECK
from db.models._common import UTC_TIMESTAMP as _UTC


class StockScoreRow(Base):
    """One symbol's cached daily composite-score result for one horizon profile — the write
    target of the daily scoring batch and the read target of both the per-symbol detail-page
    lookup and the leaderboard. Unlike the theme tables above, this *does* carry a date
    (`as_of`): unlike industry classification, a score is genuinely a new value every trading
    day, so a fresh batch pass replaces (upserts) the prior row for the same
    `(symbol, market, profile)` rather than accumulating history — this table is a same-day
    snapshot cache, not a time series. No `user_id`: the composite is platform-wide, not
    per-user state.

    `symbol_type` discriminates which breakdown shape this row holds (`StockScoreBreakdown` vs
    `EtfScoreBreakdown`, `analytics/models.py`) — a plain equity and an ETF need genuinely
    different factors (an ETF has no ROE/EV-EBITDA of its own), so this table carries BOTH the
    five stock factor columns and the five ETF factor columns (all nullable); a stock row leaves
    the ETF columns null and vice versa. `momentum_*` is the one column name shared by both
    breakdowns (12-1 momentum is meaningful for both a stock and a fund) — every other factor
    name is shape-specific. Flat columns rather than one JSON blob for the same reason the
    original 5-column design chose them: each is independently point-looked-up-shaped, never a
    variable set, so flat columns are simpler and index-free reads faster than unpacking JSON on
    every read. `partial_factors` (a short list of factor names) is a JSON array per this
    project's SQLite/Postgres-portable JSON convention.

    Indexing: `uq_stock_scores_symbol_market_profile_as_of` is the write-side upsert key and also
    serves the detail-page point lookup (`WHERE symbol=? AND market=? AND profile=?` ordered by
    `as_of` desc, or filtered to a specific `as_of`); `ix_stock_scores_leaderboard` serves the
    leaderboard's `WHERE market=? AND profile=? AND as_of=? ORDER BY composite` sort-and-slice
    directly off the index, without a separate sort step.
    """

    __tablename__ = "stock_scores"

    id: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    market: Mapped[str]
    as_of: Mapped[str]  # ISO date string — portable, matches the theme layer's `as_of` convention
    profile: Mapped[str]
    symbol_type: Mapped[str] = mapped_column(default="stock")
    composite: Mapped[float | None] = mapped_column(default=None)
    band: Mapped[str]

    # Shared by both breakdowns — 12-1 momentum is meaningful for a stock AND a fund.
    momentum_percentile: Mapped[float | None] = mapped_column(default=None)
    momentum_description: Mapped[str] = mapped_column(default="")
    momentum_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    # -- Stock-only factors (StockScoreBreakdown) --
    trend_quality_percentile: Mapped[float | None] = mapped_column(default=None)
    trend_quality_description: Mapped[str] = mapped_column(default="")
    trend_quality_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    value_guard_percentile: Mapped[float | None] = mapped_column(default=None)
    value_guard_description: Mapped[str] = mapped_column(default="")
    value_guard_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    quality_percentile: Mapped[float | None] = mapped_column(default=None)
    quality_description: Mapped[str] = mapped_column(default="")
    quality_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    revenue_flow_percentile: Mapped[float | None] = mapped_column(default=None)
    revenue_flow_description: Mapped[str] = mapped_column(default="")
    revenue_flow_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    # -- US-stock-only factor (UsStockScoreBreakdown) — `analyst_revision` replaces `revenue_flow`
    # for Market.US rows (revenue_flow has no US data source; see that model's own docstring).
    # A US row leaves `revenue_flow_*` null and a TW row leaves `analyst_revision_*` null.
    analyst_revision_percentile: Mapped[float | None] = mapped_column(default=None)
    analyst_revision_description: Mapped[str] = mapped_column(default="")
    analyst_revision_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    growth_percentile: Mapped[float | None] = mapped_column(default=None)
    growth_description: Mapped[str] = mapped_column(default="")
    growth_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    liquidity_percentile: Mapped[float | None] = mapped_column(default=None)
    liquidity_description: Mapped[str] = mapped_column(default="")
    liquidity_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    # -- ETF-only factors (EtfScoreBreakdown) --
    premium_discount_percentile: Mapped[float | None] = mapped_column(default=None)
    premium_discount_description: Mapped[str] = mapped_column(default="")
    premium_discount_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    cost_percentile: Mapped[float | None] = mapped_column(default=None)
    cost_description: Mapped[str] = mapped_column(default="")
    cost_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    management_activity_percentile: Mapped[float | None] = mapped_column(default=None)
    management_activity_description: Mapped[str] = mapped_column(default="")
    management_activity_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    liquidity_flow_percentile: Mapped[float | None] = mapped_column(default=None)
    liquidity_flow_description: Mapped[str] = mapped_column(default="")
    liquidity_flow_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    partial_factors: Mapped[list[str]] = mapped_column(JSON, default=list)
    regime_caveat: Mapped[str] = mapped_column(default="")
    non_trading_caveat: Mapped[str] = mapped_column(default="")
    normalization_caveat: Mapped[str] = mapped_column(default="")

    computed_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_stock_scores_market"),
        CheckConstraint("profile IN ('swing','position')", name="ck_stock_scores_profile"),
        CheckConstraint("band IN ('偏多','中性','偏空')", name="ck_stock_scores_band"),
        CheckConstraint(
            "symbol_type IN ('stock','etf','etf_leveraged','etf_inverse')",
            name="ck_stock_scores_symbol_type",
        ),
        UniqueConstraint(
            "symbol",
            "market",
            "profile",
            "as_of",
            name="uq_stock_scores_symbol_market_profile_as_of",
        ),
        Index(
            "ix_stock_scores_leaderboard",
            "market",
            "profile",
            "as_of",
            "composite",
        ),
    )


class StockSummaryRow(Base):
    """One symbol's cached AI-generated headline + narrative + SWOT — the write target of
    `analytics/summary_generation.py::generate_stock_summary` and the read target of the
    stock-detail-page `get_stock_summary` tool.

    `trade_date` is deliberately the *score's own* `ScoreResult.as_of` this summary was
    generated from, not "today" — matching `analytics.summary_store.get_cached_summary`'s point
    lookup by that exact date (never a "latest" fallback), so a summary generated against a
    still-cached prior day's score is never silently treated as fresh-today. Unlike
    `StockScoreRow`, this table carries no `market IN ('TW','US')` composite reuse concern beyond
    the same check, and no factor columns — the SWOT quadrants are portable JSON arrays of short
    strings (this project's SQLite/Postgres-portable JSON convention), not flat per-item columns,
    because the bullet count per quadrant varies call to call. No `user_id`: like the score cache,
    this is platform-wide derived content, not per-user state."""

    __tablename__ = "stock_summaries"

    id: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    market: Mapped[str]
    trade_date: Mapped[str]  # ISO date string — the ScoreResult.as_of this summary is grounded in
    headline: Mapped[str]
    narrative: Mapped[str]
    swot_strengths: Mapped[list[str]] = mapped_column(JSON, default=list)
    swot_weaknesses: Mapped[list[str]] = mapped_column(JSON, default=list)
    swot_opportunities: Mapped[list[str]] = mapped_column(JSON, default=list)
    swot_threats: Mapped[list[str]] = mapped_column(JSON, default=list)
    non_trading_caveat: Mapped[str] = mapped_column(default="")
    generated_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_stock_summaries_market"),
        UniqueConstraint(
            "symbol", "market", "trade_date", name="uq_stock_summaries_symbol_market_trade_date"
        ),
    )


class TrendScoreRow(Base):
    """One symbol's 趨勢評分 (trend score) composite for one trading day — the write target of
    `analytics/scoring_batch/::job_trend_score_batch` and the read target of both the
    detail-page point lookup and the chart's ranged history read.

    Unlike `StockScoreRow` (a same-day snapshot cache the batch *replaces* daily), this table
    genuinely needs history: a caller reads a date RANGE for a chart
    (`analytics/trend_score_store.py::get_trend_score_history`), so every day's row is kept, not
    overwritten — `upsert_trend_score` still upserts by `(symbol, market, as_of)` so a same-day
    re-run doesn't accumulate duplicate rows for that one day, but distinct `as_of` days
    genuinely coexist.

    Flat per-dimension columns (`technical_*`/`chip_flow_*`/`margin_credit_*`/
    `broker_concentration_*`/`sector_rotation_*`/`taiex_correlation_*`, each a value/description/
    unavailable_reason triplet) — same flat-column reasoning `StockScoreRow`'s own docstring
    gives: each dimension is independently point-looked-up-shaped, never a variable set, so flat
    columns are simpler and index-free reads faster than unpacking JSON on every read.
    `unavailable_dimensions` (a short list of dimension names) is a JSON array per this project's
    SQLite/Postgres-portable JSON convention (backend/CLAUDE.md §6).

    Indexing: `uq_trend_scores_symbol_market_as_of` is the write-side upsert key and also serves
    the detail-page point lookup; `ix_trend_scores_symbol_market_as_of` (non-unique) serves the
    chart's `WHERE symbol=? AND market=? ORDER BY as_of` range scan directly off the index."""

    __tablename__ = "trend_scores"

    id: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str]
    market: Mapped[str]
    as_of: Mapped[str]  # ISO date string — portable, matches stock_scores' own as_of convention
    composite: Mapped[float | None] = mapped_column(default=None)

    technical_value: Mapped[float | None] = mapped_column(default=None)
    technical_description: Mapped[str] = mapped_column(default="")
    technical_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    chip_flow_value: Mapped[float | None] = mapped_column(default=None)
    chip_flow_description: Mapped[str] = mapped_column(default="")
    chip_flow_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    margin_credit_value: Mapped[float | None] = mapped_column(default=None)
    margin_credit_description: Mapped[str] = mapped_column(default="")
    margin_credit_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    broker_concentration_value: Mapped[float | None] = mapped_column(default=None)
    broker_concentration_description: Mapped[str] = mapped_column(default="")
    broker_concentration_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    sector_rotation_value: Mapped[float | None] = mapped_column(default=None)
    sector_rotation_description: Mapped[str] = mapped_column(default="")
    sector_rotation_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    taiex_correlation_value: Mapped[float | None] = mapped_column(default=None)
    taiex_correlation_description: Mapped[str] = mapped_column(default="")
    taiex_correlation_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    # -- US-only dimensions — replace chip_flow/margin_credit/broker_concentration/sector_rotation
    # one-for-one for Market.US rows (none of those four TW dimensions has a US data source; see
    # `analytics/trend_score.py::US_DIMENSION_WEIGHTS`'s own comment). A US row leaves the four TW
    # columns above null; a TW row leaves these four null.
    institutional_ownership_value: Mapped[float | None] = mapped_column(default=None)
    institutional_ownership_description: Mapped[str] = mapped_column(default="")
    institutional_ownership_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    insider_sentiment_value: Mapped[float | None] = mapped_column(default=None)
    insider_sentiment_description: Mapped[str] = mapped_column(default="")
    insider_sentiment_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    analyst_revision_value: Mapped[float | None] = mapped_column(default=None)
    analyst_revision_description: Mapped[str] = mapped_column(default="")
    analyst_revision_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    sector_momentum_value: Mapped[float | None] = mapped_column(default=None)
    sector_momentum_description: Mapped[str] = mapped_column(default="")
    sector_momentum_unavailable_reason: Mapped[str | None] = mapped_column(default=None)

    unavailable_dimensions: Mapped[list[str]] = mapped_column(JSON, default=list)
    non_trading_caveat: Mapped[str] = mapped_column(default="")

    computed_at: Mapped[datetime] = mapped_column(_UTC)

    __table_args__ = (
        CheckConstraint(_MARKET_TW_US_CHECK, name="ck_trend_scores_market"),
        UniqueConstraint(
            "symbol",
            "market",
            "as_of",
            name="uq_trend_scores_symbol_market_as_of",
        ),
        Index(
            "ix_trend_scores_symbol_market_as_of",
            "symbol",
            "market",
            "as_of",
        ),
    )
