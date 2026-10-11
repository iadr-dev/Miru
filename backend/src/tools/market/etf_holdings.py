"""Which active ETFs hold a given stock."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date as date_
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

from ingest.jobs import ACTIVE_ETF_HOLDING_CHANGE_JOB, ACTIVE_ETF_HOLDING_JOB
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import ActiveEtfHoldingChangeRow, ActiveEtfHoldingRow
from schemas.enums import DataClass
from tools.base import BaseTool, ToolContext, is_entitled, parse_args
from tools.common import SymbolInput
from tools.market._common import (
    HoldingStatus,
    classify_holding_status,
    ingested_dates,
    latest_ingested_date_on_or_before,
    prior_report_date,
    shares_outstanding_by_symbol,
)

_ETFS_HOLDING_STOCK_TOP_N = 50  # every active ETF ever holding a given TW stock is well under this


_ETFS_HOLDING_STOCK_QUARTER_DAYS = 63  # trading-day quarter, matches the reference app's 近一季


_ETFS_HOLDING_STOCK_EVENT_LOOKBACK_DAYS = 90  # bounded 建清倉 event-history window (calendar days)


_ETFS_HOLDING_STOCK_FREE_ROW_COUNT = 2  # governance.md §4 top-N-preview exception: top 2 rows of


class GetEtfsHoldingStockInput(SymbolInput):
    """One stock + an optional as-of date (defaults to the latest ingested holding snapshot)."""

    as_of: date_ | None = Field(
        default=None,
        description="Snapshot date to rank as of (ISO date). Omitted/null means the latest "
        "ingested active-ETF holding snapshot.",
    )


class EtfHoldingRankRow(BaseModel):
    """One ETF's current holding of the target stock — the 持有張數排行 sub-view's row shape.
    `locked=True` rows (governance.md §4 top-N-preview exception) have EVERY field nulled
    server-side, including `etf_symbol`/`etf_name` — an unentitled caller must not learn which
    ETF a locked row even belongs to, only that it exists (via `rank`, its 1-based position in
    the full ranked list, which is not sensitive on its own)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int  # 1-based position in the full ranked list — real even when locked
    etf_symbol: str | None  # null only when locked
    etf_name: str | None  # null only when locked
    shares: int | None  # 持有張數 (FinMind's own share units, not lots) — null only when locked
    shares_delta: int | None = None  # 張數增減 vs. the prior ingested snapshot; None on day 1
    stock_capital_pct: float | None = None  # 股本比重 — shares ÷ the target stock's own float
    stock_capital_pct_delta: float | None = None  # 股本比重增減% vs. the prior snapshot
    locked: bool = False  # True only for a row-preview-masked row, never for a real day-1 gap


class EtfWeightRankRow(BaseModel):
    """One ETF's current weighting of the target stock in ITS OWN portfolio — the 權重排行
    sub-view's row shape. `locked` — see `EtfHoldingRankRow`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int  # 1-based position in the full ranked list — real even when locked
    etf_symbol: str | None  # null only when locked
    etf_name: str | None  # null only when locked
    weight_pct: float | None  # 權重% of the ETF's own portfolio — null only when locked
    weight_pct_delta: float | None = None  # 權重增減% vs. the prior snapshot; None on day 1
    quarter_min_weight_pct: float | None = None  # 近一季 weight range floor
    quarter_max_weight_pct: float | None = None  # 近一季 weight range ceiling
    locked: bool = False


class EtfPositionEvent(BaseModel):
    """One ETF's day-over-day change in its holding of the target stock — 建倉/清倉 (opening/
    fully closing the position) or 加碼/減碼 (a same-day increase/decrease while still held) —
    the 建清倉 sub-view's row shape. `event` is any `HoldingStatus` value except
    `NO_PRIOR_DATA` (a day with no prior snapshot to diff against emits no event at all).
    `locked` — see `EtfHoldingRankRow`; a locked event keeps its real `event_date`/`event`
    classification (the timeline's own axis, not identifying) but nulls `etf_symbol`/`etf_name`/
    `shares_delta` — an unentitled caller must not learn which ETF made the move."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_date: date_
    etf_symbol: str | None  # null only when locked
    etf_name: str | None  # null only when locked
    event: HoldingStatus  # NEW/REMOVED/INCREASED/DECREASED — never NO_PRIOR_DATA
    shares_delta: int | None  # null only when locked
    locked: bool = False


class EtfPositionEventCounts(BaseModel):
    """新增/刪除/加碼/減碼 counts across `position_events` — the summary-strip shape, mirroring
    `GetDailySignalOutput`'s own `new_count`/`removed_count`/`increased_count`/`decreased_count`
    fields (same four-way classification, scoped to one stock instead of the whole market)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    new_count: int = 0
    removed_count: int = 0
    increased_count: int = 0
    decreased_count: int = 0


class GetEtfsHoldingStockOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    as_of: date_ | None = None
    prior_date: date_ | None = None
    data_available: bool = True
    unavailable_reason: str | None = None
    etf_count: int = 0  # 被N支ETF持有 — len(holding_rank), before the top-N truncation below
    # governance.md §4 top-N-preview exception: how many of EACH ranked list's leading rows are
    # real/unmasked. Equal to `etf_count` (i.e. nothing is locked) for an entitled caller —
    # `GetEtfsHoldingStockTool.run` only shrinks this for an unentitled one.
    free_row_count: int = 0
    holding_rank: tuple[EtfHoldingRankRow, ...] = Field(default_factory=tuple)
    weight_rank: tuple[EtfWeightRankRow, ...] = Field(default_factory=tuple)
    # 分佈圖 (the weight-bucket histogram the 權重排行 sub-view also shows) is deliberately NOT
    # precomputed here — it's a client-side bucketing of `weight_rank`'s own `weight_pct` values,
    # the same "cite the field directly, don't duplicate the derivation" split this codebase's
    # other pre-computed-summary fields (e.g. `ValuationBandSummary`) draw between "needs
    # multi-row math" (precomputed) and "needs one pass over already-returned rows" (client-side).
    position_event_counts: EtfPositionEventCounts = Field(default_factory=EtfPositionEventCounts)
    position_events: tuple[EtfPositionEvent, ...] = Field(default_factory=tuple)


def _mask_holding_rank_rows_beyond(
    rows: tuple[EtfHoldingRankRow, ...], free_row_count: int
) -> tuple[EtfHoldingRankRow, ...]:
    """governance.md §4 top-N-preview exception: nulls every field on each row past
    `free_row_count` — including `etf_symbol`/`etf_name` — never a client-side-only blur; the
    real number AND the real identity never leave this function for a masked row. `rank` (this
    row's 1-based position in the full list) is the only thing that survives masking."""
    return tuple(
        row
        if i < free_row_count
        else row.model_copy(
            update={
                "etf_symbol": None,
                "etf_name": None,
                "shares": None,
                "shares_delta": None,
                "stock_capital_pct": None,
                "stock_capital_pct_delta": None,
                "locked": True,
            }
        )
        for i, row in enumerate(rows)
    )


def _mask_weight_rank_rows_beyond(
    rows: tuple[EtfWeightRankRow, ...], free_row_count: int
) -> tuple[EtfWeightRankRow, ...]:
    return tuple(
        row
        if i < free_row_count
        else row.model_copy(
            update={
                "etf_symbol": None,
                "etf_name": None,
                "weight_pct": None,
                "weight_pct_delta": None,
                "quarter_min_weight_pct": None,
                "quarter_max_weight_pct": None,
                "locked": True,
            }
        )
        for i, row in enumerate(rows)
    )


def _mask_position_events_beyond(
    events: tuple[EtfPositionEvent, ...], free_row_count: int
) -> tuple[EtfPositionEvent, ...]:
    return tuple(
        event
        if i < free_row_count
        else event.model_copy(
            update={"etf_symbol": None, "etf_name": None, "shares_delta": None, "locked": True}
        )
        for i, event in enumerate(events)
    )


def _stock_capital_pcts(
    store: ParquetStore,
    market: str,
    symbol: str,
    dates: tuple[date_, date_ | None],
    holdings: tuple[Sequence[ActiveEtfHoldingRow], Sequence[ActiveEtfHoldingRow]],
) -> tuple[float | None, float | None]:
    """`(stock_capital_pct, prior_stock_capital_pct)` — the target stock's combined active-ETF
    holding as a % of its own shares-outstanding, on `dates[0]` (report) and `dates[1]` (prior).
    Extracted from `GetEtfsHoldingStockTool.run` purely to keep that method's own cognitive
    complexity down; has no state of its own."""
    report_date, prior_date = dates
    report_holdings, prior_holdings = holdings
    shares_outstanding = shares_outstanding_by_symbol(store, market, report_date)
    prior_shares_outstanding = (
        shares_outstanding_by_symbol(store, market, prior_date) if prior_date is not None else {}
    )
    outstanding = shares_outstanding.get(symbol)
    prior_outstanding = prior_shares_outstanding.get(symbol)
    report_total_shares = sum(h.shares for h in report_holdings)
    prior_total_shares = sum(h.shares for h in prior_holdings) if prior_holdings else None
    stock_capital_pct = report_total_shares / outstanding * 100 if outstanding else None
    prior_stock_capital_pct = (
        prior_total_shares / prior_outstanding * 100
        if prior_total_shares is not None and prior_outstanding
        else None
    )
    return stock_capital_pct, prior_stock_capital_pct


def _apply_row_preview_gate(
    *,
    entitled: bool,
    holding_rank: tuple[EtfHoldingRankRow, ...],
    weight_rank: tuple[EtfWeightRankRow, ...],
    position_events: tuple[EtfPositionEvent, ...],
) -> tuple[
    int, tuple[EtfHoldingRankRow, ...], tuple[EtfWeightRankRow, ...], tuple[EtfPositionEvent, ...]
]:
    """governance.md §4 top-N-preview exception, applied once for all three ranked lists: an
    entitled caller's `free_row_count` equals each list's own length (nothing masked); an
    unentitled caller's is capped at the fixed preview size and every row past it is masked
    server-side, right here — never a client-side-only blur."""
    if entitled:
        free_row_count = max(len(holding_rank), len(weight_rank), len(position_events))
        return free_row_count, holding_rank, weight_rank, position_events
    return (
        _ETFS_HOLDING_STOCK_FREE_ROW_COUNT,
        _mask_holding_rank_rows_beyond(holding_rank, _ETFS_HOLDING_STOCK_FREE_ROW_COUNT),
        _mask_weight_rank_rows_beyond(weight_rank, _ETFS_HOLDING_STOCK_FREE_ROW_COUNT),
        _mask_position_events_beyond(position_events, _ETFS_HOLDING_STOCK_FREE_ROW_COUNT),
    )


def _etf_holding_rank_rows(
    *,
    report_holdings: Sequence[ActiveEtfHoldingRow],
    prior_holdings_by_etf: dict[str, ActiveEtfHoldingRow],
    stock_capital_pct: float | None,
    prior_stock_capital_pct: float | None,
) -> tuple[EtfHoldingRankRow, ...]:
    rows: list[EtfHoldingRankRow] = []
    for holding in report_holdings:
        prior = prior_holdings_by_etf.get(holding.symbol)
        shares_delta = holding.shares - prior.shares if prior is not None else None
        rows.append(
            EtfHoldingRankRow(
                rank=0,  # placeholder — fixed up below once sorted, the real rank order
                etf_symbol=holding.symbol,
                etf_name=holding.component_stock_name,
                shares=holding.shares,
                shares_delta=shares_delta,
                stock_capital_pct=stock_capital_pct,
                stock_capital_pct_delta=(
                    stock_capital_pct - prior_stock_capital_pct
                    if stock_capital_pct is not None and prior_stock_capital_pct is not None
                    else None
                ),
            )
        )
    ranked = sorted(rows, key=lambda r: r.shares or 0, reverse=True)
    return tuple(row.model_copy(update={"rank": i + 1}) for i, row in enumerate(ranked))


def _etf_weight_rank_rows(
    *,
    report_holdings: Sequence[ActiveEtfHoldingRow],
    prior_holdings_by_etf: dict[str, ActiveEtfHoldingRow],
    quarter_holdings_by_etf: dict[str, list[ActiveEtfHoldingRow]],
) -> tuple[EtfWeightRankRow, ...]:
    rows: list[EtfWeightRankRow] = []
    for holding in report_holdings:
        prior = prior_holdings_by_etf.get(holding.symbol)
        quarter = quarter_holdings_by_etf.get(holding.symbol, [])
        quarter_weights = [h.weight for h in quarter]
        rows.append(
            EtfWeightRankRow(
                rank=0,  # placeholder — fixed up below once sorted, the real rank order
                etf_symbol=holding.symbol,
                etf_name=holding.component_stock_name,
                weight_pct=holding.weight,
                weight_pct_delta=(holding.weight - prior.weight) if prior is not None else None,
                quarter_min_weight_pct=min(quarter_weights) if quarter_weights else None,
                quarter_max_weight_pct=max(quarter_weights) if quarter_weights else None,
            )
        )
    ranked = sorted(rows, key=lambda r: r.weight_pct or 0.0, reverse=True)
    return tuple(row.model_copy(update={"rank": i + 1}) for i, row in enumerate(ranked))


def _etf_position_events(
    changes_by_date: Mapping[date_, Sequence[ActiveEtfHoldingChangeRow]],
    holdings_by_date_and_etf: dict[date_, dict[str, ActiveEtfHoldingRow]],
    ordered_dates: Sequence[date_],
) -> tuple[EtfPositionEvent, ...]:
    """Walks each consecutive pair of ingested snapshot dates and emits one event per ETF whose
    holding of the target stock changed — 建倉/清倉 when the holding crosses zero (prior day had
    no holding and this day does, or vice versa), 加碼/減碼 when both days are nonzero, via the
    same `classify_holding_status` `ActiveEtfReportRow`/`DailySignalRow` already use."""
    events: list[EtfPositionEvent] = []
    for i in range(1, len(ordered_dates)):
        prior_date, current_date = ordered_dates[i - 1], ordered_dates[i]
        prior_etfs = holdings_by_date_and_etf.get(prior_date, {})
        current_etfs = holdings_by_date_and_etf.get(current_date, {})
        for change in changes_by_date.get(current_date, ()):
            prior_row = prior_etfs.get(change.symbol)
            current_row = current_etfs.get(change.symbol)
            status = classify_holding_status(
                buy=change.buy,
                sell=change.sell,
                prior_weight=prior_row.weight if prior_row else None,
                current_weight=current_row.weight if current_row else None,
                has_prior_day=True,
            )
            events.append(
                EtfPositionEvent(
                    event_date=current_date,
                    etf_symbol=change.symbol,
                    etf_name=change.component_stock_name,
                    event=status,
                    shares_delta=change.buy - change.sell,
                )
            )
    return tuple(sorted(events, key=lambda e: e.event_date, reverse=True))


def _etf_position_event_counts(
    events: Sequence[EtfPositionEvent],
) -> EtfPositionEventCounts:
    counts = dict.fromkeys(HoldingStatus, 0)
    for event in events:
        counts[event.event] += 1
    return EtfPositionEventCounts(
        new_count=counts[HoldingStatus.NEW],
        removed_count=counts[HoldingStatus.REMOVED],
        increased_count=counts[HoldingStatus.INCREASED],
        decreased_count=counts[HoldingStatus.DECREASED],
    )


class GetEtfsHoldingStockTool(BaseTool):
    """`get_etfs_holding_stock` — the reverse of `get_active_etf_report`: every active-
    management ETF currently holding one target stock, with three derived views (持有張數排行/
    權重排行/建清倉) computed from the same already-ingested `ACTIVE_ETF_HOLDING_JOB`/
    `ACTIVE_ETF_HOLDING_CHANGE_JOB` whole-market Parquet partitions `get_etf_money_flow`/
    `get_daily_signal` already read — grouped by `component_stock_id == symbol` instead of
    aggregated across every stock. Never a live per-ETF fan-out across the ~300-ETF universe
    (that would be exactly the full-market-scan shape the whole-market ingest job exists to
    avoid); this reads the derived store the same way every other cross-ETF tool in this module
    does.

    Backed by FinMind's `TaiwanStockActiveETFHolding` dataset, which covers active-management
    ETFs only — passive index ETFs (0050/00878/006208/etc.) publish their daily holdings
    per-issuer with no unified feed on either TWSE's or TPEx's official OpenAPI catalogs, so they
    are not represented here.

    `supports_row_preview=True` (governance.md §4 top-N-preview exception): an unentitled caller
    still gets this tool's real row shape/rank/labels, with every row past
    `_ETFS_HOLDING_STOCK_FREE_ROW_COUNT` having its value fields nulled in `run()` itself — the
    masking happens here, server-side, before the response leaves this function; `dispatch_tool`
    never sees or needs to see the real numbers for the caller it already knows is unentitled."""

    name = "get_etfs_holding_stock"
    description = (
        "Get every active-management ETF holding one stock: holding-shares ranking, "
        "weight-in-ETF ranking, and recent holding-change events (建倉/清倉/加碼/減碼) — the "
        "reverse of get_active_etf_report (Sponsor-tier; top rows free, rest row-locked)."
    )
    input_model: type[BaseModel] = GetEtfsHoldingStockInput
    output_model: type[BaseModel] = GetEtfsHoldingStockOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "etfs_holding_stock"
    supports_row_preview: bool = True

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def _holdings_of_stock(
        self, market: str, symbol: str, as_of_date: date_
    ) -> tuple[ActiveEtfHoldingRow, ...]:
        raw = self._store.read_partition(ACTIVE_ETF_HOLDING_JOB.dataset, market, as_of_date)
        return tuple(
            h for h in rows_from_store(ActiveEtfHoldingRow, raw) if h.component_stock_id == symbol
        )

    def _quarter_holdings_by_etf(
        self, market: str, symbol: str, report_date: date_
    ) -> tuple[list[date_], dict[str, list[ActiveEtfHoldingRow]]]:
        """Every ingested date in the trailing `_ETFS_HOLDING_STOCK_QUARTER_DAYS` window, plus
        the same holdings grouped by ETF — backs both `_etf_weight_rank_rows`' 近一季 range and
        the event-lookback window below."""
        quarter_dates = [
            d
            for d in ingested_dates(self._store, ACTIVE_ETF_HOLDING_JOB.dataset, market)
            if d <= report_date
        ][-_ETFS_HOLDING_STOCK_QUARTER_DAYS:]
        by_etf: dict[str, list[ActiveEtfHoldingRow]] = {}
        for quarter_date in quarter_dates:
            for holding in self._holdings_of_stock(market, symbol, quarter_date):
                by_etf.setdefault(holding.symbol, []).append(holding)
        return quarter_dates, by_etf

    def _position_events_for(
        self, market: str, symbol: str, report_date: date_, quarter_dates: Sequence[date_]
    ) -> tuple[EtfPositionEvent, ...]:
        """Bounded 建清倉 event history: every ingested date within the event-lookback window
        (falling back to the last 2 quarter dates if that window is empty), diffed via
        `_etf_position_events`."""
        event_start = report_date - timedelta(days=_ETFS_HOLDING_STOCK_EVENT_LOOKBACK_DAYS)
        event_dates = [d for d in quarter_dates if d >= event_start] or list(quarter_dates[-2:])
        changes_by_date: dict[date_, tuple[ActiveEtfHoldingChangeRow, ...]] = {}
        holdings_by_date_and_etf: dict[date_, dict[str, ActiveEtfHoldingRow]] = {}
        for event_date in event_dates:
            raw_changes = self._store.read_partition(
                ACTIVE_ETF_HOLDING_CHANGE_JOB.dataset, market, event_date
            )
            changes_by_date[event_date] = tuple(
                c
                for c in rows_from_store(ActiveEtfHoldingChangeRow, raw_changes)
                if c.component_stock_id == symbol
            )
            holdings_by_date_and_etf[event_date] = {
                h.symbol: h for h in self._holdings_of_stock(market, symbol, event_date)
            }
        return _etf_position_events(changes_by_date, holdings_by_date_and_etf, event_dates)

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetEtfsHoldingStockInput, args)
        market = typed.market.value
        target = typed.as_of or date_.today()

        report_date = latest_ingested_date_on_or_before(
            self._store, ACTIVE_ETF_HOLDING_JOB.dataset, market, target
        )
        if report_date is None:
            return GetEtfsHoldingStockOutput(
                symbol=typed.id,
                market=market,
                data_available=False,
                unavailable_reason=(
                    f"No ingested active-ETF holding partition found for {market} on or before "
                    f"{target.isoformat()} — the whole-market ingest job may not have run yet."
                ),
            )

        report_holdings = self._holdings_of_stock(market, typed.id, report_date)
        if not report_holdings:
            return GetEtfsHoldingStockOutput(
                symbol=typed.id,
                market=market,
                as_of=report_date,
                data_available=False,
                unavailable_reason=(
                    f"No active ETF held {typed.id} as of {report_date.isoformat()}."
                ),
            )

        prior_date = prior_report_date(
            list(ingested_dates(self._store, ACTIVE_ETF_HOLDING_JOB.dataset, market)),
            report_date,
        )
        prior_holdings = (
            self._holdings_of_stock(market, typed.id, prior_date) if prior_date is not None else ()
        )
        prior_holdings_by_etf = {h.symbol: h for h in prior_holdings}
        stock_capital_pct, prior_stock_capital_pct = _stock_capital_pcts(
            self._store,
            market,
            typed.id,
            (report_date, prior_date),
            (report_holdings, prior_holdings),
        )

        quarter_dates, quarter_holdings_by_etf = self._quarter_holdings_by_etf(
            market, typed.id, report_date
        )
        position_events = self._position_events_for(market, typed.id, report_date, quarter_dates)

        holding_rank = _etf_holding_rank_rows(
            report_holdings=report_holdings,
            prior_holdings_by_etf=prior_holdings_by_etf,
            stock_capital_pct=stock_capital_pct,
            prior_stock_capital_pct=prior_stock_capital_pct,
        )[:_ETFS_HOLDING_STOCK_TOP_N]
        weight_rank = _etf_weight_rank_rows(
            report_holdings=report_holdings,
            prior_holdings_by_etf=prior_holdings_by_etf,
            quarter_holdings_by_etf=quarter_holdings_by_etf,
        )[:_ETFS_HOLDING_STOCK_TOP_N]

        free_row_count, holding_rank, weight_rank, position_events = _apply_row_preview_gate(
            entitled=is_entitled(self.data_class, ctx.tier),
            holding_rank=holding_rank,
            weight_rank=weight_rank,
            position_events=position_events,
        )

        return GetEtfsHoldingStockOutput(
            symbol=typed.id,
            market=market,
            as_of=report_date,
            prior_date=prior_date,
            data_available=True,
            etf_count=len(report_holdings),
            free_row_count=free_row_count,
            holding_rank=holding_rank,
            weight_rank=weight_rank,
            position_event_counts=_etf_position_event_counts(position_events),
            position_events=position_events,
        )
