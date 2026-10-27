"""`get_event_study` - descriptive historical statistics: what stocks did over the next N trading
days after a dated event (disposition start / release, attention listing, restart of trading after
a capital reduction, start / end of a treasury-stock buy-back).

Everything is read back from the ingest-owned store (the price window and the event feeds); the
one exception is the capital-reduction event, which has no stored whole-market history and is
fetched per symbol through the router. The statistics live in `analytics/event_study`; this module
only validates the request, picks the event source, and shapes the typed output.

Public tier: every input dataset is `DataClass.PUBLIC` (exchange announcements, end-of-day prices,
the public tier of the capital-reduction dataset), so every caller gets the same full result.
Nothing here ranks, scores or recommends; the output is counts and distribution statistics with
their sample sizes and limits.
"""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime, timedelta
from typing import Final, Literal
from zoneinfo import ZoneInfo

import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field, field_validator

from analytics.event_study import (
    DEFAULT_HORIZONS,
    MIN_SAMPLE_SIZE,
    PRE_EVENT_DAYS,
    PROXY_ID,
    EventCoverage,
    EventDateFilter,
    EventOutcome,
    EventType,
    ExtractionResult,
    HorizonSummary,
    SampleSummary,
    StudyResult,
    YearSummary,
    capital_reduction_events,
    load_close_panel,
    load_snapshot_events,
    price_calendar,
    run_event_study,
)
from dataaccess.router import DataAccessRouter
from ingest.store import ParquetStore
from schemas.capabilities import CapitalReductionReferencePriceRow
from schemas.chart_hint import ChartHint, FieldDef, FieldType, Mark
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from schemas.source import HealthStatus
from tools._rows import fetch_rows
from tools.base import SyncStoreTool, ToolContext, parse_args
from tools.market._common import names_by_symbol_for

_TAIPEI = ZoneInfo("Asia/Taipei")
_STALE_AFTER_DAYS: Final[int] = 5  # calendar days a price window may trail today and stay "ok"
_PRICE_MARGIN_DAYS: Final[int] = 21  # price rows read before the earliest event (pre-event window)
_MAX_HORIZON: Final[int] = 120
_MAX_HORIZONS: Final[int] = 5
_MAX_LISTED_EVENTS: Final[int] = 50
_PCT_DIGITS: Final[int] = 2

HorizonStatus = Literal["ok", "insufficient_sample"]
LimitationCode = Literal[
    "short_price_history",
    "short_event_history",
    "per_symbol_only",
    "insufficient_sample",
    "event_clustering",
    "survivorship",
    "unadjusted_prices",
    "price_basis_break",
    "no_costs",
    "not_predictive",
]


class _Text(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    en: str
    zh: str


_DEFINITIONS: Final[dict[EventType, _Text]] = {
    EventType.DISPOSITION_START: _Text(
        en=(
            "Disposition start: the first day of a disposition period, when the exchange applies "
            "its special trading measures to a stock (slower matching, cash prepayment). Day 0 is "
            "the period's start date (first trading day on or after it)."
        ),
        zh="處置起日：交易所公告的處置期間第一天，該股改採特殊撮合與預收款券等處置措施。"
        "第 0 日為處置起日當天（若非交易日則為其後第一個交易日）。",
    ),
    EventType.DISPOSITION_END: _Text(
        en=(
            "Disposition release: the first trading day after a disposition period ended, when "
            "normal matching resumes. A period followed straight away by a new period for the "
            "same stock is an extension and is not counted. Day 0 is that first day."
        ),
        zh="處置出關：處置期間結束後的第一個交易日，恢復一般撮合。處置期間結束後緊接著又被處置"
        "（延長）者不計入。第 0 日為出關首日。",
    ),
    EventType.ATTENTION_LISTED: _Text(
        en=(
            "Attention listing: the date a stock was put on the exchange's attention-stock list "
            "(注意股). Day 0 is the listing date (first trading day on or after it)."
        ),
        zh="列入注意股：個股被交易所公布為注意股的日期。第 0 日為公布日當天（若非交易日則為其後"
        "第一個交易日）。",
    ),
    EventType.CAPITAL_REDUCTION_RESUME: _Text(
        en=(
            "Capital-reduction restart: the day trading resumes after a capital reduction "
            "(減資恢復買賣), when the post-reduction reference price applies. Day 0 is that day."
        ),
        zh="減資恢復買賣：減資換發新股後恢復買賣的第一天，適用減資後參考價。第 0 日為恢復買賣日。",
    ),
    EventType.BUYBACK_START: _Text(
        en=(
            "Treasury-stock buy-back start: the first day of the planned buy-back period in a "
            "board resolution to repurchase the company's own shares. Day 0 is that start date "
            "(first trading day on or after it)."
        ),
        zh="庫藏股買回起日：董事會決議買回本公司股份後，預定買回期間的第一天。第 0 日為該起日"
        "（若非交易日則為其後第一個交易日）。",
    ),
    EventType.BUYBACK_END: _Text(
        en=(
            "Treasury-stock buy-back end: the last day of the actual buy-back period reported in "
            "a completion or period-expiry filing. Day 0 is that date (first trading day on or "
            "after it)."
        ),
        zh="庫藏股買回結束：買回執行完畢或期間屆滿公告中，實際買回期間的最後一天。第 0 日為該日"
        "（若非交易日則為其後第一個交易日）。",
    ),
}

_PROXY_TEXT: Final[_Text] = _Text(
    en=(
        "Market proxy: the median return, over the same trading-day window, of every ordinary "
        "stock (4-character code) priced at both ends. Excess return = the stock's return minus "
        "this median."
    ),
    zh="市場基準：同一段交易日區間內，所有一般股票（4 碼代號、兩端皆有收盤價）報酬率的中位數。"
    "超額報酬 = 個股報酬 - 此中位數。",
)


class GetEventStudyInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: EventType = Field(
        description="Which dated event to study: disposition_start, disposition_end, "
        "attention_listed, capital_reduction_resume (needs symbol), buyback_start, buyback_end."
    )
    symbol: str | None = Field(
        default=None,
        min_length=1,
        max_length=12,
        description="One stock code. Restricts the study to that stock and lists its own past "
        "events with their outcomes. Required for capital_reduction_resume.",
    )
    market: Market = Market.TW
    start_date: date_ | None = Field(
        default=None, description="Keep events dated on or after this day (ISO date)."
    )
    end_date: date_ | None = Field(
        default=None, description="Keep events dated on or before this day (ISO date)."
    )
    horizons: tuple[int, ...] = Field(
        default=DEFAULT_HORIZONS,
        description="Forward windows in trading days (1-120, up to 5). Default 5, 20, 60.",
    )
    min_volume: int | None = Field(
        default=None,
        ge=1,
        description="Keep only events whose day-0 trading volume (shares) is at least this.",
    )
    max_events: int = Field(
        default=20, ge=1, le=_MAX_LISTED_EVENTS, description="How many recent events to list."
    )

    @field_validator("horizons")
    @classmethod
    def _check_horizons(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        cleaned = tuple(sorted(set(value)))
        if not cleaned or len(cleaned) > _MAX_HORIZONS:
            raise ValueError(f"give between 1 and {_MAX_HORIZONS} distinct horizons")
        if cleaned[0] < 1 or cleaned[-1] > _MAX_HORIZON:
            raise ValueError(f"horizons must be between 1 and {_MAX_HORIZON} trading days")
        return cleaned


class EventLimitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: LimitationCode
    text: str
    text_zh: str


class EventDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: EventType
    en: str
    zh: str


class MarketProxy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    en: str
    zh: str


class EventDataWindow(BaseModel):
    """What the numbers are built from. `latest_price_date` bounds every horizon; the snapshot
    fields show how far back the stored event snapshots go."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    price_window_start: date_ | None = None
    latest_price_date: date_ | None = None
    first_event_date: date_ | None = None
    last_event_date: date_ | None = None
    event_feed_first_snapshot: date_ | None = None
    event_feed_last_snapshot: date_ | None = None
    event_feed_snapshots: int = 0


class EventCounts(BaseModel):
    """How many candidate events there were and what happened to each on the way."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows_seen: int = 0
    candidates: int = 0
    measured: int = 0
    excluded_non_equity: int = 0
    excluded_extended: int = 0
    excluded_unparsed: int = 0
    excluded_outside_filter: int = 0
    not_yet_effective: int = 0
    outside_price_window: int = 0
    missing_price: int = 0
    low_volume: int = 0


class HorizonStats(BaseModel):
    """One forward window. Statistics are percentages; they are None exactly when `status` is
    `insufficient_sample` (fewer than `min_sample_size` events), where only the counts are given."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_days: int
    status: HorizonStatus
    n_events: int
    n_symbols: int
    n_event_days: int
    max_events_on_one_day: int
    n_censored: int
    n_missing_price: int
    n_with_proxy: int = 0
    mean_pct: float | None = None
    median_pct: float | None = None
    stdev_pct: float | None = None
    p25_pct: float | None = None
    p75_pct: float | None = None
    share_positive_pct: float | None = None
    mean_excess_pct: float | None = None
    median_excess_pct: float | None = None


class YearStats(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_days: int
    year: int
    status: HorizonStatus
    n_events: int
    mean_pct: float | None = None
    median_pct: float | None = None
    mean_excess_pct: float | None = None
    median_excess_pct: float | None = None


class EventChartRow(BaseModel):
    """One bar of the chart: a statistic of the excess return at one horizon, labelled with n."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    horizon_days: int
    statistic: Literal["mean", "median"]
    excess_return_pct: float
    n_events: int


class EventHorizonReturn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_days: int
    return_pct: float | None = None
    excess_return_pct: float | None = None
    not_measured: Literal["censored", "missing_price"] | None = None


class EventRow(BaseModel):
    """One past event with its own outcome. Facts about that event, not a pattern to act on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str | None = None
    event_date: date_
    day0_date: date_ | None = None
    status: Literal[
        "measured", "not_yet_effective", "outside_price_window", "missing_price", "low_volume"
    ]
    pre_event_return_pct: float | None = None
    returns: tuple[EventHorizonReturn, ...] = Field(default_factory=tuple)


class GetEventStudyOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    event_type: EventType
    symbol: str | None = None
    data_available: bool
    unavailable_reason: str | None = None
    source_id: str = "ingest-store"
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    event_definition: EventDefinition
    data_window: EventDataWindow = Field(default_factory=EventDataWindow)
    proxy: MarketProxy
    min_sample_size: int = MIN_SAMPLE_SIZE
    pre_event_days: int = PRE_EVENT_DAYS
    counts: EventCounts = Field(default_factory=EventCounts)
    horizons: tuple[HorizonStats, ...] = Field(default_factory=tuple)
    pre_event: HorizonStats | None = None
    by_year: tuple[YearStats, ...] = Field(default_factory=tuple)
    chart_rows: tuple[EventChartRow, ...] = Field(default_factory=tuple)
    events: tuple[EventRow, ...] = Field(default_factory=tuple)
    limitations: tuple[EventLimitation, ...] = Field(default_factory=tuple)
    chart_hint: ChartHint | None = None


def _pct(value: float | None) -> float | None:
    return None if value is None else round(value * 100.0, _PCT_DIGITS)


def _horizon_stats(summary: HorizonSummary) -> HorizonStats:
    stats: SampleSummary | None = summary.stats
    return HorizonStats(
        horizon_days=summary.horizon_days,
        status="insufficient_sample" if summary.insufficient_sample else "ok",
        n_events=summary.n_events,
        n_symbols=summary.n_symbols,
        n_event_days=summary.n_event_days,
        max_events_on_one_day=summary.max_events_on_one_day,
        n_censored=summary.n_censored,
        n_missing_price=summary.n_missing_price,
        n_with_proxy=summary.n_with_proxy,
        mean_pct=_pct(stats.mean) if stats else None,
        median_pct=_pct(stats.median) if stats else None,
        stdev_pct=_pct(stats.stdev) if stats else None,
        p25_pct=_pct(stats.p25) if stats else None,
        p75_pct=_pct(stats.p75) if stats else None,
        share_positive_pct=_pct(stats.share_positive) if stats else None,
        mean_excess_pct=_pct(summary.excess_mean),
        median_excess_pct=_pct(summary.excess_median),
    )


def _year_stats(row: YearSummary) -> YearStats:
    return YearStats(
        horizon_days=row.horizon_days,
        year=row.year,
        status="insufficient_sample" if row.insufficient_sample else "ok",
        n_events=row.n_events,
        mean_pct=_pct(row.mean),
        median_pct=_pct(row.median),
        mean_excess_pct=_pct(row.excess_mean),
        median_excess_pct=_pct(row.excess_median),
    )


def _chart_rows(horizons: tuple[HorizonStats, ...]) -> tuple[EventChartRow, ...]:
    rows: list[EventChartRow] = []
    for stats in horizons:
        if stats.status != "ok":
            continue
        label = f"D+{stats.horizon_days} (n={stats.n_events})"
        for statistic, value in (
            ("mean", stats.mean_excess_pct),
            ("median", stats.median_excess_pct),
        ):
            if value is not None:
                rows.append(
                    EventChartRow(
                        label=label,
                        horizon_days=stats.horizon_days,
                        statistic="mean" if statistic == "mean" else "median",
                        excess_return_pct=value,
                        n_events=stats.n_events,
                    )
                )
    return tuple(rows)


def _chart_hint(rows: tuple[EventChartRow, ...]) -> ChartHint | None:
    if not rows:
        return None
    return ChartHint(
        mark=Mark.BAR,
        title="事後超額報酬 Excess return vs market median（%）",
        data_key="chart_rows",
        x=FieldDef(field="label", type=FieldType.NOMINAL),
        y=FieldDef(field="excess_return_pct", type=FieldType.QUANTITATIVE),
        color=FieldDef(field="statistic", type=FieldType.NOMINAL),
    )


def _event_rows(
    outcomes: tuple[EventOutcome, ...], names: dict[str, str], limit: int, *, with_pre: bool
) -> tuple[EventRow, ...]:
    newest_first = sorted(
        outcomes, key=lambda o: (o.event.event_date, o.event.symbol), reverse=True
    )
    rows: list[EventRow] = []
    for outcome in newest_first[:limit]:
        horizons = sorted(set(outcome.returns) | set(outcome.skipped))
        rows.append(
            EventRow(
                symbol=outcome.event.symbol,
                name=outcome.event.name or names.get(outcome.event.symbol),
                event_date=outcome.event.event_date,
                day0_date=outcome.day0,
                status=outcome.status,
                pre_event_return_pct=_pct(outcome.pre_event) if with_pre else None,
                returns=tuple(
                    EventHorizonReturn(
                        horizon_days=h,
                        return_pct=_pct(outcome.returns.get(h)),
                        excess_return_pct=_pct(outcome.excess.get(h)),
                        not_measured=outcome.skipped.get(h),
                    )
                    for h in horizons
                ),
            )
        )
    return tuple(rows)


# A feed that starts within this many days of the price history covers it for practical purposes.
_FEED_START_TOLERANCE_DAYS = 10


def _feed_starts_late(window: EventDataWindow) -> bool:
    first = window.event_feed_first_snapshot
    return first is None or (first - window.price_window_start).days > _FEED_START_TOLERANCE_DAYS


def _limitations(
    kind: EventType,
    horizons: tuple[HorizonStats, ...],
    window: EventDataWindow,
) -> tuple[EventLimitation, ...]:
    found: list[EventLimitation] = [
        EventLimitation(
            code="short_price_history",
            text=(
                f"The stored price history starts {window.price_window_start}, about three "
                "years, so a long horizon has few completed events and every figure covers one "
                "market regime at most."
            ),
            text_zh=(
                f"已入庫的價格資料從 {window.price_window_start} 開始，約三年；較長的觀察期可完成的"
                "事件很少，所有數字最多只涵蓋一段市況。"
            ),
        )
    ]
    if kind is EventType.CAPITAL_REDUCTION_RESUME:
        found.append(
            EventLimitation(
                code="per_symbol_only",
                text=(
                    "Capital-reduction restarts have no stored market-wide history; they are "
                    "fetched for one stock at a time, so this reads that stock's own events only "
                    "and a pooled sample across stocks is not available."
                ),
                text_zh="減資恢復買賣沒有入庫的全市場歷史，只能逐檔查詢，因此僅呈現該檔自己的事件，"
                "無法合併多檔樣本。",
            )
        )
        found.append(
            EventLimitation(
                code="price_basis_break",
                text=(
                    "Prices before a capital reduction are on the old share basis, so the "
                    "pre-event change is not comparable and is left out."
                ),
                text_zh="減資前的價格基準與減資後不同，事前漲跌不可比較，因此不列出。",
            )
        )
    elif _feed_starts_late(window):
        first = window.event_feed_first_snapshot
        found.append(
            EventLimitation(
                code="short_event_history",
                text=(
                    "The event feed holds daily snapshots only from "
                    f"{first} ({window.event_feed_snapshots} of them), later than the price "
                    "history starts. Events before that date are not in this sample."
                ),
                text_zh=(
                    f"事件資料僅有 {first} 起的每日快照"
                    f"（共 {window.event_feed_snapshots} 份），比價格資料的起點晚；"
                    "更早的事件不在樣本內。"
                ),
            )
        )
    if any(h.status == "insufficient_sample" for h in horizons):
        found.append(
            EventLimitation(
                code="insufficient_sample",
                text=(
                    f"A window with fewer than {MIN_SAMPLE_SIZE} events shows counts only, with no "
                    "summary statistic: a handful of cases says nothing reliable about a typical "
                    "outcome."
                ),
                text_zh=f"事件少於 {MIN_SAMPLE_SIZE} 件的觀察期只列件數、不提供統計數字：少數個案"
                "無法說明一般情況。",
            )
        )
    spread = "; ".join(
        f"D+{h.horizon_days}: {h.n_events} events fall on {h.n_event_days} distinct days"
        for h in horizons
        if h.n_events > 0
    )
    found.append(
        EventLimitation(
            code="event_clustering",
            text=(
                "Events cluster in time and share the same market moves, so the effective "
                f"number of independent samples is smaller than n ({spread or 'no events'})."
            ),
            text_zh="事件集中在特定日期、共同受當時大盤走勢影響，實際獨立樣本數小於 n"
            f"（{spread or '無事件'}）。",
        )
    )
    found.extend(
        [
            EventLimitation(
                code="survivorship",
                text=(
                    "Stocks delisted or without a price on a needed day are excluded and counted, "
                    "so the sample can lean toward names that kept trading."
                ),
                text_zh="已下市或在所需日期沒有價格的個股會被排除並計數，樣本可能偏向持續交易的個股。",
            ),
            EventLimitation(
                code="unadjusted_prices",
                text=(
                    "Returns use the closes as stored, which are not adjusted for cash dividends "
                    "or splits, so an ex-dividend day inside a window lowers that window's return."
                ),
                text_zh="報酬以入庫收盤價計算，未還原現金股利與分割；觀察期內遇除息會拉低該期報酬。",
            ),
            EventLimitation(
                code="no_costs",
                text="No trading costs, taxes or slippage are included, and disposition rules "
                "can limit what could be traded.",
                text_zh="未計入交易成本、稅負與滑價，處置期間的交易限制也未反映。",
            ),
            EventLimitation(
                code="not_predictive",
                text="These are descriptions of past cases. They say nothing about what any "
                "future case will do.",
                text_zh="以上僅描述過去個案，無法說明未來任何個案的走勢。",
            ),
        ]
    )
    return tuple(found)


def _health(latest: date_ | None) -> HealthStatus:
    if latest is None:
        return HealthStatus.UNAVAILABLE
    today = datetime.now(_TAIPEI).date()
    return HealthStatus.OK if (today - latest).days <= _STALE_AFTER_DAYS else HealthStatus.STALE


def _caveat(latest: date_ | None) -> str:
    through = f"through {latest}" if latest else "(none stored yet)"
    return (
        f"End-of-day closes from the stored daily partitions {through}; event feeds hold only the "
        "snapshots shown in data_window. A historical description of past cases, not a forecast."
    )


def _unavailable(
    typed: GetEventStudyInput, reason: str, latest: date_ | None = None
) -> GetEventStudyOutput:
    return GetEventStudyOutput(
        market=typed.market.value,
        event_type=typed.event_type,
        symbol=typed.symbol,
        data_available=False,
        unavailable_reason=reason,
        currentness_caveat=_caveat(latest),
        health_status=HealthStatus.UNAVAILABLE,
        event_definition=_definition(typed.event_type),
        proxy=_proxy(),
        data_window=EventDataWindow(latest_price_date=latest),
    )


def _definition(kind: EventType) -> EventDefinition:
    text = _DEFINITIONS[kind]
    return EventDefinition(event_type=kind, en=text.en, zh=text.zh)


def _proxy() -> MarketProxy:
    return MarketProxy(id=PROXY_ID, en=_PROXY_TEXT.en, zh=_PROXY_TEXT.zh)


class GetEventStudyTool(SyncStoreTool):
    """See the module docstring."""

    name = "get_event_study"
    description = (
        "Get historical statistics on what stocks did over the next 5/20/60 trading days after a "
        "dated event: disposition start (處置), release from disposition (出關), attention-list "
        "listing (注意股), restart of trading after a capital reduction (減資恢復買賣, one symbol "
        "at a time), or start / end of a treasury-stock buy-back (庫藏股). Returns, per window, "
        "the number of events, mean / median / spread of the return and of the return relative to "
        "the market median, a by-year table, the stock's own past events when a symbol is given, "
        "and typed limitations. Present results ONLY as descriptive historical statistics: always "
        "state n, the data window and the limitations, mention when a window has too few events "
        "to report, and never present them as a signal, a forecast, a ranking or advice."
    )
    input_model: type[BaseModel] = GetEventStudyInput
    output_model: type[BaseModel] = GetEventStudyOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "event_study"
    cache_ttl_seconds = 300.0  # recomputed when ingest writes new data

    def __init__(self, store: ParquetStore, router: DataAccessRouter) -> None:
        self._store = store
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetEventStudyInput, args)
        if typed.event_type is not EventType.CAPITAL_REDUCTION_RESUME:
            return await super().run(args, ctx)
        return await self._run_capital_reduction(typed, ctx)

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # public data: every caller gets the same result
        typed = parse_args(GetEventStudyInput, args)
        if typed.market is not Market.TW:
            return _unavailable(typed, "The event study covers the Taiwan market only.")
        calendar = price_calendar(self._store)
        if not calendar:
            return _unavailable(typed, "No stored price history is available yet.")
        flt = EventDateFilter(symbol=typed.symbol, start=typed.start_date, end=typed.end_date)
        extraction, coverage = load_snapshot_events(self._store, typed.event_type, flt, calendar)
        return self._assemble(typed, extraction, coverage, calendar, health=None)

    async def _run_capital_reduction(
        self, typed: GetEventStudyInput, ctx: ToolContext
    ) -> GetEventStudyOutput:
        if typed.market is not Market.TW:
            return _unavailable(typed, "The event study covers the Taiwan market only.")
        if typed.symbol is None:
            return _unavailable(
                typed,
                "Capital-reduction restarts are not stored for the whole market and a "
                "market-wide fetch is not allowed: give one symbol to read that stock's own "
                "events.",
            )
        calendar = await anyio.to_thread.run_sync(price_calendar, self._store)
        if not calendar:
            return _unavailable(typed, "No stored price history is available yet.")
        result, rows = await fetch_rows(
            self._router,
            Capability.CAPITAL_REDUCTION_REFERENCE_PRICE,
            Symbol(id=typed.symbol, market=typed.market),
            tls_policy=ctx.tls_policy,
            model=CapitalReductionReferencePriceRow,
            key="rows",
            params={
                "start_date": calendar[0].isoformat(),
                "end_date": datetime.now(_TAIPEI).date().isoformat(),
            },
        )
        flt = EventDateFilter(symbol=typed.symbol, start=typed.start_date, end=typed.end_date)
        extraction = capital_reduction_events(rows, flt)
        return await anyio.to_thread.run_sync(
            self._assemble, typed, extraction, None, calendar, result.health.status
        )

    def _assemble(
        self,
        typed: GetEventStudyInput,
        extraction: ExtractionResult,
        coverage: EventCoverage | None,
        calendar: list[date_],
        health: HealthStatus | None,
    ) -> GetEventStudyOutput:
        latest = calendar[-1]
        if coverage is not None and coverage.n_snapshots == 0:
            return _unavailable(typed, f"No {coverage.dataset} snapshots are stored yet.", latest)
        if not extraction.events:
            return _unavailable(
                typed,
                "No matching events in the stored feed for this request "
                f"(candidate rows read: {extraction.rows_seen}).",
                latest,
            )
        first_event = extraction.events[0].event_date
        since = first_event - timedelta(days=_PRICE_MARGIN_DAYS)
        panel = load_close_panel(self._store, since)
        study = run_event_study(
            extraction.events,
            panel,
            horizons=typed.horizons,
            min_volume=typed.min_volume,
        )
        window = EventDataWindow(
            price_window_start=calendar[0],
            latest_price_date=study.latest_price_date,
            first_event_date=extraction.events[0].event_date,
            last_event_date=extraction.events[-1].event_date,
            event_feed_first_snapshot=coverage.first_snapshot if coverage else None,
            event_feed_last_snapshot=coverage.last_snapshot if coverage else None,
            event_feed_snapshots=coverage.n_snapshots if coverage else 0,
        )
        return self._output(typed, extraction, study, window, health)

    def _output(
        self,
        typed: GetEventStudyInput,
        extraction: ExtractionResult,
        study: StudyResult,
        window: EventDataWindow,
        health: HealthStatus | None,
    ) -> GetEventStudyOutput:
        horizons = tuple(_horizon_stats(h) for h in study.horizons)
        pre = None if typed.event_type is EventType.CAPITAL_REDUCTION_RESUME else study.pre_event
        needs_names = [o.event.symbol for o in study.outcomes if not o.event.name]
        names = names_by_symbol_for(self._store, typed.market.value) if needs_names else {}
        limit = typed.max_events
        chart_rows = _chart_rows(horizons)
        measured_health = _health(study.latest_price_date)
        if health is not None and health is not HealthStatus.OK:
            measured_health = health
        counts = study.counts
        return GetEventStudyOutput(
            market=typed.market.value,
            event_type=typed.event_type,
            symbol=typed.symbol,
            data_available=True,
            currentness_caveat=_caveat(study.latest_price_date),
            health_status=measured_health,
            event_definition=_definition(typed.event_type),
            data_window=window,
            proxy=_proxy(),
            counts=EventCounts(
                rows_seen=extraction.rows_seen,
                candidates=counts.candidates,
                measured=counts.measured,
                excluded_non_equity=extraction.excluded_non_equity,
                excluded_extended=extraction.excluded_extended,
                excluded_unparsed=extraction.excluded_unparsed,
                excluded_outside_filter=extraction.excluded_outside_filter,
                not_yet_effective=counts.not_yet_effective,
                outside_price_window=counts.outside_price_window,
                missing_price=counts.missing_price,
                low_volume=counts.low_volume,
            ),
            horizons=horizons,
            pre_event=_horizon_stats(pre) if pre is not None else None,
            by_year=tuple(_year_stats(y) for y in study.by_year),
            chart_rows=chart_rows,
            events=_event_rows(study.outcomes, names, limit, with_pre=pre is not None),
            limitations=_limitations(typed.event_type, horizons, window),
            chart_hint=_chart_hint(chart_rows),
        )


__all__ = [
    "EventChartRow",
    "EventCounts",
    "EventDataWindow",
    "EventDefinition",
    "EventHorizonReturn",
    "EventLimitation",
    "EventRow",
    "GetEventStudyInput",
    "GetEventStudyOutput",
    "GetEventStudyTool",
    "HorizonStats",
    "MarketProxy",
    "YearStats",
]
