"""處置王-style disposition board — `GetDispositionBoardTool`. Reads the ingest-owned
`attention_stocks`/`disposition_events` partitions (`ingest/jobs.py::job_attention_stocks`/
`job_disposition_events`) for bucket classification + a rolling-window escalation projection,
joins already-ingested `PRICE_EOD_JOB`/`INSTITUTIONAL_FLOW_JOB` partitions for price/flow, and
fans out `get_margin_short` per-symbol bounded to that day's roster only (never a whole-market
live scan — `market_short` has no whole-market ingest job, confirmed via grep).

Sponsor-tier: same risk-signal family as 大戶加碼股/融資維持率 (`chip_tools.py`).
"""

from __future__ import annotations

import math
import re
from calendar.trading_days import TradingDayCalendar, calendar_from_known_dates
from collections.abc import Sequence
from datetime import date as date_
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from config.settings import get_settings
from dataaccess.router import DataAccessRouter
from ingest.jobs import (
    ATTENTION_STOCKS_DATASET,
    DISPOSITION_EVENTS_DATASET,
    INSTITUTIONAL_FLOW_JOB,
    PRICE_EOD_JOB,
)
from ingest.store import ParquetStore
from schemas.capabilities import (
    AttentionStockRow,
    DayTradingRow,
    DispositionBoardCardRow,
    DispositionEventRow,
    InstitutionalFlowRow,
    MarginShortRow,
    SelfAssessedEarningsRow,
)
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceResult
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext, is_entitled, parse_args

# TWSE's own publicly-cited escalation pattern is "repeat 注意股 flags within a rolling window
# escalate to 處置" (examples seen live: "連續三次"/"連續五次及當日沖銷標準") — no single official
# figure is pinned in this codebase's own research, so this is a documented, correctable
# encoding of that pattern, not a verbatim TWSE-published constant. See
# `DispositionBoardCardRow.projection_caveat`.
_ATTENTION_FLAGS_TO_DISPOSITION = 3
_ROLLING_WINDOW_TRADING_DAYS = 10
_EXITING_SOON_WINDOW_DAYS = 5

_PROJECTION_CAVEAT = (
    f"'attention_flags_remaining' is this codebase's own rule-based estimate — "
    f"{_ATTENTION_FLAGS_TO_DISPOSITION} 注意股 flags within a rolling "
    f"{_ROLLING_WINDOW_TRADING_DAYS}-trading-day window — not a verbatim TWSE-published figure, "
    "and is bounded by however much attention-flag history has accumulated since this "
    "feature's ingest jobs first ran."
)

_BUCKET_IN_DISPOSITION = "in_disposition"
_BUCKET_EXITING_SOON = "exiting_soon"
_BUCKET_AT_RISK = "at_risk"

_DISPOSITION_BOARD_FREE_ROW_COUNT = 2  # governance.md §4 top-N-preview exception — per bucket


def _ranked_cards_by_bucket(
    cards: Sequence[DispositionBoardCardRow],
) -> tuple[DispositionBoardCardRow, ...]:
    """Stamps each card with a 1-based `rank` WITHIN its own bucket (roster order — already
    sorted by symbol) — the top-N-preview exception's free-row budget is per bucket, not global,
    since a caller viewing the 即將出關 tab shouldn't see zero free rows just because 處置中 used
    up the whole-board budget."""
    counters: dict[str, int] = {}
    ranked: list[DispositionBoardCardRow] = []
    for card in cards:
        counters[card.bucket] = counters.get(card.bucket, 0) + 1
        ranked.append(card.model_copy(update={"rank": counters[card.bucket]}))
    return tuple(ranked)


def _apply_row_preview_gate(
    cards: Sequence[DispositionBoardCardRow], *, entitled: bool
) -> tuple[DispositionBoardCardRow, ...]:
    """Ranks every card within its own bucket, then masks beyond the free-row budget for an
    unentitled caller — split out of `GetDispositionBoardTool.run` purely to keep that method
    under ruff's statement cap (this is pure post-processing, no I/O of its own)."""
    ranked = _ranked_cards_by_bucket(cards)
    if entitled:
        return ranked
    return _mask_disposition_cards_beyond(ranked, _DISPOSITION_BOARD_FREE_ROW_COUNT)


def _mask_disposition_cards_beyond(
    cards: Sequence[DispositionBoardCardRow], free_row_count: int
) -> tuple[DispositionBoardCardRow, ...]:
    """Cards beyond `free_row_count` (within their own bucket) get every identity/value field
    nulled server-side — `rank`/`bucket`/`market`/`projection_caveat` survive, matching
    `_mask_daily_signal_rows_beyond`'s contract."""
    masked: list[DispositionBoardCardRow] = []
    for card in cards:
        if card.rank <= free_row_count:
            masked.append(card)
            continue
        masked.append(
            DispositionBoardCardRow(
                rank=card.rank,
                symbol=None,
                market=card.market,
                company_name=None,
                security_type=None,
                bucket=card.bucket,
                projection_caveat=card.projection_caveat,
                locked=True,
            )
        )
    return tuple(masked)


class _SymbolEnrichment(NamedTuple):
    """One roster symbol's bundled per-symbol fetch results — see
    `GetDispositionBoardTool._fetch_symbol_enrichment`."""

    margin_row: MarginShortRow | None
    margin_caveat_and_health: tuple[str, HealthStatus]
    day_trading_row: DayTradingRow | None
    self_assessed_row: SelfAssessedEarningsRow | None


def _rows_from_store[T: BaseModel](model: type[T], raw_rows: list[dict[str, object]]) -> list[T]:
    """Same drop-storage-bookkeeping-columns-before-validating helper
    `tools/mops_tools.py::_rows_from_store` already documents — duplicated here (not imported)
    since it's private to that module."""
    field_names = set(model.model_fields)
    return [
        model.model_validate({k: v for k, v in row.items() if k in field_names}) for row in raw_rows
    ]


def _calendar(store: ParquetStore, market: str) -> TradingDayCalendar:
    """Same construction `market_tools.py::_real_calendar` uses — duplicated here since
    `tools/` modules don't import each other's private helpers."""
    known_dates = store.list_partition_dates(PRICE_EOD_JOB.dataset, market)
    if not known_dates:
        return TradingDayCalendar()
    return calendar_from_known_dates(known_dates)


def _latest_dataset_date(store: ParquetStore, dataset: str, market: str) -> date_ | None:
    dates = store.list_partition_dates(dataset, market)
    return max(dates) if dates else None


def _latest_ingested_date_on_or_before(
    store: ParquetStore, dataset: str, market: str, on_or_before: date_
) -> date_ | None:
    """Same shape as `_two_latest_dates_on_or_before`, single-date version — see that function's
    docstring for why an exact-date read isn't safe here."""
    dates = [d for d in store.list_partition_dates(dataset, market) if d <= on_or_before]
    return max(dates) if dates else None


def _two_latest_dates_on_or_before(
    store: ParquetStore, dataset: str, market: str, on_or_before: date_
) -> tuple[date_ | None, date_ | None]:
    """`(latest, prior)` — the two most recent dates `dataset`+`market` has an actually-written
    partition for, at or before `on_or_before`. Same shape as
    `market_tools.py::_two_latest_ingested_dates_on_or_before` (duplicated, not imported — that
    module's helper is private) — needed because `PRICE_EOD_JOB`/`INSTITUTIONAL_FLOW_JOB` lag
    behind `disposition_events`'s own report date in a sparse dev backfill (their upstream
    cadence isn't 1:1 with TWSE/TPEx's snapshot-only feed); reading exactly `report_date`'s own
    partition would silently return an empty map instead of falling back to the latest real one,
    which is what previously left every card's price/change/turnover blank whenever the two
    datasets' latest dates didn't happen to line up exactly."""
    dates = [d for d in store.list_partition_dates(dataset, market) if d <= on_or_before]
    if not dates:
        return None, None
    ordered = sorted(dates, reverse=True)
    latest = ordered[0]
    prior = ordered[1] if len(ordered) > 1 else None
    return latest, prior


_BarInfo = tuple[float, int, float | None]


def _closes_by_symbol(store: ParquetStore, market: str, trade_date: date_) -> dict[str, _BarInfo]:
    """`symbol -> (close, volume, turnover)` for `trade_date`, from `PRICE_EOD_JOB`'s partition."""
    raw_bars = store.read_partition(PRICE_EOD_JOB.dataset, market, trade_date)
    out: dict[str, _BarInfo] = {}
    for row in raw_bars:
        symbol = row.get("symbol")
        close = row.get("close")
        volume = row.get("volume")
        if not isinstance(symbol, str) or not isinstance(close, (int, float)):
            continue
        if not isinstance(volume, int):
            continue
        turnover = row.get("turnover")
        turnover_value = float(turnover) if isinstance(turnover, (int, float)) else None
        out[symbol] = (float(close), volume, turnover_value)
    return out


def _institutional_net_by_symbol(
    store: ParquetStore, market: str, trade_date: date_
) -> dict[str, int]:
    """Sum of `buy - sell` across every investor category, per symbol, for `trade_date` — read
    back from `INSTITUTIONAL_FLOW_JOB`'s already-ingested partition, never fetched live."""
    dataset = INSTITUTIONAL_FLOW_JOB.dataset
    if not store.has_partition(dataset, market, trade_date):
        return {}
    raw_rows = store.read_partition(dataset, market, trade_date)
    net: dict[str, int] = {}
    for row in _rows_from_store(InstitutionalFlowRow, raw_rows):
        net[row.symbol] = net.get(row.symbol, 0) + (row.buy - row.sell)
    return net


class GetDispositionBoardOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    report_date: date_ | None = None
    data_available: bool
    unavailable_reason: str | None = None
    bucket_counts: dict[str, int] = Field(default_factory=dict)
    free_row_count: int = 0  # governance.md §4 top-N-preview exception
    cards: tuple[DispositionBoardCardRow, ...] = Field(default_factory=tuple)


class GetDispositionBoardInput(BaseModel):
    """A market + an optional trading day; omitted `trade_date` means the latest date the
    `disposition_events`/`attention_stocks` ingest jobs have actually captured."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW


class GetDispositionBoardTool(BaseTool):
    """`get_disposition_board` — 處置王-style board: 風險股 (at_risk) / 處置中 (in_disposition) /
    即將出關 (exiting_soon) buckets, joined with price/institutional-flow/margin. 自結發布 is out
    of scope this pass (see the plan — no confirmed data source exists for it).

    Bucket rule: 處置中 = today falls within a `DispositionEventRow`'s `period_start..period_end`;
    即將出關 = currently in 處置中 AND `period_end` is within `_EXITING_SOON_WINDOW_DAYS` trading
    days; 風險股 = flagged in the rolling `attention_stocks` window but not currently disposed.
    A symbol in an active disposition period is never also counted as 風險股 — 處置中/即將出關
    are mutually exclusive with 風險股 (already escalated, not merely at risk of escalating)."""

    name = "get_disposition_board"
    description = (
        "Get the 處置王-style disposition board: 風險股/處置中/即將出關 buckets of stocks flagged "
        "注意股 or under 處置, each with price, institutional flow, margin ratio, and a "
        "rule-based 'days until next disposition' projection."
    )
    input_model: type[BaseModel] = GetDispositionBoardInput
    output_model: type[BaseModel] = GetDispositionBoardOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "disposition_board"
    supports_row_preview: bool = True

    def __init__(self, store: ParquetStore, router: DataAccessRouter) -> None:
        self._store = store
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetDispositionBoardInput, args)
        market = typed.market.value

        report_date = _latest_dataset_date(self._store, DISPOSITION_EVENTS_DATASET, market)
        if report_date is None:
            return GetDispositionBoardOutput(
                market=market,
                data_available=False,
                unavailable_reason="Disposition-board ingest has not captured any day yet for "
                "this market — history only starts accumulating once "
                "job_attention_stocks/job_disposition_events first run; there is no backfill.",
            )

        disposition_rows = _rows_from_store(
            DispositionEventRow,
            self._store.read_partition(DISPOSITION_EVENTS_DATASET, market, report_date),
        )
        by_symbol_disposition: dict[str, DispositionEventRow] = {}
        for row in disposition_rows:
            existing = by_symbol_disposition.get(row.symbol)
            if existing is None or row.period_end > existing.period_end:
                by_symbol_disposition[row.symbol] = row

        calendar = _calendar(self._store, market)
        window_offset = -(_ROLLING_WINDOW_TRADING_DAYS - 1)
        window_start = calendar.shift_business_days(report_date, window_offset)
        flag_counts: dict[str, int] = {}
        flag_names: dict[str, set[str]] = {}
        attention_info: dict[str, AttentionStockRow] = {}
        observed_days = 0
        for day in calendar.business_days_between(window_start, report_date):
            if not self._store.has_partition(ATTENTION_STOCKS_DATASET, market, day):
                continue
            observed_days += 1
            for attention_row in _rows_from_store(
                AttentionStockRow, self._store.read_partition(ATTENTION_STOCKS_DATASET, market, day)
            ):
                flag_counts[attention_row.symbol] = flag_counts.get(attention_row.symbol, 0) + 1
                flag_names.setdefault(attention_row.symbol, set()).add("注意")
                # Latest flag's own name/exchange wins — a symbol's company name/listing never
                # actually changes within a 10-day window, but `day` walks oldest-to-newest here
                # so this naturally keeps the most recent snapshot without extra bookkeeping.
                attention_info[attention_row.symbol] = attention_row

        at_risk_symbols = set(flag_counts) - set(by_symbol_disposition)
        roster = sorted(set(by_symbol_disposition) | at_risk_symbols)

        price_date, price_prior_date = _two_latest_dates_on_or_before(
            self._store, PRICE_EOD_JOB.dataset, market, report_date
        )
        closes = _closes_by_symbol(self._store, market, price_date) if price_date else {}
        prior_bars = (
            _closes_by_symbol(self._store, market, price_prior_date) if price_prior_date else {}
        )
        prior_closes = {symbol: bar[0] for symbol, bar in prior_bars.items()}
        flow_date = _latest_ingested_date_on_or_before(
            self._store, INSTITUTIONAL_FLOW_JOB.dataset, market, report_date
        )
        institutional_net = (
            _institutional_net_by_symbol(self._store, market, flow_date) if flow_date else {}
        )

        margin_by_symbol: dict[str, MarginShortRow] = {}
        margin_caveat_by_symbol: dict[str, tuple[str, HealthStatus]] = {}
        day_trading_by_symbol: dict[str, DayTradingRow] = {}
        self_assessed_by_symbol: dict[str, SelfAssessedEarningsRow] = {}
        candidate_periods = _recent_self_assessed_periods(report_date)
        for symbol in roster:
            enrichment = await self._fetch_symbol_enrichment(
                symbol,
                typed.market,
                price_date or report_date,
                _security_type_for(by_symbol_disposition.get(symbol), attention_info.get(symbol)),
                candidate_periods,
                ctx,
            )
            margin_caveat_by_symbol[symbol] = enrichment.margin_caveat_and_health
            if enrichment.margin_row is not None:
                margin_by_symbol[symbol] = enrichment.margin_row
            if enrichment.day_trading_row is not None:
                day_trading_by_symbol[symbol] = enrichment.day_trading_row
            if enrichment.self_assessed_row is not None:
                self_assessed_by_symbol[symbol] = enrichment.self_assessed_row

        cards: list[DispositionBoardCardRow] = []
        bucket_counts: dict[str, int] = {}
        for symbol in roster:
            card = _build_card(
                symbol=symbol,
                market=typed.market,
                report_date=report_date,
                calendar=calendar,
                event=by_symbol_disposition.get(symbol),
                attention_row=attention_info.get(symbol),
                flag_names=flag_names.get(symbol, set()),
                flag_count=flag_counts.get(symbol, 0),
                observed_days=observed_days,
                close_info=closes.get(symbol),
                prior_close=prior_closes.get(symbol),
                institutional_net=institutional_net.get(symbol),
                margin_row=margin_by_symbol.get(symbol),
                margin_caveat_and_health=margin_caveat_by_symbol[symbol],
                day_trading_row=day_trading_by_symbol.get(symbol),
                self_assessed_row=self_assessed_by_symbol.get(symbol),
            )
            bucket_counts[card.bucket] = bucket_counts.get(card.bucket, 0) + 1
            cards.append(card)

        output_cards = _apply_row_preview_gate(
            cards, entitled=is_entitled(self.data_class, ctx.tier)
        )

        return GetDispositionBoardOutput(
            market=market,
            report_date=report_date,
            data_available=True,
            bucket_counts=bucket_counts,
            free_row_count=_DISPOSITION_BOARD_FREE_ROW_COUNT,
            cards=output_cards,
        )

    async def _fetch_symbol_enrichment(  # noqa: PLR0913, PLR0917 — every arg is one
        # already-resolved lookup for this symbol, the same shape `_build_card` itself uses.
        self,
        symbol: str,
        market: Market,
        matching_trade_date: date_,
        security_type: str,
        candidate_periods: tuple[str, ...],
        ctx: ToolContext,
    ) -> _SymbolEnrichment:
        """The three bounded per-symbol fetches (margin/day-trading/self-assessed-earnings) for
        one roster symbol, bundled into one call so `run`'s own per-symbol loop body stays under
        ruff's statement cap — this is pure fan-out orchestration, no new fetch logic of its
        own."""
        margin_result, margin_rows = await self._fetch_margin(symbol, market, ctx)
        _, day_trading_rows = await self._fetch_day_trading(symbol, market, ctx)
        # `rows[-1]` (the fetch's own latest row) is NOT necessarily `matching_trade_date`'s own
        # day — day-trading's own upstream cadence can lag behind price_eod's (the same class of
        # mismatch `_two_latest_dates_on_or_before` exists to guard against for the price join),
        # and silently pairing a stale day-trading volume with today's total volume produces a
        # nonsense ratio (seen live: >100%, up to ~800%). Only a row whose OWN `trade_date`
        # matches the date `volume`/`close` actually come from is valid.
        matching_day_trading_rows = [
            row for row in day_trading_rows if row.trade_date == matching_trade_date
        ]
        self_assessed_row = await self._fetch_self_assessed_earnings(
            symbol, market, security_type, candidate_periods, ctx
        )
        margin_caveat_and_health = (margin_result.currentness_caveat, margin_result.health.status)
        return _SymbolEnrichment(
            margin_row=margin_rows[-1] if margin_rows else None,
            margin_caveat_and_health=margin_caveat_and_health,
            day_trading_row=matching_day_trading_rows[-1] if matching_day_trading_rows else None,
            self_assessed_row=self_assessed_row,
        )

    async def _fetch_margin(
        self, symbol: str, market: Market, ctx: ToolContext
    ) -> tuple[SourceResult, tuple[MarginShortRow, ...]]:
        """One bounded per-symbol `get_margin_short`-equivalent fetch — the roster this loops
        over is only that day's 3-bucket symbols (never a whole-market scan)."""
        result, rows = await fetch_rows(
            self._router,
            Capability.MARGIN_SHORT,
            Symbol(id=symbol, market=market),
            tls_policy=ctx.tls_policy,
            model=MarginShortRow,
            key="rows",
        )
        return result, rows

    async def _fetch_day_trading(
        self, symbol: str, market: Market, ctx: ToolContext
    ) -> tuple[SourceResult, tuple[DayTradingRow, ...]]:
        """One bounded per-symbol `get_day_trading`-equivalent fetch — same fan-out shape as
        `_fetch_margin`, over the same small roster (never a whole-market scan)."""
        result, rows = await fetch_rows(
            self._router,
            Capability.DAY_TRADING,
            Symbol(id=symbol, market=market),
            tls_policy=ctx.tls_policy,
            model=DayTradingRow,
            key="rows",
        )
        return result, rows

    async def _fetch_self_assessed_earnings(
        self,
        symbol: str,
        market: Market,
        security_type: str,
        candidate_periods: tuple[str, ...],
        ctx: ToolContext,
    ) -> SelfAssessedEarningsRow | None:
        """One bounded per-symbol `get_self_assessed_earnings`-equivalent fetch, tried across
        `candidate_periods` (most recent first) until a filing is found — the upstream form
        takes one period per POST, and most stocks in most months have not filed (a voluntary
        disclosure), so this walks a short recent window rather than one fixed period. Prefers a
        consolidated (合併) row over an individual (個別) one when a company filed both for the
        same period, matching how the reference StockWarden-style board reads consolidated
        figures when available. Never a whole-market scan — same bounded-roster shape as
        `_fetch_margin`/`_fetch_day_trading`."""
        for period in candidate_periods:
            _, rows = await fetch_rows(
                self._router,
                Capability.SELF_ASSESSED_EARNINGS,
                Symbol(id=symbol, market=market),
                tls_policy=ctx.tls_policy,
                model=SelfAssessedEarningsRow,
                key="rows",
                params={"period": period, "security_type": security_type},
            )
            if not rows:
                continue
            consolidated_rows = [row for row in rows if row.consolidated]
            return consolidated_rows[-1] if consolidated_rows else rows[-1]
        return None


_SELF_ASSESSED_LOOKBACK_MONTHS = 2


def _recent_self_assessed_periods(report_date: date_) -> tuple[str, ...]:
    """The last `_SELF_ASSESSED_LOOKBACK_MONTHS` + 1 ISO "YYYY-MM" periods on or before
    `report_date`, most recent first — a company's monthly 自結損益 filing is due before the
    10th of the FOLLOWING month, so `report_date`'s own month's filing may not exist yet even
    late in that month; walking back a couple of months finds the most recent one that has."""
    periods: list[str] = []
    year, month = report_date.year, report_date.month
    for _ in range(_SELF_ASSESSED_LOOKBACK_MONTHS + 1):
        periods.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return tuple(periods)


def _security_type_for(
    event: DispositionEventRow | None, attention_row: AttentionStockRow | None
) -> str:
    """Same fallback `_build_card` uses for `security_type` on the card itself — duplicated here
    (not shared via a helper) since this call site needs it BEFORE `_build_card` runs, to pick
    which market code to POST for the self-assessed-earnings fetch."""
    if event:
        return event.security_type
    if attention_row:
        return attention_row.security_type
    return "twse"


def _build_card(  # noqa: PLR0913 — every kwarg is one already-resolved lookup for this symbol;
    # splitting into a request object would only move the same argument count one level out.
    *,
    symbol: str,
    market: Market,
    report_date: date_,
    calendar: TradingDayCalendar,
    event: DispositionEventRow | None,
    attention_row: AttentionStockRow | None,
    flag_names: set[str],
    flag_count: int,
    observed_days: int,
    close_info: _BarInfo | None,
    prior_close: float | None,
    institutional_net: int | None,
    margin_row: MarginShortRow | None,
    margin_caveat_and_health: tuple[str, HealthStatus],
    day_trading_row: DayTradingRow | None,
    self_assessed_row: SelfAssessedEarningsRow | None,
) -> DispositionBoardCardRow:
    """One roster symbol's full card — the bucket classification + every joined field, factored
    out of `GetDispositionBoardTool.run` so that method stays under ruff's branch/statement caps
    (this is pure per-symbol assembly, no I/O of its own)."""
    days_until_exiting: int | None = None
    if event is not None:
        days_remaining = (
            calendar.business_days_between(report_date, event.period_end)
            if event.period_end >= report_date
            else ()
        )
        # `-1`: `business_days_between` counts `report_date` itself as the first element (an
        # inclusive range), but "days until exiting" means days remaining AFTER today, so today
        # itself must not count as one of them — a disposition ending today is 0 days out, not 1.
        days_until_exiting = max(len(days_remaining) - 1, 0)
        bucket = (
            _BUCKET_EXITING_SOON
            if 0 < len(days_remaining) <= _EXITING_SOON_WINDOW_DAYS
            else _BUCKET_IN_DISPOSITION
        )
    else:
        bucket = _BUCKET_AT_RISK

    close, volume, turnover = close_info if close_info else (None, None, None)
    change_pct = (
        (close - prior_close) / prior_close * 100 if close is not None and prior_close else None
    )

    active_flags: set[str] = set(flag_names)
    if event:
        active_flags.add("處")
    if margin_row and margin_row.margin_purchase_today_balance > 0:
        active_flags.add("資")
    if margin_row and margin_row.short_sale_today_balance > 0:
        active_flags.add("券")
    if day_trading_row and day_trading_row.volume > 0:
        active_flags.add("沖")
    margin_ratio = (
        margin_row.short_sale_today_balance / margin_row.margin_purchase_today_balance * 100
        if margin_row and margin_row.margin_purchase_today_balance
        else None
    )
    day_trading_ratio = (
        day_trading_row.volume / volume * 100 if day_trading_row and volume else None
    )
    margin_caveat, margin_health = margin_caveat_and_health
    flags_remaining = None if event is not None else _project_days_until_disposition(flag_count)
    estimated_days = (
        None
        if event is not None
        else _project_days_by_rate(flag_count, observed_days, _ATTENTION_FLAGS_TO_DISPOSITION)
    )

    company_name = (
        event.company_name if event else (attention_row.company_name if attention_row else "")
    )
    security_type = (
        event.security_type if event else (attention_row.security_type if attention_row else "twse")
    )

    return DispositionBoardCardRow(
        symbol=symbol,
        market=market,
        company_name=company_name,
        security_type=security_type,
        close=close,
        change_pct=change_pct,
        volume=volume,
        turnover=turnover,
        institutional_net=institutional_net,
        margin_ratio=margin_ratio,
        margin_caveat=margin_caveat,
        margin_health_status=margin_health.value,
        day_trading_ratio=day_trading_ratio,
        bucket=bucket,
        disposition_measure=event.measure if event else None,
        disposition_period_start=event.period_start if event else None,
        disposition_period_end=event.period_end if event else None,
        matching_interval_minutes=(
            _parse_matching_interval_minutes(event.detail, event.measure) if event else None
        ),
        flags=tuple(sorted(active_flags)),
        attention_flags_remaining=flags_remaining,
        estimated_days_until_disposition=estimated_days,
        days_until_exiting=days_until_exiting,
        self_assessed_period=self_assessed_row.revenue_period if self_assessed_row else None,
        self_assessed_consolidated=self_assessed_row.consolidated if self_assessed_row else None,
        self_assessed_monthly_pretax_profit=(
            self_assessed_row.monthly_pretax_profit if self_assessed_row else None
        ),
        self_assessed_ytd_pretax_profit=(
            self_assessed_row.ytd_pretax_profit if self_assessed_row else None
        ),
        projection_caveat=_PROJECTION_CAVEAT,
    )


def _project_days_until_disposition(flag_count: int) -> int | None:
    """`None` when the symbol isn't close enough (no meaningful projection); otherwise a crude
    "flags remaining" count, not a calendar-day estimate — see `_PROJECTION_CAVEAT`."""
    remaining = _ATTENTION_FLAGS_TO_DISPOSITION - flag_count
    return remaining if 0 <= remaining < _ATTENTION_FLAGS_TO_DISPOSITION else None


def _project_days_by_rate(flag_count: int, observed_days: int, flags_needed: int) -> int | None:
    """A real trading-day estimate: this symbol's own observed flag rate (`flag_count /
    observed_days`) extrapolated forward to `flags_needed` total flags, rounded up. `None` when
    `flag_count` is 0 (no rate to extrapolate — dividing by a zero rate is undefined, not "0
    days") or `observed_days` is 0 (nothing observed yet, e.g. day 1 of this feature's own
    history). This is an estimate ON TOP OF `_project_days_until_disposition`'s own already-
    approximate rule, not a second independent source — see `_PROJECTION_CAVEAT`, which states
    both explicitly rather than presenting this as more precise than it is."""
    if flag_count <= 0 or observed_days <= 0:
        return None
    remaining_flags = flags_needed - flag_count
    if remaining_flags <= 0:
        return 0
    rate = flag_count / observed_days
    return math.ceil(remaining_flags / rate)


_CJK_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_MATCHING_INTERVAL_PATTERN = re.compile(r"每([一二三四五六七八九0-9]+)分鐘?撮合")


def _parse_matching_interval_minutes(*texts: str | None) -> int | None:
    """The real 撮合終端機 cadence in minutes from TWSE/TPEx's own free-text `detail`/`measure`
    (e.g. "約每2分鐘撮合一次" -> 2, "約每45分鐘撮合一次" -> 45, "約每二分鐘撮合一次" -> 2). Not a
    closed 5/20-minute enum — TWSE's own announcements use both digit and CJK-numeral spellings
    for small values, so both are handled; returns `None` when no such phrase is found (e.g. a
    TPEx row, which carries no separate interval statement)."""
    for text in texts:
        if not text:
            continue
        match = _MATCHING_INTERVAL_PATTERN.search(text)
        if match is None:
            continue
        raw = match.group(1)
        if raw.isdigit():
            return int(raw)
        if raw in _CJK_DIGITS:
            return _CJK_DIGITS[raw]
    return None


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table — builds its own `ParquetStore` from
    `config.settings`, same self-contained-construction pattern `mops_tools.py`/`market_tools.py`
    already use, since the one call site (`tools/__init__.py`) only ever passes `router`."""
    store = ParquetStore(root=get_settings().parquet_root)
    return [(GetDispositionBoardTool(store, router), False)]


__all__ = [
    "GetDispositionBoardInput",
    "GetDispositionBoardOutput",
    "GetDispositionBoardTool",
    "build_registration_table",
]
