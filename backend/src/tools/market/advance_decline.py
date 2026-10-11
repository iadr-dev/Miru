"""Advance/decline breadth buckets."""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field

from ingest.store import ParquetStore
from schemas.enums import DataClass, Market
from tools.base import SyncStoreTool, ToolContext, parse_args
from tools.market._common import (
    closes_by_symbol,
    price_eod_dataset_for,
    real_calendar,
    two_day_window,
    unavailable_reason_for_window,
)


class GetAdvanceDeclineInput(BaseModel):
    """A market + an optional trading day; omitted/`None` means "latest available"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to report breadth for (ISO date). Omitted or null means the "
        "latest available trading day.",
    )


class AdvanceDeclineBuckets(BaseModel):
    """Symbol counts bucketed by close-to-close percentage change. `unchanged` is exactly
    `pct_change == 0`; every other symbol falls into exactly one up- or down-side bucket."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    up_over_5pct: int = 0
    up_3_to_5pct: int = 0
    up_2_to_3pct: int = 0
    up_1_to_2pct: int = 0
    up_0_to_1pct: int = 0
    unchanged: int = 0
    down_0_to_1pct: int = 0
    down_1_to_2pct: int = 0
    down_2_to_3pct: int = 0
    down_3_to_5pct: int = 0
    down_over_5pct: int = 0


class GetAdvanceDeclineOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    advancing_count: int = 0
    declining_count: int = 0
    unchanged_count: int = 0
    buckets: AdvanceDeclineBuckets = Field(default_factory=AdvanceDeclineBuckets)


_BUCKET_THRESHOLD_5PCT = 5


_BUCKET_THRESHOLD_3PCT = 3


_BUCKET_THRESHOLD_2PCT = 2


_BUCKET_THRESHOLD_1PCT = 1


def _advance_decline_bucket_name(pct: float) -> str:
    """Which `AdvanceDeclineBuckets` field one symbol's close-to-close percentage change
    (already `x100` — a whole-number percent, not a fraction) belongs in."""
    if pct == 0:
        return "unchanged"
    magnitude, sign = abs(pct), 1 if pct > 0 else -1
    if magnitude > _BUCKET_THRESHOLD_5PCT:
        step = "over_5pct"
    elif magnitude > _BUCKET_THRESHOLD_3PCT:
        step = "3_to_5pct"
    elif magnitude > _BUCKET_THRESHOLD_2PCT:
        step = "2_to_3pct"
    elif magnitude > _BUCKET_THRESHOLD_1PCT:
        step = "1_to_2pct"
    else:
        step = "0_to_1pct"
    return f"{'up' if sign > 0 else 'down'}_{step}"


class GetAdvanceDeclineTool(SyncStoreTool):
    """`get_advance_decline` — whole-market advance/decline breadth for one trading day: every
    symbol's close-to-close percentage change, bucketed into >5%/3-5%/2-3%/1-2%/0-1% on both
    the up- and down-side plus `unchanged`, read from the already-ingested `PRICE_EOD_JOB`
    Parquet partition (the same derived-store read `closes_by_symbol` backs for
    `GetActiveEtfReportTool`) — never a per-symbol live fan-out, since this is a genuinely
    whole-market aggregate and reading the ingest-owned store back is the one legitimate path
    for that shape of read (see `GetEtfMoneyFlowTool`'s own docstring for the same reasoning).

    Public tier: this reads `Capability.PRICE_EOD`'s own already-public whole-market dataset
    (`sources/finmind/technical.py`'s `_DatasetConfig.data_class` for `PRICE_EOD` is `PUBLIC`),
    not a Sponsor-gated one — unlike `get_index_contribution` alongside it, which reads a
    genuinely Sponsor-tier dataset and is gated accordingly."""

    name = "get_advance_decline"
    description = (
        "Get the whole-market advance/decline breadth for one trading day (defaults to the "
        "latest available day): counts of advancing/declining/unchanged symbols, bucketed by "
        "close-to-close percentage change."
    )
    input_model: type[BaseModel] = GetAdvanceDeclineInput
    output_model: type[BaseModel] = GetAdvanceDeclineOutput
    data_class = DataClass.PUBLIC
    cache_ttl_seconds = 300.0  # whole-market derivation; recomputed when ingest writes new data
    card_type: str | None = "advance_decline"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no router fetch — this tool only reads the ingest-owned Parquet store
        typed = parse_args(GetAdvanceDeclineInput, args)
        market = typed.market.value
        calendar = real_calendar(self._store, market)
        window = two_day_window(
            self._store, price_eod_dataset_for(market), market, calendar, typed.trade_date
        )
        if window is None:
            return GetAdvanceDeclineOutput(
                market=market,
                trade_date=typed.trade_date,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        trade_date, prior_date = window
        today_closes = closes_by_symbol(self._store, market, trade_date)
        prior_closes = closes_by_symbol(self._store, market, prior_date)

        bucket_counts: dict[str, int] = {}
        advancing = declining = unchanged = 0
        for symbol, close in today_closes.items():
            prior_close = prior_closes.get(symbol)
            if not prior_close:  # missing or legitimately-zero prior close — never divide by it
                continue
            pct = (close - prior_close) / prior_close * 100
            if pct > 0:
                advancing += 1
            elif pct < 0:
                declining += 1
            else:
                unchanged += 1
            bucket = _advance_decline_bucket_name(pct)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        return GetAdvanceDeclineOutput(
            market=market,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            data_available=True,
            advancing_count=advancing,
            declining_count=declining,
            unchanged_count=unchanged,
            buckets=AdvanceDeclineBuckets(**bucket_counts),
        )
