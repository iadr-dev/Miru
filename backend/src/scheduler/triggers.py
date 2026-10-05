"""Trigger resolution — turns a `ScheduleTrigger` into the next concrete fire time.

v1 never resolved triggers: the router stored no `next_run` (so API-created jobs never became
due) and the worker just advanced by one day. This module is the single resolver both callers
use. It is pure — no I/O, no clock — so it is trivially testable, and `is_trading_day` is an
injected seam (defaults to weekdays minus the market's scheduled holidays, see
`calendar.holidays`) so a `TRADING_CALENDAR`-backed check can swap in without touching the resolver.

Times are evaluated in the trigger's *market* timezone (Asia/Taipei for TW, America/New_York
for US) and returned in UTC. Cadence stays coarse by construction: a market event fires once per
trading day; a cron needs concrete minute and hour fields (enforced by `scheduler.validation`).
"""

from __future__ import annotations

from calendar.holidays import is_market_open_day
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from scheduler.models import MarketEvent, ScheduleTrigger, TriggerType
from schemas.enums import Market

TradingDayCheck = Callable[[date, Market], bool]

_MARKET_TZ: dict[Market, str] = {Market.TW: "Asia/Taipei", Market.US: "America/New_York"}
_DEFAULT_TZ = "Asia/Taipei"

# Local wall-clock fire times per market event. after_close lands after the official EOD data is
# normally published; pre_open before the session; morning_digest before pre_open.
_EVENT_TIMES: dict[Market, dict[MarketEvent, time]] = {
    Market.TW: {
        MarketEvent.MORNING_DIGEST: time(8, 0),
        MarketEvent.PRE_OPEN: time(8, 30),
        MarketEvent.AFTER_CLOSE: time(14, 30),
    },
    Market.US: {
        MarketEvent.MORNING_DIGEST: time(8, 0),
        MarketEvent.PRE_OPEN: time(8, 30),
        MarketEvent.AFTER_CLOSE: time(16, 30),
    },
}

# How far ahead the resolver searches before giving up. A cron that matches nothing in a year
# (e.g. Feb 30) is invalid, not "never"; this bound is what turns that into an error.
_HORIZON_DAYS = 366


class UnresolvableTriggerError(ValueError):
    """The trigger spec is malformed or never matches within the search horizon."""


def market_timezone(market: Market) -> ZoneInfo:
    return ZoneInfo(_MARKET_TZ.get(market, _DEFAULT_TZ))


_tz_for = market_timezone


# ---- cron ----------------------------------------------------------------------------------


def _parse_field(raw: str, low: int, high: int) -> frozenset[int]:
    """One cron field -> the set of allowed values. Supports `*`, `*/n`, `a`, `a-b`, `a-b/n`
    and comma lists."""
    values: set[int] = set()
    for part in raw.split(","):
        base, _, step_raw = part.partition("/")
        step = int(step_raw) if step_raw else 1
        if step < 1:
            raise UnresolvableTriggerError(f"invalid cron step in {raw!r}")
        if base == "*":
            start, end = low, high
        elif "-" in base:
            lo_raw, _, hi_raw = base.partition("-")
            start, end = int(lo_raw), int(hi_raw)
        else:
            start = int(base)
            end = high if step_raw else start
        if start < low or end > high or start > end:
            raise UnresolvableTriggerError(f"cron value out of range in {raw!r}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


class _Cron:
    def __init__(self, spec: str) -> None:
        fields = spec.split()
        if len(fields) != 5:  # noqa: PLR2004 — min hour dom mon dow
            raise UnresolvableTriggerError("cron must have 5 fields (min hour dom mon dow)")
        try:
            self.minutes = sorted(_parse_field(fields[0], 0, 59))
            self.hours = sorted(_parse_field(fields[1], 0, 23))
            self.dom = _parse_field(fields[2], 1, 31)
            self.months = _parse_field(fields[3], 1, 12)
            dow = _parse_field(fields[4], 0, 7)
        except ValueError as exc:
            raise UnresolvableTriggerError(f"invalid cron {spec!r}") from exc
        self.dow = frozenset(0 if d == 7 else d for d in dow)  # noqa: PLR2004 — 7 == Sunday
        self.dom_restricted = fields[2] != "*"
        self.dow_restricted = fields[4] != "*"

    def day_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.dom
        dow_ok = (day.weekday() + 1) % 7 in self.dow  # cron: Sun=0, Mon=1
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok  # standard cron: either restricted field may match
        return dom_ok and dow_ok


def validate_cron(spec: str) -> None:
    """Raise `UnresolvableTriggerError` if `spec` is not a parseable 5-field cron."""
    _Cron(spec)


# ---- resolution ----------------------------------------------------------------------------


def next_run_after(
    trigger: ScheduleTrigger,
    after: datetime,
    *,
    is_trading_day: TradingDayCheck | None = None,
) -> datetime:
    """The first fire time strictly after `after`, in UTC.

    Market events fire only on trading days (per `is_trading_day`, default: weekdays minus the
    market's scheduled holidays). A cron fires on the days it names, at its concrete times, in the
    trigger's market timezone.
    """
    if after.tzinfo is None:
        raise ValueError("`after` must be timezone-aware")
    tz = _tz_for(trigger.market)
    local_after = after.astimezone(tz)
    trading = is_trading_day or is_market_open_day

    if trigger.type is TriggerType.MARKET_EVENT:
        try:
            fire_time = _EVENT_TIMES[trigger.market][MarketEvent(trigger.spec)]
        except (KeyError, ValueError) as exc:
            raise UnresolvableTriggerError(f"unknown market event {trigger.spec!r}") from exc
        for offset in range(_HORIZON_DAYS):
            day = local_after.date() + timedelta(days=offset)
            if not trading(day, trigger.market):
                continue
            candidate = datetime.combine(day, fire_time, tzinfo=tz)
            if candidate > local_after:
                return candidate.astimezone(UTC)
        raise UnresolvableTriggerError("no trading day found within the search horizon")

    cron = _Cron(trigger.spec)
    for offset in range(_HORIZON_DAYS):
        day = local_after.date() + timedelta(days=offset)
        if not cron.day_matches(day):
            continue
        for hour in cron.hours:
            for minute in cron.minutes:
                candidate = datetime.combine(day, time(hour, minute), tzinfo=tz)
                if candidate > local_after:
                    return candidate.astimezone(UTC)
    raise UnresolvableTriggerError(f"cron {trigger.spec!r} never fires within a year")


__all__ = [
    "TradingDayCheck",
    "UnresolvableTriggerError",
    "market_timezone",
    "next_run_after",
    "validate_cron",
]
