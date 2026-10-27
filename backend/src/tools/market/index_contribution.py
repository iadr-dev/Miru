"""Per-stock index-point contribution."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field

from ingest.jobs import MARKET_VALUE_WEIGHT_JOB, PRICE_EOD_JOB
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import MarketValueWeightRow
from schemas.enums import DataClass, Market
from tools.base import SyncStoreTool, ToolContext, is_entitled, parse_args
from tools.market._common import (
    closes_by_symbol,
    real_calendar,
    two_day_window,
    unavailable_reason_for_window,
)

_INDEX_CONTRIBUTION_TOP_N = 10


_CONTRIBUTION_CAVEAT = (
    "Estimated: each stock's market-value weight x its close-to-close price change. The weight is "
    "close x issued common shares as a share of the listed + OTC companies priced that day, not "
    "free-float adjusted; ETFs and emerging-board stocks are not in it. Not TWSE's official "
    "float-adjusted index-contribution points. Days stored before 2026-09-25 carry weights that "
    "covered the OTC board only."
)


class GetIndexContributionInput(BaseModel):
    """A market + an optional trading day + a top-N cap on each side of the ranking — mirrors
    `GetAdvanceDeclineInput`'s date semantics and `GetEtfMoneyFlowInput.top_n`'s ranking cap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to estimate contribution for (ISO date). Omitted or null "
        "means the latest available trading day.",
    )
    top_n: int = Field(default=_INDEX_CONTRIBUTION_TOP_N, ge=1, le=100)


_INDEX_CONTRIBUTION_FREE_ROW_COUNT = 2  # governance.md §4 top-N-preview exception — per side


def _ranked_index_contribution_rows(
    rows: Iterable[IndexContributionRow],
) -> tuple[IndexContributionRow, ...]:
    return tuple(row.model_copy(update={"rank": i}) for i, row in enumerate(rows, start=1))


def _mask_index_contribution_rows_beyond(
    rows: Sequence[IndexContributionRow], free_row_count: int
) -> tuple[IndexContributionRow, ...]:
    """Rows beyond `free_row_count` get every identity/value field nulled server-side — only
    `rank` survives, matching `_mask_broker_top_rows_beyond`'s contract."""
    masked: list[IndexContributionRow] = []
    for row in rows:
        if row.rank <= free_row_count:
            masked.append(row)
            continue
        masked.append(
            IndexContributionRow(
                rank=row.rank,
                symbol=None,
                stock_name=None,
                weight_per=None,
                pct_change=None,
                contribution_estimate=None,
                locked=True,
            )
        )
    return tuple(masked)


class IndexContributionRow(BaseModel):
    """`locked=True` rows (governance.md §4 top-N-preview exception) have every field but `rank`
    nulled server-side."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = 0  # 1-based position within its own side (positive/negative) — real when locked
    symbol: str | None
    stock_name: str | None
    weight_per: float | None  # 市值比重 (%) on trade_date: market-value share, not free-float adjusted
    pct_change: float | None  # (close - prior_close) / prior_close, a fraction not a percent
    contribution_estimate: float | None  # weight_per * pct_change — see contribution_caveat
    locked: bool = False


class GetIndexContributionOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    trade_date: date_ | None = None
    prior_trade_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    contribution_caveat: str = Field(default=_CONTRIBUTION_CAVEAT, min_length=1)
    free_row_count: int = 0  # governance.md §4 top-N-preview exception
    positive_contributors: tuple[IndexContributionRow, ...] = Field(default_factory=tuple)
    negative_contributors: tuple[IndexContributionRow, ...] = Field(default_factory=tuple)


class GetIndexContributionTool(SyncStoreTool):
    """`get_index_contribution` — an APPROXIMATE per-symbol index-contribution estimate,
    `weight_per x pct_change` (close-to-close), joining the already-ingested
    `MARKET_VALUE_WEIGHT_JOB` weight partition against `PRICE_EOD_JOB` closes for the same
    two-day window `GetAdvanceDeclineTool` resolves (`two_day_window`) — never a per-symbol
    live fan-out, for the same whole-market-aggregate reasoning as that tool.

    The weights are written by ingest (`ingest/jobs/market_weight.py`): close x issued common
    shares from the free TWSE/TPEx company-basics dumps, as a percent of the listed + OTC
    companies priced that day (the partitions stored while the paid table was served covered the
    OTC board only). This is **not** TWSE's real index-contribution methodology, which uses a
    float-adjusted divisor this codebase has no data for (`weight_per` is a plain market-value
    share, not a float-adjusted one) — `contribution_caveat` is therefore
    always populated (never empty, `Field(min_length=1)`) with that disclosure rather than
    only living in this docstring, mirroring how `currentness_caveat` is enforced elsewhere.

    Sponsor tier: `Capability.MARKET_VALUE_WEIGHT`'s underlying FinMind dataset
    (`TaiwanStockMarketValueWeight`) is documented Backer/Sponsor-only with no free per-symbol
    tier (`sources/finmind/fundamental.py`'s own `_DatasetConfig.data_class` for that
    capability). Reading it back from the already-ingested derived store does not lift that
    gate — the same reasoning `GetActiveEtfReportTool`/`GetEtfMoneyFlowTool` apply to their own
    Sponsor-tier ingested datasets (`ACTIVE_ETF_HOLDING`/`ACTIVE_ETF_HOLDING_CHANGE`): the
    entitlement tier tracks the underlying capability/dataset, not which code path reads it."""

    name = "get_index_contribution"
    description = (
        "Get an APPROXIMATE per-symbol estimate of contribution to the market's move for one "
        "trading day (market-value weight, close x issued shares and not free-float adjusted, x "
        "price change) — not TWSE's official float-adjusted contribution figure; see "
        "`contribution_caveat` (Sponsor-tier)."
    )
    input_model: type[BaseModel] = GetIndexContributionInput
    output_model: type[BaseModel] = GetIndexContributionOutput
    data_class = DataClass.SPONSOR
    cache_ttl_seconds = 300.0  # whole-market derivation; recomputed when ingest writes new data
    card_type: str | None = "index_contribution"
    supports_row_preview: bool = True

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetIndexContributionInput, args)
        market = typed.market.value
        calendar = real_calendar(self._store, market)
        window = two_day_window(
            self._store, PRICE_EOD_JOB.dataset, market, calendar, typed.trade_date
        )
        if window is None:
            return GetIndexContributionOutput(
                market=market,
                trade_date=typed.trade_date,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "price_eod", market, typed.trade_date
                ),
            )
        trade_date, prior_date = window
        if not self._store.has_partition(MARKET_VALUE_WEIGHT_JOB.dataset, market, trade_date):
            return GetIndexContributionOutput(
                market=market,
                trade_date=trade_date,
                prior_trade_date=prior_date,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "market_value_weight", market, trade_date
                ),
            )

        raw_weights = self._store.read_partition(
            MARKET_VALUE_WEIGHT_JOB.dataset, market, trade_date
        )
        weight_rows = rows_from_store(MarketValueWeightRow, raw_weights)
        today_closes = closes_by_symbol(self._store, market, trade_date)
        prior_closes = closes_by_symbol(self._store, market, prior_date)

        rows: list[IndexContributionRow] = []
        for weight_row in weight_rows:
            close = today_closes.get(weight_row.symbol)
            prior_close = prior_closes.get(weight_row.symbol)
            if close is None or not prior_close:  # missing/zero prior close — never divide by it
                continue
            pct_change = (close - prior_close) / prior_close
            rows.append(
                IndexContributionRow(
                    symbol=weight_row.symbol,
                    stock_name=weight_row.stock_name,
                    weight_per=weight_row.weight_per,
                    pct_change=pct_change,
                    contribution_estimate=weight_row.weight_per * pct_change,
                )
            )

        ranked = sorted(rows, key=lambda r: abs(r.contribution_estimate or 0.0), reverse=True)
        positive = _ranked_index_contribution_rows(
            r for r in ranked if (r.contribution_estimate or 0.0) > 0
        )[: typed.top_n]
        negative = _ranked_index_contribution_rows(
            r for r in ranked if (r.contribution_estimate or 0.0) < 0
        )[: typed.top_n]

        entitled = is_entitled(self.data_class, ctx.tier)
        if not entitled:
            positive = _mask_index_contribution_rows_beyond(
                positive, _INDEX_CONTRIBUTION_FREE_ROW_COUNT
            )
            negative = _mask_index_contribution_rows_beyond(
                negative, _INDEX_CONTRIBUTION_FREE_ROW_COUNT
            )

        return GetIndexContributionOutput(
            market=market,
            trade_date=trade_date,
            prior_trade_date=prior_date,
            data_available=True,
            free_row_count=_INDEX_CONTRIBUTION_FREE_ROW_COUNT,
            positive_contributors=positive,
            negative_contributors=negative,
        )
