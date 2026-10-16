"""Large-holder accumulation rankings."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date as date_
from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field

from ingest.jobs import SHAREHOLDING_DISTRIBUTION_JOB, STOCK_INFO_JOB
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import StockInfoRow
from schemas.enums import DataClass, Market
from tools.base import SyncStoreTool, ToolContext, is_entitled, parse_args
from tools.market._common import (
    closes_by_symbol,
    latest_dataset_date,
    unavailable_reason_for_window,
)

# The tier scheme confirmed live against the real upstream — both TDCC's own web query form
# (https://www.tdcc.com.tw/portal/zh/smWeb/qryStock, symbol 2330, 2026-08-21 snapshot) and
# FinMind's Sponsor-tier `TaiwanStockHoldingSharesPer` return the IDENTICAL 17-row tier table
# with these exact upstream label strings — never a guessed/assumed boundary. Tier 16
# ("差異數調整") and 17 ("合計") are bookkeeping rows, not real holding-size tiers, and are
# deliberately excluded from every threshold sum below.
_HOLDER_TIER_LABELS: tuple[str, ...] = (
    "1-999",
    "1,000-5,000",
    "5,001-10,000",
    "10,001-15,000",
    "15,001-20,000",
    "20,001-30,000",
    "30,001-40,000",
    "40,001-50,000",
    "50,001-100,000",
    "100,001-200,000",
    "200,001-400,000",
    "400,001-600,000",
    "600,001-800,000",
    "800,001-1,000,000",
    "1,000,001以上",
    # FinMind's own label for the same top tier (seen in the ingested partitions); listed after the
    # TDCC spelling so every tail slice below includes both.
    "more than 1,000,001",
)


# Threshold (張, 1張 = 1,000 shares) -> the tier labels whose shares sum to "holds at least this
# many張". Each threshold is every tier whose lower bound is >= that many shares — e.g. "400張"
# includes tier 12 (400,001-600,000) through the open-ended top tier, matching how the TDCC/
# goodinfo convention for "X張以上持股比例" is computed (a cumulative lower-bound sum, not one
# isolated bucket).
_HOLDER_THRESHOLDS: dict[int, tuple[str, ...]] = {
    200: _HOLDER_TIER_LABELS[10:],  # 200,001-400,000 and every tier above
    400: _HOLDER_TIER_LABELS[11:],  # 400,001-600,000 and every tier above
    800: _HOLDER_TIER_LABELS[13:],  # 800,001-1,000,000 and every tier above
    1000: _HOLDER_TIER_LABELS[14:],  # the top tier only (either spelling)
}


_HOLDER_ACCUMULATION_TOP_N = 20


class HolderThreshold(IntEnum):
    TIER_200 = 200
    TIER_400 = 400
    TIER_800 = 800
    TIER_1000 = 1000


class HolderWindow(IntEnum):
    WEEK_1 = 1
    WEEK_2 = 2
    WEEK_3 = 3
    WEEK_4 = 4


class GetHolderAccumulationInput(BaseModel):
    """A market + which holding-size threshold to rank by + how many weekly snapshots back to
    compare against. `window` counts *snapshots*, not calendar weeks — this dataset publishes
    at most once per real trading week, so `window=2` means "2 snapshots ago", which is
    ordinarily ~2 calendar weeks but can stretch further across a holiday-shortened week with
    no snapshot at all (an honest gap, not silently treated as "no such week")."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    threshold: HolderThreshold = Field(
        default=HolderThreshold.TIER_400,
        description="Holding-size threshold in 張 (1,000 shares): 200/400/800/1000, cumulative "
        "(e.g. 400 sums every tier holding >= 400,001 shares).",
    )
    window: HolderWindow = Field(
        default=HolderWindow.WEEK_2,
        description="How many weekly snapshots back to compare against: 1/2/3/4.",
    )
    as_of: date_ | None = Field(
        default=None,
        description="The snapshot date to rank as of (ISO date). Omitted or null means the "
        "latest ingested snapshot.",
    )
    top_n: int = Field(default=_HOLDER_ACCUMULATION_TOP_N, ge=1, le=50)


class HolderAccumulationRow(BaseModel):
    """`locked=True` rows (governance.md §4 top-N-preview exception) have every field but
    `rank` nulled server-side — an unentitled caller must not learn which stock a locked row
    even names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = 0  # 1-based position in the ranked list — real even when locked
    symbol: str | None
    stock_name: str | None
    latest_close: float | None = None  # None if no PRICE_EOD_JOB bar exists for `as_of`
    percent_of_custody_before: float | None  # this threshold's summed 占集保庫存數比例% at
    # `period_ago`
    percent_of_custody_after: float | None  # this threshold's summed 占集保庫存數比例% at `as_of`
    percent_of_custody_change: float | None  # after - before, in percentage points
    shares_change: int | None  # this threshold's summed shares, after - before
    locked: bool = False


_HOLDER_ACCUMULATION_FREE_ROW_COUNT = 2  # governance.md §4 top-N-preview exception


def _ranked_holder_accumulation_rows(
    rows: Sequence[HolderAccumulationRow],
) -> tuple[HolderAccumulationRow, ...]:
    return tuple(row.model_copy(update={"rank": i}) for i, row in enumerate(rows, start=1))


def _mask_holder_accumulation_rows_beyond(
    rows: Sequence[HolderAccumulationRow], free_row_count: int
) -> tuple[HolderAccumulationRow, ...]:
    """Rows beyond `free_row_count` get every identity/value field nulled server-side — only
    `rank` survives, matching `_mask_broker_top_rows_beyond`'s contract."""
    masked: list[HolderAccumulationRow] = []
    for row in rows:
        if row.rank <= free_row_count:
            masked.append(row)
            continue
        masked.append(
            HolderAccumulationRow(
                rank=row.rank,
                symbol=None,
                stock_name=None,
                latest_close=None,
                percent_of_custody_before=None,
                percent_of_custody_after=None,
                percent_of_custody_change=None,
                shares_change=None,
                locked=True,
            )
        )
    return tuple(masked)


class GetHolderAccumulationOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    threshold: HolderThreshold = HolderThreshold.TIER_400
    window: HolderWindow = HolderWindow.WEEK_2
    as_of: date_ | None = None
    period_ago: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    free_row_count: int = 0  # governance.md §4 top-N-preview exception
    rows: tuple[HolderAccumulationRow, ...] = Field(default_factory=tuple)


class GetHolderAccumulationTool(SyncStoreTool):
    """`get_holder_accumulation` — 大戶加碼股: which symbols grew a chosen large-holder tier's
    share-of-custody the most over 1-4 weekly snapshots, computed from the already-ingested
    `SHAREHOLDING_DISTRIBUTION_JOB` partitions (`ingest/jobs.py`) — never a per-symbol live
    fan-out across the whole ~2,700-symbol universe (see that job's own docstring for why a
    ranking read specifically needed a whole-market ingest path rather than reusing the
    Sponsor-tier per-symbol `get_shareholder_dispersion`/TDCC `get_tdcc_shareholding_distribution`
    tools' existing per-symbol shape).

    Tier scheme confirmed live against both TDCC's own web query tool and FinMind's
    `TaiwanStockHoldingSharesPer` (`_HOLDER_TIER_LABELS`'s own docstring) — never guessed.

    History caveat: `SHAREHOLDING_DISTRIBUTION_JOB` only started ingesting from whenever its
    daemon entry first ran; `window` snapshots further back than that simply are not available
    yet, disclosed via `unavailable_reason` rather than silently returning zero-width deltas.

    Sponsor tier: `Capability.SHAREHOLDING_DISTRIBUTION`'s underlying FinMind dataset
    (`TaiwanStockHoldingSharesPer`) is documented Backer/Sponsor-only (`sources/finmind/
    chip_institutional.py`'s own `_DatasetConfig.data_class`) — reading it back from the
    already-ingested derived store does not lift that gate, the same reasoning
    `GetIndexContributionTool` gives for its own Sponsor-tier ingested dataset."""

    name = "get_holder_accumulation"
    description = (
        "Get the whole-market ranking of which stocks grew a chosen large-holder tier "
        "(>=200/400/800/1,000張) share-of-custody the most over 1-4 weekly snapshots "
        "(Sponsor-tier; 集保戶股權分散表 week-over-week accumulation)."
    )
    input_model: type[BaseModel] = GetHolderAccumulationInput
    output_model: type[BaseModel] = GetHolderAccumulationOutput
    data_class = DataClass.SPONSOR
    cache_ttl_seconds = 300.0  # whole-market derivation; recomputed when ingest writes new data
    card_type: str | None = "holder_accumulation"
    supports_row_preview: bool = True

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def _tier_totals_by_symbol(
        self, market: str, snapshot_date: date_, threshold: HolderThreshold
    ) -> dict[str, tuple[float, int]] | None:
        """This threshold's summed `(percent_of_custody, shares)` per symbol at `snapshot_date`,
        or `None` if that snapshot has never been ingested."""
        if not self._store.has_partition(
            SHAREHOLDING_DISTRIBUTION_JOB.dataset, market, snapshot_date
        ):
            return None
        raw_rows = self._store.read_partition(
            SHAREHOLDING_DISTRIBUTION_JOB.dataset, market, snapshot_date
        )
        wanted_labels = set(_HOLDER_THRESHOLDS[threshold.value])
        totals: dict[str, tuple[float, int]] = {}
        for row in raw_rows:
            symbol = row.get("symbol")
            level = row.get("holding_shares_level")
            if not isinstance(symbol, str) or level not in wanted_labels:
                continue
            percent = row.get("percent")
            unit = row.get("unit")
            if not isinstance(percent, (int, float)) or not isinstance(unit, (int, float)):
                continue
            prior_percent, prior_shares = totals.get(symbol, (0.0, 0))
            totals[symbol] = (prior_percent + float(percent), prior_shares + int(unit))
        return totals

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetHolderAccumulationInput, args)
        market = typed.market.value

        snapshot_dates = self._store.list_partition_dates(
            SHAREHOLDING_DISTRIBUTION_JOB.dataset, market
        )
        if not snapshot_dates:
            return GetHolderAccumulationOutput(
                market=market,
                threshold=typed.threshold,
                window=typed.window,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "shareholding_distribution", market, typed.as_of
                ),
            )
        as_of = typed.as_of or snapshot_dates[-1]
        eligible = [d for d in snapshot_dates if d <= as_of]
        if not eligible or eligible[-1] != as_of:
            # `as_of` was explicitly requested but has no ingested snapshot — never silently
            # substitute a different date the caller did not ask for.
            return GetHolderAccumulationOutput(
                market=market,
                threshold=typed.threshold,
                window=typed.window,
                as_of=typed.as_of,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "shareholding_distribution", market, typed.as_of
                ),
            )
        idx = eligible.index(as_of)
        if idx < typed.window.value:
            return GetHolderAccumulationOutput(
                market=market,
                threshold=typed.threshold,
                window=typed.window,
                as_of=as_of,
                data_available=False,
                unavailable_reason=(
                    f"Only {idx} weekly shareholding-distribution snapshot(s) ingested before "
                    f"{as_of.isoformat()} — need at least {typed.window.value} for a "
                    f"{typed.window.value}-week comparison. This history grows by one real "
                    "snapshot per week; no bulk backfill exists for this dataset."
                ),
            )
        period_ago = eligible[idx - typed.window.value]

        after = self._tier_totals_by_symbol(market, as_of, typed.threshold)
        before = self._tier_totals_by_symbol(market, period_ago, typed.threshold)
        if after is None or before is None:
            return GetHolderAccumulationOutput(
                market=market,
                threshold=typed.threshold,
                window=typed.window,
                as_of=as_of,
                period_ago=period_ago,
                data_available=False,
                unavailable_reason=unavailable_reason_for_window(
                    "shareholding_distribution", market, as_of
                ),
            )

        roster_date = latest_dataset_date(self._store, STOCK_INFO_JOB.dataset, market)
        names_by_symbol: dict[str, str] = {}
        if roster_date is not None:
            raw_roster = self._store.read_partition(STOCK_INFO_JOB.dataset, market, roster_date)
            roster_rows = rows_from_store(StockInfoRow, raw_roster)
            names_by_symbol = {row.symbol: row.name for row in roster_rows}
        closes = closes_by_symbol(self._store, market, as_of)

        rows: list[HolderAccumulationRow] = []
        for symbol, (percent_after, shares_after) in after.items():
            percent_before, shares_before = before.get(symbol, (0.0, 0))
            name = names_by_symbol.get(symbol)
            if not name:
                continue
            rows.append(
                HolderAccumulationRow(
                    symbol=symbol,
                    stock_name=name,
                    latest_close=closes.get(symbol),
                    percent_of_custody_before=percent_before,
                    percent_of_custody_after=percent_after,
                    percent_of_custody_change=percent_after - percent_before,
                    shares_change=shares_after - shares_before,
                )
            )
        ranked = sorted(rows, key=lambda r: r.percent_of_custody_change or 0.0, reverse=True)
        output_rows = _ranked_holder_accumulation_rows(ranked[: typed.top_n])
        if not is_entitled(self.data_class, ctx.tier):
            output_rows = _mask_holder_accumulation_rows_beyond(
                output_rows, _HOLDER_ACCUMULATION_FREE_ROW_COUNT
            )
        return GetHolderAccumulationOutput(
            market=market,
            threshold=typed.threshold,
            window=typed.window,
            as_of=as_of,
            period_ago=period_ago,
            data_available=True,
            free_row_count=_HOLDER_ACCUMULATION_FREE_ROW_COUNT,
            rows=output_rows,
        )
