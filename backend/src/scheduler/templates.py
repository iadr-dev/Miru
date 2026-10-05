"""Alert rule templates — the scheduler's view of the shared rule vocabulary.

The pure vocabulary (condition models, `RuleCondition`, `describe`, `NON_ADVICE_FOOTER`) lives in
`schemas.alerts` so the agent's `propose_alert` tool can share it without an upward import; it is
re-exported here so scheduler code keeps a single import point. What stays here is the part that
needs the ingest layer: which dataset / capability a template reads and how much history it needs.
"""

from __future__ import annotations

from ingest.jobs import (
    ATTENTION_STOCKS_DATASET,
    DISPOSITION_EVENTS_DATASET,
    ETF_NAV_DATASET,
    INSTITUTIONAL_FLOW_JOB,
    MARGIN_SHORT_JOB,
    MONTH_REVENUE_JOB,
    PRICE_EOD_JOB,
)
from ingest.us_jobs import US_PRICE_EOD_DATASET
from schemas.alerts import (
    MAX_HISTORY_DAYS,
    NON_ADVICE_FOOTER,
    AttentionListEntry,
    ClosePrice,
    ConditionModel,
    DailyChange,
    DispositionListEntry,
    EtfPremiumDiscount,
    InstitutionalStreak,
    MarginBalanceChange,
    MonthRevenuePublished,
    RuleCondition,
    VolumeSurge,
    describe,
    investor_label,
)
from schemas.enums import Capability, Market

# The one place a non-price template kind is tied to the capability that drives it and the ingested
# dataset it reads. Price-derived kinds are deliberately absent: their dataset depends on the market
# (Taiwan's whole-market FinMind pull vs the US yfinance pull), so they fall through to the price
# default below. `dataset_of`, `capability_of` and the reconciliation probe all derive from this.
_KIND_SOURCE: dict[str, tuple[Capability, str]] = {
    "institutional_streak": (Capability.INSTITUTIONAL_FLOW, INSTITUTIONAL_FLOW_JOB.dataset),
    "attention_entry": (Capability.ATTENTION_STOCKS, ATTENTION_STOCKS_DATASET),
    "disposition_entry": (Capability.DISPOSITION_EVENTS, DISPOSITION_EVENTS_DATASET),
    "month_revenue_published": (Capability.MONTH_REVENUE, MONTH_REVENUE_JOB.dataset),
    "margin_balance_change": (Capability.MARGIN_SHORT, MARGIN_SHORT_JOB.dataset),
    "etf_premium_discount": (Capability.ETF_NAV, ETF_NAV_DATASET),
}


def _price_dataset(market: Market) -> str:
    return US_PRICE_EOD_DATASET if market is Market.US else PRICE_EOD_JOB.dataset


def dataset_of(condition: ConditionModel, market: Market = Market.TW) -> str:
    """The ingested dataset a template reads. Only price-derived templates vary by market; the
    chip-flow, watch-list, revenue, margin and NAV datasets are Taiwan-only."""
    source = _KIND_SOURCE.get(condition.kind)
    return source[1] if source is not None else _price_dataset(market)


def capability_of(condition: ConditionModel) -> Capability:
    """The capability whose `data.updated` signal (or eval watermark) drives a template."""
    source = _KIND_SOURCE.get(condition.kind)
    return source[0] if source is not None else Capability.PRICE_EOD


def history_days_of(condition: ConditionModel) -> int:
    """Prior trading days (excluding the as_of day) the template needs to read."""
    if isinstance(condition, VolumeSurge):
        return condition.window
    if isinstance(condition, InstitutionalStreak):
        return condition.days - 1
    if isinstance(condition, DailyChange):
        return 1
    return 0


_PROBE_DATASETS: dict[Capability, str] = dict(_KIND_SOURCE.values())


def probe_dataset(capability: Capability, market: Market) -> str:
    """The dataset whose newest partition marks "data for this capability has landed" — the single
    place a capability is tied to its dataset for the reconciliation sweep. Price data is the one
    capability stored per market (Taiwan and US are different datasets)."""
    known = _PROBE_DATASETS.get(capability)
    if known is not None:
        return known
    return _price_dataset(market)


__all__ = [
    "MAX_HISTORY_DAYS",
    "NON_ADVICE_FOOTER",
    "AttentionListEntry",
    "ClosePrice",
    "ConditionModel",
    "DailyChange",
    "DispositionListEntry",
    "EtfPremiumDiscount",
    "InstitutionalStreak",
    "MarginBalanceChange",
    "MonthRevenuePublished",
    "RuleCondition",
    "VolumeSurge",
    "capability_of",
    "dataset_of",
    "describe",
    "history_days_of",
    "investor_label",
    "probe_dataset",
]
