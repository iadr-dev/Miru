"""Institutional (三大法人) flow and broker-branch (分點) chip-flow tools."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date as date_
from typing import NamedTuple, cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.capabilities import ChipBrokerRow, InstitutionalFlowRow
from schemas.enums import Capability, DataClass
from schemas.source import HealthStatus
from tools.base import BaseTool, ToolContext, is_entitled, parse_args
from tools.common import DateRangeInput, candidate_trading_days
from tools.market._common import RowList


class InstitutionalFlowRollingNet(BaseModel):
    """One investor category's pre-summed net buy/sell (`sum(buy - sell)`) over the trailing
    5/10/20-trading-day windows within whatever range `get_institutional_flow` fetched — see
    that tool's own docstring for why this exists: without it, a skill asking for a "5/10/20
    日買賣超趨勢" forces the model to sum 20+ raw daily rows by hand across every investor
    category, which measurably happened live (a `chip-flow-review` turn's reasoning trace
    showed it manually adding up 20 days x 3 categories and running out of response budget
    before writing any prose at all — the final answer had zero analysis, just a raw table).
    A window field is `None` when fewer trading days than that window exist in the fetched
    range (e.g. a 5-day request can't back a 20-day figure) — never a partial-window number
    silently mislabeled as the full window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    investor: str
    net_5d: float | None = None
    net_10d: float | None = None
    net_20d: float | None = None


class GetInstitutionalFlowOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    # `rolling_net` is declared BEFORE `rows` — `agent.loop._render_result_for_model` serializes
    # this model to JSON in field-declaration order and hard-truncates at a fixed char budget;
    # with `rows` first, a large window's raw rows alone can consume the entire budget and cut
    # `rolling_net` out of what the model ever sees (confirmed live — see `_rolling_net_by_investor`
    # and `ChipBrokerNetSummary`'s docstrings for the incident this fixes). Declaring the
    # pre-aggregated summary first guarantees it always survives truncation.
    rolling_net: tuple[InstitutionalFlowRollingNet, ...] = Field(default_factory=tuple)
    rows: tuple[InstitutionalFlowRow, ...] = Field(default_factory=tuple)


def _rolling_net_by_investor(
    rows: Sequence[InstitutionalFlowRow],
) -> tuple[InstitutionalFlowRollingNet, ...]:
    """Pre-sum `buy - sell` over the trailing 5/10/20 TRADING days present in `rows` (not
    calendar days — a distinct-`trade_date` count, so a window with holidays/gaps still means
    "N trading sessions"), independently per investor category. Every category the rows
    actually contain gets its own entry — never hardcoded to just foreign/trust/dealer, since
    FinMind's own category set includes `Dealer_Hedging`/`Foreign_Dealer_Self` too (confirmed
    live) and a caller should see whatever categories were really returned."""
    by_investor: dict[str, dict[date_, tuple[int, int]]] = {}
    for row in rows:
        per_date = by_investor.setdefault(row.investor, {})
        buy, sell = per_date.get(row.trade_date, (0, 0))
        per_date[row.trade_date] = (buy + row.buy, sell + row.sell)

    results: list[InstitutionalFlowRollingNet] = []
    for investor, per_date in sorted(by_investor.items()):
        ordered_dates = sorted(per_date.keys())
        results.append(
            InstitutionalFlowRollingNet(
                investor=investor,
                net_5d=_net_over_trailing(per_date, ordered_dates, 5),
                net_10d=_net_over_trailing(per_date, ordered_dates, 10),
                net_20d=_net_over_trailing(per_date, ordered_dates, 20),
            )
        )
    return tuple(results)


def _net_over_trailing(
    per_date: dict[date_, tuple[int, int]], ordered_dates: list[date_], window: int
) -> float | None:
    if len(ordered_dates) < window:
        return None
    trailing = ordered_dates[-window:]
    return float(sum(per_date[d][0] - per_date[d][1] for d in trailing))


class GetInstitutionalFlowTool(BaseTool):
    """`get_institutional_flow` — wraps `Capability.INSTITUTIONAL_FLOW` (三大法人買賣). Public,
    deferred. Also returns `rolling_net` — server-side pre-summed 5/10/20-trading-day net
    buy/sell per investor category (see `InstitutionalFlowRollingNet`'s own docstring for why:
    a skill citing a rolling-window trend must never make the model sum raw daily rows by
    hand)."""

    name = "get_institutional_flow"
    description = (
        "Get the three-major-institutional-investor (foreign/investment-trust/dealer) "
        "buy/sell rows for one stock/ETF symbol over an optional date range, plus a "
        "pre-computed 5/10/20-trading-day rolling net buy/sell per investor category — cite "
        "`rolling_net`'s figures directly rather than summing `rows` yourself."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetInstitutionalFlowOutput
    card_type: str | None = "institutional_flow"
    data_class = DataClass.PUBLIC

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        result = await self._router.fetch(
            Capability.INSTITUTIONAL_FLOW,
            typed.to_symbol(),
            tls_policy=ctx.tls_policy,
            **typed.date_params(),
        )
        raw_rows = result.payload.get("rows")
        rows: tuple[InstitutionalFlowRow, ...] = ()
        if isinstance(raw_rows, list):
            typed_rows = cast(RowList, raw_rows)
            rows = tuple(InstitutionalFlowRow.model_validate(row) for row in typed_rows)
        return GetInstitutionalFlowOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            rows=rows,
            rolling_net=_rolling_net_by_investor(rows),
        )


class ChipBrokerNetSummary(BaseModel):
    """One broker/branch's total net buy/sell (`sum(buy - sell)`) over the whole fetched range —
    see `_top_broker_net_summary`'s docstring for why this exists: the same architecture gap as
    `InstitutionalFlowRollingNet` (Finding #69), one tool over. `get_chip_flows` can return
    thousands of per-day per-branch rows (a live repro against 鴻海/2317 returned 6,500+), and
    without a pre-aggregated summary the model was observed spending its entire response budget
    manually eyeballing/summing individual 分點 rows, leaving zero room for prose — this field
    is what it should cite instead."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    broker_id: str
    broker_name: str
    net: int


class ChipBrokerTopRow(BaseModel):
    """One broker's row in the 買超Top15/賣超Top15 table (`chip-analysis-panel.tsx`'s own
    `BrokerTopRow`, now computed here instead of client-side so an unentitled caller's
    row-preview mask (governance.md §4 top-N-preview exception) can apply to the SAME ranked
    rows the frontend renders, not to the raw per-day feed underneath them). `rank` (1-based,
    within this row's own buy-top or sell-top list) is the only field that survives masking."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank: int
    broker_id: str | None  # null only when locked
    broker_name: str | None  # null only when locked
    net: int | None  # null only when locked
    buy: int | None  # null only when locked
    sell: int | None  # null only when locked
    locked: bool = False


class GetChipFlowsOutput(BaseModel):
    """Sponsor-tier 分點 branch trading rows. `dispatch_tool` runs this tool even for an
    unentitled caller (`supports_row_preview=True` — governance.md §4 top-N-preview exception);
    `run()` itself is what nulls `buy_top`/`sell_top` rows beyond `free_row_count` and empties
    `rows`/`top_brokers` for that caller — `dispatch_tool` never inspects this output's fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    total_volume: int = 0  # 分點成交量 — sum(buy + sell) across every branch in the fetched range
    main_force_net: int = 0  # 主力籌碼淨額 — combined net of the _TOP_BROKER_COUNT largest movers
    concentration_days_pct: float | None = None  # 集中天數% — see `_concentration_days_pct`
    latest_trade_date: date_ | None = None
    free_row_count: int = 0  # governance.md §4 — real rows at the front of EACH of buy_top/sell_top
    buy_top: tuple[ChipBrokerTopRow, ...] = Field(default_factory=tuple)
    sell_top: tuple[ChipBrokerTopRow, ...] = Field(default_factory=tuple)
    # `top_brokers` declared BEFORE `rows` for the same reason as `InstitutionalFlowOutput`'s
    # `rolling_net` field ordering — see that field's comment. `rows` here is the worse offender
    # in practice: a live repro against 鴻海/2317 returned 10,000+ rows, guaranteeing `rows`
    # alone exceeds `agent.loop._MAX_MODEL_RESULT_CHARS` and would silently drop `top_brokers`
    # from what the model ever sees if it were declared second. Both are empty (never truncated
    # to a `MaskedTeaser`-shaped stub) for an unentitled caller — `buy_top`/`sell_top`/the always-
    # real stats above are that caller's whole preview; the raw feed is Sponsor-only regardless.
    top_brokers: tuple[ChipBrokerNetSummary, ...] = Field(default_factory=tuple)
    rows: tuple[ChipBrokerRow, ...] = Field(default_factory=tuple)


_TOP_BROKER_COUNT = 10


def _top_broker_net_summary(rows: Sequence[ChipBrokerRow]) -> tuple[ChipBrokerNetSummary, ...]:
    """Pre-sum `buy - sell` per broker/branch across every row in the fetched range, then keep
    only the `_TOP_BROKER_COUNT` largest-magnitude net positions (both buy- and sell-side) —
    the handful of branches that actually moved the stock, not a re-derivation of all 6,500+ raw
    rows the model would otherwise have to scan itself."""
    net_by_broker: dict[str, tuple[str, int]] = {}
    for row in rows:
        name, net = net_by_broker.get(row.broker_id, (row.broker_name, 0))
        net_by_broker[row.broker_id] = (name, net + row.buy - row.sell)

    ranked = sorted(net_by_broker.items(), key=lambda item: abs(item[1][1]), reverse=True)
    return tuple(
        ChipBrokerNetSummary(broker_id=broker_id, broker_name=name, net=net)
        for broker_id, (name, net) in ranked[:_TOP_BROKER_COUNT]
    )


_CHIP_BROKER_MAX_TRADING_DAYS = 10  # FinMind's TaiwanStockTradingDailyReport is single-day-per-


# call upstream (`date_param="date"` in chip_institutional.py's own _DatasetConfig — confirmed
# empirically, the endpoint has no start/end-range form) — a multi-day `get_chip_flows` request
# fans out to one concurrent router.fetch() per trading day. Bounded to 10 (not the full 250+
# trading days a "一年" window would imply) so one page load never issues more than 10 sequential
# Sponsor-tier FinMind calls; the frontend's own period selector caps its widest option to match.
_TOP_BROKER_ROW_COUNT = 15  # matches chip-analysis-panel.tsx's own 買超Top15/賣超Top15 tables


_CHIP_FLOWS_FREE_ROW_COUNT = 2  # governance.md §4 top-N-preview exception — see tools/market/'s


class _BrokerAgg(NamedTuple):
    broker_id: str
    broker_name: str
    net: int
    buy: int
    sell: int


def _aggregate_brokers(rows: Sequence[ChipBrokerRow]) -> tuple[_BrokerAgg, ...]:
    """Sums `buy`/`sell`/`net` per broker/branch across every row in the fetched range — the
    backend port of `chip-analysis-panel.tsx`'s own (now-removed) client-side `aggregateBrokers`,
    moved server-side so the row-level preview mask below applies to the SAME ranked rows the
    frontend renders, not to the raw per-day feed underneath them."""
    by_broker: dict[str, _BrokerAgg] = {}
    for row in rows:
        prior = by_broker.get(row.broker_id)
        by_broker[row.broker_id] = _BrokerAgg(
            broker_id=row.broker_id,
            broker_name=row.broker_name,
            net=(prior.net if prior else 0) + row.buy - row.sell,
            buy=(prior.buy if prior else 0) + row.buy,
            sell=(prior.sell if prior else 0) + row.sell,
        )
    return tuple(by_broker.values())


def _broker_top_rows(
    aggregates: Sequence[_BrokerAgg], *, positive_net: bool
) -> tuple[ChipBrokerTopRow, ...]:
    """買超Top15 (`positive_net=True`) or 賣超Top15 (`positive_net=False`) — ranked by `net`
    magnitude among same-signed brokers only, capped at `_TOP_BROKER_ROW_COUNT`, `rank` assigned
    post-sort (1-based)."""
    side = [agg for agg in aggregates if (agg.net > 0) is positive_net]
    ranked = sorted(side, key=lambda agg: agg.net, reverse=positive_net)[:_TOP_BROKER_ROW_COUNT]
    return tuple(
        ChipBrokerTopRow(
            rank=i + 1,
            broker_id=agg.broker_id,
            broker_name=agg.broker_name,
            net=agg.net,
            buy=agg.buy,
            sell=agg.sell,
        )
        for i, agg in enumerate(ranked)
    )


def _mask_broker_top_rows_beyond(
    rows: tuple[ChipBrokerTopRow, ...], free_row_count: int
) -> tuple[ChipBrokerTopRow, ...]:
    """governance.md §4 top-N-preview exception — see `_mask_holding_rank_rows_beyond`'s own
    docstring for the shared rationale (never a client-side-only blur)."""
    return tuple(
        row
        if i < free_row_count
        else row.model_copy(
            update={
                "broker_id": None,
                "broker_name": None,
                "net": None,
                "buy": None,
                "sell": None,
                "locked": True,
            }
        )
        for i, row in enumerate(rows)
    )


def _concentration_days_pct(rows: Sequence[ChipBrokerRow], overall_net: int) -> float | None:
    """集中天數% — the % of trading days within the fetched range where that day's own net
    buy/sell stayed same-signed as the range's overall net; a defensible "how consistent was the
    dominant flow" figure (see `ChipFlowsBody`'s own docstring in the frontend for what this
    is/isn't a claim of)."""
    by_date_net: dict[date_, int] = {}
    for row in rows:
        by_date_net[row.trade_date] = by_date_net.get(row.trade_date, 0) + row.buy - row.sell
    daily_nets = list(by_date_net.values())
    if not daily_nets:
        return None
    same_signed = sum(
        1 for net in daily_nets if (net >= 0 and overall_net >= 0) or (net < 0 and overall_net < 0)
    )
    return same_signed / len(daily_nets) * 100


class GetChipFlowsTool(BaseTool):
    """`get_chip_flows` — wraps `Capability.CHIP_BROKER` (FinMind Sponsor-tier 分點進出).

    `supports_row_preview=True` (governance.md §4 top-N-preview exception): an unentitled
    caller still gets this tool's real ranked buy_top/sell_top row shape/rank, with every row
    past `_CHIP_FLOWS_FREE_ROW_COUNT` having its value AND identity fields (`broker_id`/
    `broker_name`) nulled in `run()` itself, mirroring `get_etfs_holding_stock`'s own row-preview
    masking exactly. `total_volume`/`main_force_net`/`concentration_days_pct`/`latest_trade_date`
    are always real (never masked) — the same "aggregate stays real, only individual rows lock"
    split `get_etfs_holding_stock`'s `etf_count`/`position_event_counts` already use. `rows`/
    `top_brokers` (the raw per-day feed) are emptied entirely for an unentitled caller — there is
    nothing in them an entitled caller doesn't already get via `buy_top`/`sell_top`.
    """

    name = "get_chip_flows"
    description = (
        "Get branch(分點)/broker-level buy-sell trading rows for one stock (Sponsor-tier; top "
        "rows of buy_top/sell_top free, rest row-locked), plus backend-ranked buy_top/sell_top "
        "Top15 tables and total_volume/main_force_net/concentration_days_pct stats — cite those "
        "fields directly rather than summing `rows` yourself. The upstream report is "
        f"single-day-per-call, so a `start_date`/`end_date` range fans out to one call per "
        f"trading day, capped at {_CHIP_BROKER_MAX_TRADING_DAYS} days (most recent first) — a "
        "wider range silently returns only the newest days within that cap."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetChipFlowsOutput
    card_type: str | None = "chip_flows"
    data_class = DataClass.SPONSOR
    supports_row_preview: bool = True

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        sym = typed.to_symbol()
        end = typed.end_date or date_.today()
        start = typed.start_date or end

        if start == end:
            days: tuple[date_, ...] = (start,)
        else:
            days = candidate_trading_days(start, end, limit=_CHIP_BROKER_MAX_TRADING_DAYS) or (end,)

        results = await asyncio.gather(
            *(
                self._router.fetch(
                    Capability.CHIP_BROKER, sym, tls_policy=ctx.tls_policy, date=day.isoformat()
                )
                for day in days
            )
        )

        rows: list[ChipBrokerRow] = []
        for result in results:
            raw_rows = result.payload.get("rows")
            if isinstance(raw_rows, list):
                typed_rows = cast(RowList, raw_rows)
                rows.extend(ChipBrokerRow.model_validate(row) for row in typed_rows)

        # Every per-day fetch shares the same `source_id`/`currentness_caveat` (one adapter, one
        # dataset) — the last result's health represents the most recent day's own fetch outcome,
        # which is what a caller checking "is today's report available" actually wants to see.
        representative = results[-1]

        aggregates = _aggregate_brokers(rows)
        overall_net = sum(agg.net for agg in aggregates)
        top_by_magnitude = sorted(aggregates, key=lambda agg: abs(agg.net), reverse=True)[
            :_TOP_BROKER_COUNT
        ]
        # These four stats are always real (never masked) — the same "aggregate stays real, only
        # individual rows lock" split `get_etfs_holding_stock`'s `etf_count`/
        # `position_event_counts` already use — so they're computed from the full `rows` BEFORE
        # any entitlement-conditional truncation below.
        total_volume = sum(agg.buy + agg.sell for agg in aggregates)
        main_force_net = sum(agg.net for agg in top_by_magnitude)
        concentration_days_pct = _concentration_days_pct(rows, overall_net)
        latest_trade_date = max((row.trade_date for row in rows), default=None)

        buy_top = _broker_top_rows(aggregates, positive_net=True)
        sell_top = _broker_top_rows(aggregates, positive_net=False)

        entitled = is_entitled(self.data_class, ctx.tier)
        free_row_count = (
            max(len(buy_top), len(sell_top)) if entitled else _CHIP_FLOWS_FREE_ROW_COUNT
        )
        raw_rows = rows if entitled else []
        if not entitled:
            buy_top = _mask_broker_top_rows_beyond(buy_top, _CHIP_FLOWS_FREE_ROW_COUNT)
            sell_top = _mask_broker_top_rows_beyond(sell_top, _CHIP_FLOWS_FREE_ROW_COUNT)

        return GetChipFlowsOutput(
            source_id=representative.source_id,
            currentness_caveat=representative.currentness_caveat,
            health_status=representative.health.status,
            total_volume=total_volume,
            main_force_net=main_force_net,
            concentration_days_pct=concentration_days_pct,
            latest_trade_date=latest_trade_date,
            free_row_count=free_row_count,
            buy_top=buy_top,
            sell_top=sell_top,
            top_brokers=_top_broker_net_summary(raw_rows),
            rows=tuple(raw_rows),
        )
