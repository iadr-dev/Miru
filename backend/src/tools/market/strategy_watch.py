"""`get_strategy_watch` — today's 策略盯盤 list with each stock's levels and where its conditions
stand: the 08:00 `strategy_watchlist` joined with the latest `strategy_hits` slot.

Read back from the ingest-owned store only; no source call. The intraday values come from the
Sponsor snapshot and the 千張大戶 figures from the Sponsor shareholding-distribution dataset, so an
unentitled caller gets the list, the levels and which conditions held, with those values masked
(`masked=True`), never invented. Copy is condition facts; nothing here ranks or recommends.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date as date_
from datetime import datetime, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from analytics.coerce import as_float
from analytics.strategy_watch.batch import (
    INSTITUTIONAL_FLOW_DATASET,
    MARGIN_SHORT_DATASET,
    PRICE_EOD_DATASET,
    STRATEGY_WATCHLIST_DATASET,
)
from ingest.jobs.strategy_intraday import STRATEGY_HITS_DATASET
from ingest.store import ParquetStore
from schemas.enums import DataClass
from schemas.strategy_watch import STRATEGY_KEYS, StrategyKey
from tools.base import SyncStoreTool, ToolContext, is_entitled, parse_args

_TAIPEI = ZoneInfo("Asia/Taipei")
_MAX_BACK_DAYS = 10  # a weekend or holiday still shows the last trading day's list
_CLOSE_SLOT = "close"
# Fields carrying Sponsor-derived values, nulled for an unentitled caller.
_SPONSOR_FIELDS = ("large_holder_pct", "large_holder_change_4w", "holders_change_4w_pct")

Regime = Literal["risk_on", "neutral", "risk_off"]


class GetStrategyWatchInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of: date_ | None = None  # the list's date; defaults to today (Taipei)
    strategy: StrategyKey | None = None


class StrategyCondition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    kind: Literal["trigger", "guard"]
    met: bool
    observed: float | None = None
    required: float | None = None


class StrategyWatchItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str | None = None
    strategy: StrategyKey
    industry: str | None = None
    close: float
    upper_band: float | None = None
    lower_band: float | None = None
    low_20d: float | None = None
    range_high_20d: float | None = None
    range_low_20d: float | None = None
    flag_high: float | None = None
    flag_low: float | None = None
    sector_strength_pct: float | None = None
    foreign_streak: int = 0
    trust_streak: int = 0
    large_holder_pct: float | None = None
    large_holder_change_4w: float | None = None
    holders_change_4w_pct: float | None = None
    margin_change_20d_pct: float | None = None
    headline: str | None = None
    days_on_list: int = 1
    first_listed: date_ | None = None
    # The latest intraday check of this stock today (None before 09:00 or without a snapshot).
    latest_slot: str | None = None
    price: float | None = None
    conditions: tuple[StrategyCondition, ...] = ()
    all_met: bool = False
    # The first slot every condition and guard held on two checks in a row (an alert was due).
    alerted_at: str | None = None
    close_confirmed: bool = False


class StrategyWatchEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: str
    symbol: str
    name: str | None = None
    strategy: StrategyKey
    kind: Literal["intraday", "close"]


class StrategyWatchSlot(BaseModel):
    """One intraday check that ran: how many stocks it compared, how many met every trigger, and
    how many of those became an alert (met on two checks in a row, guards passed)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: str
    checked: int
    all_met: int
    alertable: int


class StrategyWatchDataDates(BaseModel):
    """The latest session each input the screen reads has real rows for: the post-close refresh
    steps of the daily cycle (股價 17:40, 融資 17:40, 三大法人 20:10). A date behind the list's
    session means that refresh has not landed (or the source is failing)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    price_eod: date_ | None = None
    margin_short: date_ | None = None
    institutional_flow: date_ | None = None


class GetStrategyWatchOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    data_available: bool
    unavailable_reason: str | None = None
    run_date: date_ | None = None
    as_of: date_ | None = None
    regime: Regime | None = None
    breadth_pct: float | None = None
    last_slot: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    items: tuple[StrategyWatchItem, ...] = ()
    events: tuple[StrategyWatchEvent, ...] = ()
    masked: bool = False
    data_dates: StrategyWatchDataDates = StrategyWatchDataDates()
    slots: tuple[StrategyWatchSlot, ...] = ()


def _conditions(raw: object) -> list[StrategyCondition]:
    try:
        decoded: object = json.loads(raw) if isinstance(raw, str) else []
    except ValueError:
        return []
    if not isinstance(decoded, list):
        return []
    items = cast("list[object]", decoded)
    return [StrategyCondition.model_validate(c) for c in items if isinstance(c, dict)]


def _masked(conditions: list[StrategyCondition]) -> list[StrategyCondition]:
    """Both values go: some thresholds are intraday values too (the day's open for 紅K, its
    average price, the market median), so only which condition held is left."""
    return [c.model_copy(update={"observed": None, "required": None}) for c in conditions]


def _slots(hits: list[dict[str, object]]) -> tuple[StrategyWatchSlot, ...]:
    """The intraday checks that ran today, in time order (the close confirmation is not one)."""
    by_slot: dict[str, list[dict[str, object]]] = {}
    for hit in hits:
        slot = str(hit.get("slot", ""))
        if slot and slot != _CLOSE_SLOT:
            by_slot.setdefault(slot, []).append(hit)
    return tuple(
        StrategyWatchSlot(
            slot=slot,
            checked=len(rows),
            all_met=sum(1 for r in rows if r.get("all_met") is True),
            alertable=sum(1 for r in rows if r.get("alertable") is True),
        )
        for slot, rows in sorted(by_slot.items())
    )


type _StockStrategy = tuple[str, str]


def _index_hits(
    hits: list[dict[str, object]],
) -> tuple[dict[_StockStrategy, dict[str, object]], dict[_StockStrategy, str], set[_StockStrategy]]:
    """Per (symbol, strategy): its latest intraday hit, the earliest alertable intraday slot, and
    whether the close confirmed it."""
    latest: dict[_StockStrategy, dict[str, object]] = {}
    alerted: dict[_StockStrategy, str] = {}
    confirmed: set[_StockStrategy] = set()
    for hit in hits:
        key = (str(hit.get("symbol")), str(hit.get("strategy")))
        slot = str(hit.get("slot", ""))
        if slot == _CLOSE_SLOT:
            if hit.get("alertable") is True:
                confirmed.add(key)
            continue
        if key not in latest or slot > str(latest[key].get("slot", "")):
            latest[key] = hit
        if hit.get("alertable") is True and (key not in alerted or slot < alerted[key]):
            alerted[key] = slot
    return latest, alerted, confirmed


def _apply_latest_hit(
    values: dict[str, object], latest_hit: dict[str, object], *, masked: bool
) -> None:
    conditions = _conditions(latest_hit.get("conditions"))
    values["latest_slot"] = str(latest_hit.get("slot"))
    values["price"] = None if masked else as_float(latest_hit.get("price"))
    values["conditions"] = _masked(conditions) if masked else conditions
    values["all_met"] = latest_hit.get("all_met") is True


class GetStrategyWatchTool(SyncStoreTool):
    """See the module docstring."""

    name = "get_strategy_watch"
    description = (
        "Get today's 策略盯盤 strategy-watch list for the Taiwan market: the stocks the 08:00 "
        "screen picked per strategy (volatility squeeze, oversold bounce, A/D divergence, chip "
        "accumulation, momentum flag), each with its trigger levels, context, and which "
        "conditions held at the latest intraday check."
    )
    input_model: type[BaseModel] = GetStrategyWatchInput
    output_model: type[BaseModel] = GetStrategyWatchOutput
    data_class = DataClass.PUBLIC
    cache_ttl_seconds = 60.0  # the hits partition changes every 10 minutes in the session
    card_type: str | None = None

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def _list_date(self, wanted: date_) -> date_ | None:
        for offset in range(_MAX_BACK_DAYS + 1):
            day = wanted - timedelta(days=offset)
            if self._store.has_partition(STRATEGY_WATCHLIST_DATASET, "TW", day):
                return day
        return None

    def _data_dates(self) -> StrategyWatchDataDates:
        def latest(dataset: str) -> date_ | None:
            dates = self._store.list_partition_dates(dataset, "TW")
            return dates[-1] if dates else None

        return StrategyWatchDataDates(
            price_eod=latest(PRICE_EOD_DATASET),
            margin_short=latest(MARGIN_SHORT_DATASET),
            institutional_flow=latest(INSTITUTIONAL_FLOW_DATASET),
        )

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetStrategyWatchInput, args)
        wanted = typed.as_of or datetime.now(_TAIPEI).date()
        day = self._list_date(wanted)
        if day is None:
            return GetStrategyWatchOutput(
                data_available=False,
                unavailable_reason=(
                    "No strategy-watch list in the last 10 days: the 08:00 screen skips when "
                    "the stored prices are stale."
                ),
                data_dates=self._data_dates(),
            )
        watch = self._store.read_partition(STRATEGY_WATCHLIST_DATASET, "TW", day)
        hits = (
            self._store.read_partition(STRATEGY_HITS_DATASET, "TW", day)
            if self._store.has_partition(STRATEGY_HITS_DATASET, "TW", day)
            else []
        )
        masked = not is_entitled(DataClass.SPONSOR, ctx.tier)
        items = self._items(watch, hits, masked=masked)
        if typed.strategy is not None:
            items = [i for i in items if i.strategy == typed.strategy]
        intraday_slots = [str(h.get("slot")) for h in hits if h.get("slot") != _CLOSE_SLOT]
        first = watch[0] if watch else {}
        regime = first.get("regime")
        as_of = first.get("as_of")
        counts = Counter(str(r.get("strategy")) for r in watch)
        return GetStrategyWatchOutput(
            data_available=True,
            run_date=day,
            as_of=date_.fromisoformat(as_of) if isinstance(as_of, str) else None,
            regime=regime if regime in ("risk_on", "neutral", "risk_off") else None,
            breadth_pct=as_float(first.get("breadth_pct")),
            last_slot=max(intraday_slots, default=None),
            counts={key: counts.get(key, 0) for key in STRATEGY_KEYS},
            items=tuple(items),
            events=tuple(self._events(hits, watch)),
            masked=masked,
            data_dates=self._data_dates(),
            slots=_slots(hits),
        )

    def _items(
        self,
        watch: list[dict[str, object]],
        hits: list[dict[str, object]],
        *,
        masked: bool,
    ) -> list[StrategyWatchItem]:
        latest, alerted, confirmed = _index_hits(hits)
        fields = set(StrategyWatchItem.model_fields)
        items: list[StrategyWatchItem] = []
        for row in watch:
            strategy = row.get("strategy")
            if strategy not in STRATEGY_KEYS:
                continue
            key = (str(row.get("symbol")), str(strategy))
            values = {k: v for k, v in row.items() if k in fields and v is not None}
            if masked:
                for name in _SPONSOR_FIELDS:
                    values.pop(name, None)
            latest_hit = latest.get(key)
            if latest_hit is not None:
                _apply_latest_hit(values, latest_hit, masked=masked)
            values["alerted_at"] = alerted.get(key)
            values["close_confirmed"] = key in confirmed
            items.append(StrategyWatchItem.model_validate(values))
        return items

    def _events(
        self, hits: list[dict[str, object]], watch: list[dict[str, object]]
    ) -> list[StrategyWatchEvent]:
        """Each (stock, strategy)'s first alertable intraday slot and its close confirmation, in
        time order: the page's 今日條件達成 feed."""
        names = {str(r.get("symbol")): r.get("name") for r in watch}
        first: dict[tuple[str, str, str], str] = {}
        for hit in hits:
            if hit.get("alertable") is not True or hit.get("strategy") not in STRATEGY_KEYS:
                continue
            slot = str(hit.get("slot", ""))
            kind = "close" if slot == _CLOSE_SLOT else "intraday"
            key = (str(hit.get("symbol")), str(hit.get("strategy")), kind)
            if key not in first or slot < first[key]:
                first[key] = slot
        events = [
            StrategyWatchEvent(
                slot=slot,
                symbol=symbol,
                name=cast("str | None", names.get(symbol)),
                strategy=cast("StrategyKey", strategy),
                kind=cast("Literal['intraday', 'close']", kind),
            )
            for (symbol, strategy, kind), slot in first.items()
        ]
        return sorted(events, key=lambda e: (e.slot, e.symbol))


__all__ = [
    "GetStrategyWatchInput",
    "GetStrategyWatchOutput",
    "GetStrategyWatchTool",
    "StrategyCondition",
    "StrategyWatchDataDates",
    "StrategyWatchEvent",
    "StrategyWatchItem",
    "StrategyWatchSlot",
]
