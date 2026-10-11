"""Dividend schedule and the monthly dividend calendar."""

from __future__ import annotations

from datetime import date as date_
from datetime import timedelta
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from analytics.symbol_universe import SymbolType, classify_symbol_type
from dataaccess.router import DataAccessRouter
from ingest.jobs import DIVIDENDS_JOB
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import DividendEvent
from schemas.chart_hint import ChartHint, FieldDef, FieldType, Mark
from schemas.enums import Capability, DataClass, Market
from schemas.source import HealthStatus
from tools.base import BaseTool, ToolContext, parse_args
from tools.common import DateRangeInput
from tools.market._common import RowList, etf_nav_names, real_calendar


class GetDividendScheduleOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    events: tuple[DividendEvent, ...] = Field(default_factory=tuple)
    # `chart_hint` (schemas/chart_hint.py): a per-quarter cash-dividend bar is materially clearer
    # than the raw event list (esp. a growth trend across a dozen+ announced quarters — see the
    # scrolling-text-list complaint this fixes). `stock_dividend` is intentionally not plotted as
    # a second series here: virtually every modern TW dividend event is 100% cash, so a
    # near-all-zero series would just add legend noise; a future migration can add it back via
    # `y2` if a real stock-dividend-heavy issuer surfaces the need.
    chart_hint: ChartHint | None = None


class GetDividendScheduleTool(BaseTool):
    """`get_dividend_schedule` — wraps `Capability.DIVIDENDS` (the per-stock/ETF
    除權息/配息 timeline). Public, deferred."""

    name = "get_dividend_schedule"
    description = "Get the announced dividend/ex-dividend schedule for one stock/ETF symbol."
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetDividendScheduleOutput
    card_type: str | None = "dividend_schedule"
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result = await self._router.fetch(
            Capability.DIVIDENDS,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            **typed.date_params(),
        )
        raw_events = result.payload.get("events")
        events: tuple[DividendEvent, ...] = ()
        if isinstance(raw_events, list):
            rows = cast(RowList, raw_events)
            events = tuple(DividendEvent.model_validate(row) for row in rows)
        return GetDividendScheduleOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            events=events,
            chart_hint=ChartHint(
                mark=Mark.BAR,
                x=FieldDef(field="announced_date", type=FieldType.TEMPORAL),
                y=FieldDef(field="cash_dividend", type=FieldType.QUANTITATIVE),
            )
            if events
            else None,
        )


_DECEMBER = 12


def _month_bounds(year: int, month: int) -> tuple[date_, date_]:
    """The first and last calendar day of `(year, month)` — computed via date arithmetic
    rather than importing the stdlib `calendar` module, which this file's own
    `calendar.trading_days` import would shadow."""
    first_day = date_(year, month, 1)
    next_month_first = date_(year + 1, 1, 1) if month == _DECEMBER else date_(year, month + 1, 1)
    return first_day, next_month_first - timedelta(days=1)


class DividendCalendarEventRow(BaseModel):
    """One ETF's dividend event landing within the requested month, on any of its three
    dates (`ex_dividend_date`/`last_buy_date`/`payment_date`) — the same row appears under
    each date it actually has, so the calendar view can group by day without re-deriving
    which dates belong to which event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str
    ex_dividend_date: date_
    # 最後購買日 — the last trading day an investor can still buy and capture this
    # distribution, i.e. the trading day strictly before `ex_dividend_date`. Derived via
    # `TradingDayCalendar.previous_trading_day`, never FinMind's own field (it doesn't
    # publish one).
    last_buy_date: date_
    # 股利發放日 — `None` when FinMind hasn't published a payment date yet for this event
    # (see `DividendEvent.cash_dividend_payment_date`'s own docstring), never derived.
    payment_date: date_ | None = None
    cash_dividend: float


class GetDividendCalendarInput(BaseModel):
    """A market + calendar year/month; omitted year/month means the current calendar month."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    year: int | None = Field(default=None, ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def _year_and_month_together(self) -> GetDividendCalendarInput:
        if (self.year is None) != (self.month is None):
            raise ValueError("year and month must both be set or both omitted")
        return self


class GetDividendCalendarOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    year: int
    month: int
    data_available: bool
    unavailable_reason: str | None = None
    events: tuple[DividendCalendarEventRow, ...] = Field(default_factory=tuple)


class GetDividendCalendarTool(BaseTool):
    """`get_dividend_calendar` — 股息日曆: every TW-listed ETF's 除息日/最後購買日/股利發放日
    landing within one requested calendar month, read from the already-ingested `dividends`
    (`TaiwanStockDividend`) partition tree (`DIVIDENDS_JOB`) — never a live per-symbol fetch.

    Filtered to ETF-shaped symbols only (`classify_symbol_type`), matching every other
    ETF-tab feature's scope (`GetEtfListTool`/`GetEtfMoneyFlowTool`/`GetDailySignalTool`) — a
    whole-market (stock+ETF) calendar was considered and rejected: this tool backs the ETF
    tab's own 5th sub-tab specifically, not a general-purpose calendar.

    Scans the FULL `dividends` partition history (not a bounded recent window) because an
    event announced months ago can still have an ex-dividend/payment date landing in a
    FUTURE requested month — unlike `_etf_ttm_distribution_by_symbol`'s trailing-12-month
    yield calc (which only cares about past events), a calendar must find every event whose
    date falls in the target month regardless of when it was announced."""

    name = "get_dividend_calendar"
    description = (
        "Get every Taiwan-listed ETF's dividend calendar events (ex-dividend date, last "
        "buy date, payment date, cash amount) landing within one requested calendar month "
        "(defaults to the current month)."
    )
    input_model: type[BaseModel] = GetDividendCalendarInput
    output_model: type[BaseModel] = GetDividendCalendarOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "dividend_calendar"

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetDividendCalendarInput, args)
        market = typed.market.value
        today = date_.today()
        year = typed.year if typed.year is not None else today.year
        month = typed.month if typed.month is not None else today.month
        month_start, month_end = _month_bounds(year, month)

        known_dates = self._store.list_partition_dates(DIVIDENDS_JOB.dataset, market)
        if not known_dates:
            return GetDividendCalendarOutput(
                market=market,
                year=year,
                month=month,
                data_available=False,
                unavailable_reason=(
                    f"No ingested dividend partition found for {market} — the whole-market "
                    "ingest job may not have run yet."
                ),
            )
        raw_rows = self._store.read_range(DIVIDENDS_JOB.dataset, market, known_dates)
        all_events = rows_from_store(DividendEvent, raw_rows)

        calendar = real_calendar(self._store, market)
        names = etf_nav_names(self._store, market)

        rows: list[DividendCalendarEventRow] = []
        for event in all_events:
            if classify_symbol_type(event.symbol) is SymbolType.STOCK:
                continue
            ex_date = event.cash_ex_dividend_date
            cash = event.cash_dividend
            if ex_date is None or cash is None or cash <= 0:
                continue
            payment_date = event.cash_dividend_payment_date
            in_month = month_start <= ex_date <= month_end or (
                payment_date is not None and month_start <= payment_date <= month_end
            )
            if not in_month:
                continue
            rows.append(
                DividendCalendarEventRow(
                    symbol=event.symbol,
                    # Falls back to the symbol itself when `etf_nav` has no name for it — the
                    # same "never drop a row for a missing enrichment field" convention
                    # `_etf_list_row`'s own `inputs.names.get(symbol, symbol)` uses. `dividends`
                    # and `etf_nav` are two independent ingest jobs; one being behind (or, in a
                    # fresh/partially-backfilled store, having zero partitions at all) must
                    # never silently empty out an otherwise-real calendar event.
                    name=names.get(event.symbol, event.symbol),
                    ex_dividend_date=ex_date,
                    last_buy_date=calendar.previous_trading_day(ex_date),
                    payment_date=payment_date,
                    cash_dividend=cash,
                )
            )

        return GetDividendCalendarOutput(
            market=market,
            year=year,
            month=month,
            data_available=True,
            events=tuple(rows),
        )
