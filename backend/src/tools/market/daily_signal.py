"""The daily active-ETF signal board."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from ingest.jobs import ACTIVE_ETF_HOLDING_CHANGE_JOB, ACTIVE_ETF_HOLDING_JOB
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import ActiveEtfHoldingChangeRow, ActiveEtfHoldingRow
from schemas.enums import DataClass, Market
from tools.base import SyncStoreTool, ToolContext, is_entitled, parse_args
from tools.market._common import (
    HoldingStatus,
    classify_holding_status,
    closes_by_symbol,
    ingested_dates,
    latest_ingested_date_on_or_before,
)


def _two_latest_ingested_dates_on_or_before(
    store: ParquetStore, dataset: str, market: str, on_or_before: date_
) -> tuple[date_ | None, date_ | None]:
    """`(latest, prior)` — the two most recent dates `dataset`+`market` has an actually-written
    partition for, at or before `on_or_before`, found by the same `ingested_dates` scan
    `latest_ingested_date_on_or_before` uses (kept as a separate function rather than a
    refactor of that one, since a day-over-day report needs both dates from one scan, but every
    existing single-date caller of that function doesn't). Either or both are `None` (never a
    guessed date) when fewer than 2 dates have landed."""
    candidates = ingested_dates(store, dataset, market, on_or_before=on_or_before)
    if not candidates:
        return None, None
    ordered = sorted(candidates, reverse=True)
    latest = ordered[0]
    prior = ordered[1] if len(ordered) > 1 else None
    return latest, prior


class GetDailySignalInput(BaseModel):
    """A market scope + an optional trading day; omitted/`None` means "latest available"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    trade_date: date_ | None = Field(
        default=None,
        description="Trading day to report on (ISO date). Omitted or null means the latest "
        "available trading day.",
    )


class DailySignalRow(BaseModel):
    """One constituent stock's day-over-day status across EVERY active ETF combined — the same
    新增/刪除/加碼/減碼 classification `ActiveEtfReportRow` uses for one ETF, applied to the
    aggregate (sum of shares held across all active ETFs) instead. Only stocks with a nonzero
    aggregate change appear (mirrors `ActiveEtfReportRow`'s own "only changed rows" contract).
    `locked=True` rows (governance.md §4 top-N-preview exception) have every field but `status`/
    `rank` nulled server-side, including `component_stock_id`/`component_stock_name` — an
    unentitled caller must not learn which stock a locked row even names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int = 0  # 1-based position in the emitted list — real even when locked
    component_stock_id: str | None
    component_stock_name: str | None
    close: float | None = None  # 股價 — None if the store has no price bar for this symbol
    change_pct: float | None = None  # 漲跌幅 vs. the prior trading day
    status: HoldingStatus
    shares_delta: int | None  # 變動張數 — aggregate net buy-sell across every active ETF, today
    pct_change: float | None = None  # 變動幅度 — shares_delta ÷ prior-day aggregate shares
    buying_etf_count: int | None  # distinct ETFs net-buying this stock today
    selling_etf_count: int | None  # distinct ETFs net-selling this stock today
    locked: bool = False


class DailySignalHighlight(BaseModel):
    """One 焦點股 highlight card — the single stock with the largest aggregate inflow/outflow
    for the day, plus the 多空共識 (buying-vs-selling ETF count) for that same stock. Unlike
    `DailySignalRow`, this is never part of the "top N free rows" budget — governance.md §4's
    row-preview exception locks it entirely (`locked=True`, every field but that flag nulled)
    for an unentitled caller, since a single 焦點股 highlight concentrates the whole card's most
    valuable signal into one card and giving it away for free would defeat the point of gating
    the rest of the list."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    component_stock_id: str | None
    component_stock_name: str | None
    held_market_value_delta: float | None = None  # 資金動向 (NTD) — None if no close was found
    buying_etf_count: int | None = None
    selling_etf_count: int | None = None
    locked: bool = False


class GetDailySignalOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    report_date: date_ | None = None
    prior_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    total_stock_count: int = 0  # 共N檔 — every stock with a nonzero aggregate change today
    new_count: int = 0  # 新增
    removed_count: int = 0  # 刪除
    increased_count: int = 0  # 加碼
    decreased_count: int = 0  # 減碼
    free_row_count: int = 0  # governance.md §4 — real rows at the front of `rows`
    top_inflow: DailySignalHighlight | None = None
    top_outflow: DailySignalHighlight | None = None
    rows: tuple[DailySignalRow, ...] = Field(default_factory=tuple)


def _aggregate_shares_by_stock(
    holdings: Sequence[ActiveEtfHoldingRow],
) -> dict[str, int]:
    """Sum of `shares` held across every active ETF, per constituent stock — the aggregate
    analog of one ETF's own per-stock `shares` field, on a single day's holding snapshot."""
    totals: dict[str, int] = {}
    for holding in holdings:
        totals[holding.component_stock_id] = (
            totals.get(holding.component_stock_id, 0) + holding.shares
        )
    return totals


@dataclass(frozen=True)
class _DailySignalContext:
    """Bundles the four per-symbol maps `_daily_signal_rows` needs — pulled into one dataclass
    purely to stay under the project's argument-count lint budget, same reasoning
    `_active_etf_fund_size_fields`'s own `dates`/`holdings` tuple params use."""

    prior_aggregate_shares: dict[str, int]
    report_aggregate_shares: dict[str, int]
    closes: dict[str, float]
    prior_closes: dict[str, float]


class _DailySignalAggregates(NamedTuple):
    """Per-stock aggregates across every ETF's change row for one day — split out of
    `_daily_signal_rows` purely to keep that function's own branching under the project's
    cognitive-complexity budget; this half is a single pass with no classification logic."""

    buy_by_stock: dict[str, int]
    sell_by_stock: dict[str, int]
    name_by_stock: dict[str, str]
    buying_etfs: dict[str, set[str]]
    selling_etfs: dict[str, set[str]]


def _aggregate_daily_signal_changes(
    changes: Sequence[ActiveEtfHoldingChangeRow],
) -> _DailySignalAggregates:
    """`buy`/`sell` summed across every ETF's change row for each stock, plus which ETFs
    contributed a buy/sell — the raw-aggregation half of `_daily_signal_rows`."""
    buy_by_stock: dict[str, int] = {}
    sell_by_stock: dict[str, int] = {}
    name_by_stock: dict[str, str] = {}
    buying_etfs: dict[str, set[str]] = {}
    selling_etfs: dict[str, set[str]] = {}
    for change in changes:
        stock_id = change.component_stock_id
        name_by_stock[stock_id] = change.component_stock_name
        buy_by_stock[stock_id] = buy_by_stock.get(stock_id, 0) + change.buy
        sell_by_stock[stock_id] = sell_by_stock.get(stock_id, 0) + change.sell
        if change.buy > 0:
            buying_etfs.setdefault(stock_id, set()).add(change.symbol)
        if change.sell > 0:
            selling_etfs.setdefault(stock_id, set()).add(change.symbol)
    return _DailySignalAggregates(
        buy_by_stock=buy_by_stock,
        sell_by_stock=sell_by_stock,
        name_by_stock=name_by_stock,
        buying_etfs=buying_etfs,
        selling_etfs=selling_etfs,
    )


def _daily_signal_row_for_stock(
    stock_id: str,
    name: str,
    aggregates: _DailySignalAggregates,
    ctx: _DailySignalContext,
    *,
    has_prior_day: bool,
) -> DailySignalRow | None:
    """One stock's `DailySignalRow`, classified with the same `classify_holding_status` the
    single-ETF report uses (just against the cross-ETF aggregate shares instead of one ETF's own
    weight) — `None` for a stock with zero net buy/sell (defensive; every stock here came from a
    nonzero-buy-or-sell change row, so this should never actually trigger)."""
    buy = aggregates.buy_by_stock.get(stock_id, 0)
    sell = aggregates.sell_by_stock.get(stock_id, 0)
    if buy == 0 and sell == 0:
        return None
    prior_shares = ctx.prior_aggregate_shares.get(stock_id)
    current_shares = ctx.report_aggregate_shares.get(stock_id)
    status = classify_holding_status(
        buy=buy,
        sell=sell,
        prior_weight=float(prior_shares) if prior_shares is not None else None,
        current_weight=float(current_shares) if current_shares is not None else None,
        has_prior_day=has_prior_day,
    )
    shares_delta = buy - sell
    pct_change = shares_delta / prior_shares if prior_shares else None
    close = ctx.closes.get(stock_id)
    prior_close = ctx.prior_closes.get(stock_id)
    change_pct = (close / prior_close - 1) * 100 if close is not None and prior_close else None
    return DailySignalRow(
        component_stock_id=stock_id,
        component_stock_name=name,
        close=close,
        change_pct=change_pct,
        status=status,
        shares_delta=shares_delta,
        pct_change=pct_change,
        buying_etf_count=len(aggregates.buying_etfs.get(stock_id, ())),
        selling_etf_count=len(aggregates.selling_etfs.get(stock_id, ())),
    )


def _daily_signal_rows(
    changes: Sequence[ActiveEtfHoldingChangeRow],
    ctx: _DailySignalContext,
    *,
    has_prior_day: bool,
) -> list[DailySignalRow]:
    """One row per stock with a nonzero aggregate buy/sell today, ranked by `abs(shares_delta)`
    descending (largest-magnitude signal first) and stamped with a 1-based `rank` — mirrors
    `_active_etf_report_rows`'s own per-ETF version, see `_aggregate_daily_signal_changes`/
    `_daily_signal_row_for_stock` for the two halves this delegates to. The ranking exists so the
    governance.md §4 top-N-preview exception's free rows are the most-informative ones, not an
    arbitrary iteration order over a dict."""
    aggregates = _aggregate_daily_signal_changes(changes)
    unranked: list[DailySignalRow] = []
    for stock_id, name in aggregates.name_by_stock.items():
        row = _daily_signal_row_for_stock(
            stock_id, name, aggregates, ctx, has_prior_day=has_prior_day
        )
        if row is not None:
            unranked.append(row)
    unranked.sort(key=lambda r: abs(r.shares_delta or 0), reverse=True)
    return [row.model_copy(update={"rank": i}) for i, row in enumerate(unranked, start=1)]


_DAILY_SIGNAL_FREE_ROW_COUNT = 2  # governance.md §4 top-N-preview exception


def _mask_daily_signal_rows_beyond(
    rows: Sequence[DailySignalRow], free_row_count: int
) -> tuple[DailySignalRow, ...]:
    """Rows beyond `free_row_count` get every identity/value field nulled server-side — `rank`
    and `status` survive (a bare ordinal + categorical bucket aren't individually identifying),
    matching `_mask_broker_top_rows_beyond`'s contract."""
    masked: list[DailySignalRow] = []
    for row in rows:
        if row.rank <= free_row_count:
            masked.append(row)
            continue
        masked.append(
            DailySignalRow(
                rank=row.rank,
                component_stock_id=None,
                component_stock_name=None,
                close=None,
                change_pct=None,
                status=row.status,
                shares_delta=None,
                pct_change=None,
                buying_etf_count=None,
                selling_etf_count=None,
                locked=True,
            )
        )
    return tuple(masked)


_DAILY_SIGNAL_LOCKED_HIGHLIGHT = DailySignalHighlight(
    component_stock_id=None,
    component_stock_name=None,
    held_market_value_delta=None,
    buying_etf_count=None,
    selling_etf_count=None,
    locked=True,
)


def _mask_daily_signal_highlight(
    highlight: DailySignalHighlight | None,
) -> DailySignalHighlight | None:
    """A 焦點股 highlight concentrates the whole card's single most valuable signal — locked
    entirely for an unentitled caller rather than counted against `free_row_count`."""
    return _DAILY_SIGNAL_LOCKED_HIGHLIGHT if highlight is not None else None


def _daily_signal_highlight(
    row: DailySignalRow, *, held_market_value_delta: float | None
) -> DailySignalHighlight:
    return DailySignalHighlight(
        component_stock_id=row.component_stock_id,
        component_stock_name=row.component_stock_name,
        held_market_value_delta=held_market_value_delta,
        buying_etf_count=row.buying_etf_count,
        selling_etf_count=row.selling_etf_count,
    )


def _daily_signal_highlight_if_significant(
    row: DailySignalRow | None,
    *,
    closes: dict[str, float],
    wants_positive_delta: bool,
) -> DailySignalHighlight | None:
    """`_daily_signal_highlight` for `row`, but only when it's actually a real inflow (positive
    `shares_delta`) or outflow (negative) — `top_inflow_row`/`top_outflow_row` are just the
    max/min of `rows`, which can legitimately be a net-sell or net-buy respectively when every
    stock moved the same direction today, and a highlight in the wrong direction would mislead a
    reader glancing at "焦點股". `None` when `row` is `None` (no rows at all) or wrong-signed."""
    # `row` always comes from the real (pre-mask) `rows` list built by `_daily_signal_rows` —
    # `shares_delta`/`component_stock_id` are only ever `None` on a row already masked for an
    # unentitled caller, which never happens before this point in `run()`.
    if row is None or row.shares_delta is None or row.component_stock_id is None:
        return None
    if wants_positive_delta and row.shares_delta <= 0:
        return None
    if not wants_positive_delta and row.shares_delta >= 0:
        return None
    close = closes.get(row.component_stock_id)
    held_market_value_delta = row.shares_delta * close if close is not None else None
    return _daily_signal_highlight(row, held_market_value_delta=held_market_value_delta)


class GetDailySignalTool(SyncStoreTool):
    """`get_daily_signal` — 今日訊號: whole-market, single-day 操作日報 aggregated across EVERY
    active ETF (never one ETF — that per-symbol drill-down is `get_active_etf_report`, used on
    the stock/ETF detail page). For each stock with a nonzero cross-ETF share change today:
    新增/刪除/加碼/減碼 status, 變動張數/幅度, and 多空共識 (買/賣 ETF count) — plus 焦點股
    highlights (the single largest inflow/outflow stock) and status-count summaries (新增/刪除/
    加碼/減碼 totals for the filter chips).

    Sourced from the same **ingest-owned Parquet store** derived-read pattern
    `GetEtfMoneyFlowTool` uses — never a live per-symbol router fan-out. `held_market_value_delta`
    on each highlight reuses `closes_by_symbol`/real EOD closes, never FinMind's own broken
    `market_value` field (see `_holdings_market_value`'s docstring)."""

    name = "get_daily_signal"
    description = (
        "Get today's whole-market active-ETF operations signal: per-stock 新增/刪除/加碼/減碼 "
        "status aggregated across every active ETF, share-count/percentage change, buying/"
        "selling ETF consensus, top inflow/outflow highlights, and status-count summary "
        "(Sponsor-tier)."
    )
    input_model: type[BaseModel] = GetDailySignalInput
    output_model: type[BaseModel] = GetDailySignalOutput
    data_class = DataClass.SPONSOR
    cache_ttl_seconds = 300.0  # whole-market derivation; recomputed when ingest writes new data
    card_type: str | None = "daily_signal"
    supports_row_preview: bool = True

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetDailySignalInput, args)
        market = typed.market.value
        target = typed.trade_date or date_.today()

        report_date = latest_ingested_date_on_or_before(
            self._store, ACTIVE_ETF_HOLDING_CHANGE_JOB.dataset, market, target
        )
        if report_date is None:
            return GetDailySignalOutput(
                market=market,
                data_available=False,
                unavailable_reason=(
                    "No ingested active-ETF holding-change partition found for "
                    f"{market} on or before {target.isoformat()} — the whole-market ingest job "
                    "may not have run yet."
                ),
            )
        # The prior HOLDING snapshot strictly before `report_date` — never the same-dataset scan
        # `_two_latest_ingested_dates_on_or_before` would give against `HoldingChange` (a daily
        # diff dataset, not a snapshot; its own "prior" partition date has no holdings meaning).
        _, prior_date = _two_latest_ingested_dates_on_or_before(
            self._store, ACTIVE_ETF_HOLDING_JOB.dataset, market, report_date
        )

        raw_changes = self._store.read_partition(
            ACTIVE_ETF_HOLDING_CHANGE_JOB.dataset, market, report_date
        )
        changes = rows_from_store(ActiveEtfHoldingChangeRow, raw_changes)

        report_holdings = rows_from_store(
            ActiveEtfHoldingRow,
            self._store.read_partition(ACTIVE_ETF_HOLDING_JOB.dataset, market, report_date),
        )
        prior_holdings = (
            rows_from_store(
                ActiveEtfHoldingRow,
                self._store.read_partition(ACTIVE_ETF_HOLDING_JOB.dataset, market, prior_date),
            )
            if prior_date is not None
            else ()
        )
        report_aggregate_shares = _aggregate_shares_by_stock(report_holdings)
        prior_aggregate_shares = _aggregate_shares_by_stock(prior_holdings)

        closes = closes_by_symbol(self._store, market, report_date)
        prior_closes = (
            closes_by_symbol(self._store, market, prior_date) if prior_date is not None else {}
        )
        rows = _daily_signal_rows(
            changes,
            _DailySignalContext(
                prior_aggregate_shares=prior_aggregate_shares,
                report_aggregate_shares=report_aggregate_shares,
                closes=closes,
                prior_closes=prior_closes,
            ),
            has_prior_day=prior_date is not None,
        )

        counts = dict.fromkeys(HoldingStatus, 0)
        for row in rows:
            counts[row.status] += 1

        # `rows` here is always the real (pre-mask) list — `shares_delta` is never `None` on it.
        top_inflow_row = max(rows, key=lambda r: r.shares_delta or 0, default=None)
        top_outflow_row = min(rows, key=lambda r: r.shares_delta or 0, default=None)
        top_inflow = _daily_signal_highlight_if_significant(
            top_inflow_row, closes=closes, wants_positive_delta=True
        )
        top_outflow = _daily_signal_highlight_if_significant(
            top_outflow_row, closes=closes, wants_positive_delta=False
        )

        entitled = is_entitled(self.data_class, ctx.tier)
        if entitled:
            output_rows: tuple[DailySignalRow, ...] = tuple(rows)
            output_top_inflow = top_inflow
            output_top_outflow = top_outflow
        else:
            output_rows = _mask_daily_signal_rows_beyond(rows, _DAILY_SIGNAL_FREE_ROW_COUNT)
            output_top_inflow = _mask_daily_signal_highlight(top_inflow)
            output_top_outflow = _mask_daily_signal_highlight(top_outflow)

        return GetDailySignalOutput(
            market=market,
            report_date=report_date,
            prior_date=prior_date,
            data_available=True,
            total_stock_count=len(rows),
            new_count=counts[HoldingStatus.NEW],
            removed_count=counts[HoldingStatus.REMOVED],
            increased_count=counts[HoldingStatus.INCREASED],
            decreased_count=counts[HoldingStatus.DECREASED],
            free_row_count=_DAILY_SIGNAL_FREE_ROW_COUNT,
            top_inflow=output_top_inflow,
            top_outflow=output_top_outflow,
            rows=output_rows,
        )
