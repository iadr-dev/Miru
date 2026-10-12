"""The daily scoring batch — computes the full multi-factor `ScoreResult` for **every** TW symbol
that day's ingested price/flow/fundamental data covers, for both horizon profiles, and upserts
every row into the relational `stock_scores` cache (`analytics/scoring_store.py`).

This is what makes both the per-symbol detail-page lookup and the whole-universe leaderboard fast
reads: the expensive part — assembling each symbol's series, building the day's cross-sectional
comparison universes (`winsorized_percentile`/`zscore` need the *whole* day's values to rank
against, not one symbol at a time), and blending the composite — happens once here; the two read
seams (`get_cached_score`/`get_leaderboard`) just look the finished rows back up.

**No `top_n`/`limit` anywhere in this module.** The whole point of a cross-sectional score is that
every symbol's percentile depends on the full comparison universe, so scoring only a subset would
both under-cover the leaderboard and silently bias every symbol's rank against a truncated peer
group. The batch iterates every symbol present in the day's price, institutional-flow, or
financial-statement/market-value data — exactly the same universe-discovery step that builds the
cross-sectional `momentum_universe`/`flow_universe`/`quality_universe`/`value_universe` sequences
`compute_factor_breakdown` needs. Top-N slicing belongs only at read time, in
`scoring_store.get_leaderboard`.

Modeled on `analytics/screener/batch.py`'s shape: leader- and calendar-gated at the call site, a
journaled run, reads the derived Parquet store the upstream whole-market jobs already wrote
(never a live per-symbol fetch), and writes through `IngestContext.session_factory` — the same
seam the theme layer's monthly jobs use for their relational writes — rather than the Parquet
store, since the score cache is relational (see `scoring_store.py`'s docstring for why).
"""

from __future__ import annotations

from analytics.scoring_batch.datasets import (
    ACTIVE_ETF_HOLDING_CHANGE_DATASET,
    ACTIVE_ETF_HOLDING_DATASET,
    ETF_EXPENSE_RATIO_DATASET,
    ETF_NAV_DATASET,
    INSTITUTIONAL_FLOW_DATASET,
    MARGIN_SHORT_DATASET,
    PRICE_EOD_DATASET,
    STOCK_INFO_DATASET,
    STOCK_SCORE_DATASET,
    TREND_SCORE_DATASET,
    US_ANALYST_CONSENSUS_DATASET,
    US_BALANCE_SHEET_DATASET,
    US_CASH_FLOW_DATASET,
    US_FINANCIALS_DATASET,
    US_INSIDER_ACTIVITY_DATASET,
    US_INSTITUTIONAL_HOLDERS_DATASET,
    US_PRICE_EOD_DATASET,
    US_SECTOR_CLASS_DATASET,
)
from analytics.scoring_batch.scoring import (
    job_scoring_batch,
)
from analytics.scoring_batch.trend import (
    job_trend_score_batch,
)

__all__ = [
    "ACTIVE_ETF_HOLDING_CHANGE_DATASET",
    "ACTIVE_ETF_HOLDING_DATASET",
    "ETF_EXPENSE_RATIO_DATASET",
    "ETF_NAV_DATASET",
    "INSTITUTIONAL_FLOW_DATASET",
    "MARGIN_SHORT_DATASET",
    "PRICE_EOD_DATASET",
    "STOCK_INFO_DATASET",
    "STOCK_SCORE_DATASET",
    "TREND_SCORE_DATASET",
    "US_ANALYST_CONSENSUS_DATASET",
    "US_BALANCE_SHEET_DATASET",
    "US_CASH_FLOW_DATASET",
    "US_FINANCIALS_DATASET",
    "US_INSIDER_ACTIVITY_DATASET",
    "US_INSTITUTIONAL_HOLDERS_DATASET",
    "US_PRICE_EOD_DATASET",
    "US_SECTOR_CLASS_DATASET",
    "job_scoring_batch",
    "job_trend_score_batch",
]
