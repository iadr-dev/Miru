"""Layer 2/3 composition for the events calendar.

Composes forward events for one `(symbol, market)` from:

- **Layer 1** — the rule engine (`calendar/rules.py`), zero feeds.
- **Layers 2/3** — existing feeds + gap scrapers, both reached via the `calendar_events`
  capability on the data-access router (never a source directly — `dataaccess/router.py`
  resolves the configured source priority list for that capability, e.g.
  `[finmind, mops, calendar_ext, rule_engine]`).

This module performs **no I/O itself** (ADR-0002/ADR-0019 §3 "calendar module composes;
adapters do I/O") — it only calls `DataAccessRouter.fetch()` and merges the result with
Layer-1 output. As of this phase, **no feed adapter for `calendar_events` has been built**
(MOPS/`calendar_ext` are Phase-2-skipped scrapers) — the router already degrades to its own
`unavailable` `SourceResult` in that case (never raises), so `get_calendar_events` below
simply carries that through: feed-sourced events list stays empty, rule-engine events still
render, and the caller sees the gap via the returned `SourceResult.health`, not a crash.
"""

from __future__ import annotations

from calendar.models import CalendarEvent, EventState, EventType
from datetime import date as date_
from typing import cast

from dataaccess.router import DataAccessRouter
from schemas.enums import Capability, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceResult

# Feed-sourced calendar rows are expected under this payload key, matching the
# `row_key="rows"`/list-of-dict convention already used by sources/finmind.py et al. Until a
# `calendar_events`-capable adapter exists, the router's own `unavailable` result has an empty
# payload, so this key is simply absent — handled below without guessing at row shape.
_FEED_ROW_KEY = "events"

_STATE_BY_LABEL: dict[str, EventState] = {state.value: state for state in EventState}
_TYPE_BY_LABEL: dict[str, EventType] = {etype.value: etype for etype in EventType}

type _UntypedRows = list[dict[str, object]]


def _feed_event_from_row(
    row: dict[str, object], symbol: Symbol, source_id: str
) -> CalendarEvent | None:
    """Map one feed row to a `CalendarEvent`. Defensive: an unrecognized/malformed row is
    skipped (never guessed at) rather than raising — a single bad row must not blank the
    whole calendar view."""
    raw_type = row.get("type")
    raw_state = row.get("state")
    event_type = _TYPE_BY_LABEL.get(str(raw_type)) if raw_type is not None else None
    event_state = _STATE_BY_LABEL.get(str(raw_state)) if raw_state is not None else None
    if event_type is None or event_state is None:
        return None

    event_date_raw = row.get("event_date")
    event_date = date_.fromisoformat(str(event_date_raw)) if event_date_raw else None
    window_start_raw = row.get("window_start")
    window_end_raw = row.get("window_end")
    window_start = date_.fromisoformat(str(window_start_raw)) if window_start_raw else None
    window_end = date_.fromisoformat(str(window_end_raw)) if window_end_raw else None

    try:
        return CalendarEvent(
            id=str(row.get("id") or f"{source_id}-{symbol.id}-{event_type.value}-{event_date_raw}"),
            market=symbol.market,
            stock_id=symbol.id,
            type=event_type,
            title_zh=str(row.get("title_zh") or event_type.value),
            title_en=str(row.get("title_en") or event_type.value),
            event_date=event_date,
            window_start=window_start,
            window_end=window_end,
            state=event_state,
            source=source_id,
        )
    except ValueError:
        # CalendarEvent's model_validator rejects a row missing the date/window its state
        # requires — skip rather than propagate (defensive against upstream feed drift).
        return None


async def fetch_feed_events(
    router: DataAccessRouter,
    symbol: Symbol,
    *,
    start_date: date_,
    end_date: date_,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
) -> tuple[tuple[CalendarEvent, ...], SourceResult]:
    """Layer 2/3 — fetch `calendar_events` for one `(symbol, market)` over `[start, end]`.

    Merges EVERY configured adapter's contribution (`router.fetch_all`), not just the first
    healthy one — `calendar_events` today composes NCDR closure alerts and TWSE AGM/ex-rights
    schedules, two disjoint feeds that both apply to a stock's calendar at once rather than one
    superseding the other (see `config/source_priority.yaml`'s own comment on this capability).
    Returns both the parsed events (possibly empty) and one representative `SourceResult` (the
    first healthy adapter's, or the last adapter's if none were healthy) so the caller can still
    surface source health/currentness — degrades gracefully when no adapter is configured for
    this capability/market (the router's own `unavailable` result, never a hard failure).
    """
    results = await router.fetch_all(
        Capability.CALENDAR_EVENTS,
        symbol,
        tls_policy=tls_policy,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    if not results:
        result = await router.fetch(Capability.CALENDAR_EVENTS, symbol, tls_policy=tls_policy)
        return (), result

    events: list[CalendarEvent] = []
    for result in results:
        if result.health.status is not HealthStatus.OK:
            continue
        rows = result.payload.get(_FEED_ROW_KEY)
        if not isinstance(rows, list):
            continue
        typed_rows = cast(_UntypedRows, rows)
        events.extend(
            event
            for row in typed_rows
            if (event := _feed_event_from_row(row, symbol, result.source_id)) is not None
        )
    representative = next((r for r in results if r.health.status is HealthStatus.OK), results[-1])
    return tuple(events), representative


def _dividend_event_from_row(
    row: dict[str, object], symbol: Symbol, source_id: str, as_of: date_
) -> CalendarEvent | None:
    """Map one normalized `Capability.DIVIDENDS` row (`sources/finmind/fundamental.py`'s
    `_map_dividends` shape: `cash_ex_dividend_date`/`stock_ex_dividend_date`) to a
    `dividend_ex_rights` event — cash ex-dividend date preferred, stock ex-dividend date as the
    fallback when a row carries only a stock dividend. A row with neither is skipped. `as_of`
    decides `released` (the ex-dividend date has already passed) vs `scheduled` (still ahead)."""
    ex_date_raw = row.get("cash_ex_dividend_date") or row.get("stock_ex_dividend_date")
    if not ex_date_raw:
        return None
    try:
        ex_date = date_.fromisoformat(str(ex_date_raw))
    except ValueError:
        return None
    fiscal_year = str(row.get("fiscal_year") or "")
    return CalendarEvent(
        id=f"{source_id}-{symbol.id}-dividend-{fiscal_year}-{ex_date.isoformat()}",
        market=symbol.market,
        stock_id=symbol.id,
        type=EventType.DIVIDEND_EX_RIGHTS,
        title_zh="除權息",
        title_en="Ex-Dividend/Ex-Rights Date",
        event_date=ex_date,
        state=EventState.RELEASED if ex_date <= as_of else EventState.SCHEDULED,
        source=source_id,
    )


def _cb_put_event_from_row(
    row: dict[str, object], symbol: Symbol, source_id: str
) -> CalendarEvent | None:
    """Map one normalized `Capability.CB_PUT_PROVISION` row
    (`sources/finmind/convertible_bond.py`'s `_map_cb_put_provision` shape: `date`/`name`) to a
    `cb_put_or_maturity` event."""
    date_raw = row.get("date")
    if not date_raw:
        return None
    try:
        put_date = date_.fromisoformat(str(date_raw))
    except ValueError:
        return None
    name = str(row.get("name") or "")
    return CalendarEvent(
        id=f"{source_id}-{symbol.id}-cb-put-{put_date.isoformat()}",
        market=symbol.market,
        stock_id=symbol.id,
        type=EventType.CB_PUT_OR_MATURITY,
        title_zh=f"CB 賣回權/到期{f'（{name}）' if name else ''}",
        title_en=f"CB Put/Maturity{f' ({name})' if name else ''}",
        event_date=put_date,
        state=EventState.SCHEDULED,
        source=source_id,
    )


async def fetch_dividend_events(  # noqa: PLR0913 — one param per fetch dimension; mirrors
    # `fetch_feed_events`'s own shape plus the `as_of` reference-date threading.
    router: DataAccessRouter,
    symbol: Symbol,
    *,
    start_date: date_,
    end_date: date_,
    as_of: date_,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
) -> tuple[tuple[CalendarEvent, ...], SourceResult]:
    """除權息/ETF配息 (`Capability.DIVIDENDS`) — real feed-sourced `dividend_ex_rights` events
    for one `(symbol, market)` over `[start, end]`, degrading to an empty tuple (never raising)
    when the capability is unconfigured/unhealthy for this market, exactly like
    `fetch_feed_events` above."""
    result = await router.fetch(
        Capability.DIVIDENDS,
        symbol,
        tls_policy=tls_policy,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    if result.health.status is not HealthStatus.OK:
        return (), result
    rows = result.payload.get("events")
    if not isinstance(rows, list):
        return (), result
    typed_rows = cast(_UntypedRows, rows)
    events = tuple(
        event
        for row in typed_rows
        if (event := _dividend_event_from_row(row, symbol, result.source_id, as_of)) is not None
    )
    return events, result


async def fetch_cb_put_events(  # noqa: PLR0913 — signature symmetry with `fetch_dividend_events`
    router: DataAccessRouter,
    symbol: Symbol,
    *,
    start_date: date_,
    end_date: date_,
    as_of: date_,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
) -> tuple[tuple[CalendarEvent, ...], SourceResult]:
    """CB 賣回權/到期 (`Capability.CB_PUT_PROVISION`) — real feed-sourced `cb_put_or_maturity`
    events for one `(symbol, market)` over `[start, end]`, degrading to an empty tuple (never
    raising) when the capability is unconfigured/unhealthy for this market. `as_of` is accepted
    for signature symmetry with `fetch_dividend_events` but unused — a CB put/maturity date is
    always a forward `scheduled` marker, never resolved to `released` by this composition."""
    del as_of
    result = await router.fetch(
        Capability.CB_PUT_PROVISION,
        symbol,
        tls_policy=tls_policy,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    if result.health.status is not HealthStatus.OK:
        return (), result
    rows = result.payload.get("rows")
    if not isinstance(rows, list):
        return (), result
    typed_rows = cast(_UntypedRows, rows)
    events = tuple(
        event
        for row in typed_rows
        if (event := _cb_put_event_from_row(row, symbol, result.source_id)) is not None
    )
    return events, result


def _within_range(event: CalendarEvent, start_date: date_, end_date: date_) -> bool:
    if event.event_date is not None:
        return start_date <= event.event_date <= end_date
    if event.window_start is not None and event.window_end is not None:
        return event.window_start <= end_date and event.window_end >= start_date
    return False  # pragma: no cover — model_validator guarantees one of the above is set


async def get_calendar_events(  # noqa: PLR0913 — the tool-facing composition API;
    # each param is independently meaningful to a caller.
    router: DataAccessRouter,
    symbol: Symbol,
    *,
    start_date: date_,
    end_date: date_,
    rule_engine_events: tuple[CalendarEvent, ...] = (),
    as_of: date_ | None = None,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
    include_cb: bool = True,
) -> tuple[tuple[CalendarEvent, ...], SourceResult]:
    """The tool-facing `get_calendar_events` composition for one symbol over
    one bounded date range — never a whole-market/whole-watchlist scan in one call.

    `rule_engine_events` lets the caller pass in whatever Layer-1 markers it already generated
    (e.g. via `calendar/rules.py` helpers) for this symbol/period; this function does not
    regenerate them, only merges + filters to the requested window. `as_of` (default: today)
    is the reference date used to decide whether a feed-confirmed event has already happened
    (`released`) or is still ahead (`scheduled`) — a caller runs this at a fixed as-of date in
    tests, never `date.today()` implicitly.

    Composes, in one call: the `calendar_events`-registered feed adapter (if any — currently
    only `sources/ncdr_closure.py` for TW, which supplies no rows of the shape this function
    reads), 除權息/ETF配息 (`Capability.DIVIDENDS`), and CB 賣回權/到期 (`Capability.
    CB_PUT_PROVISION`). The returned `SourceResult` is the `calendar_events` feed fetch's own
    result (preserving this function's existing contract) — dividend/CB source health is
    carried on each mapped event's own `source` field instead of a second return value.

    `include_cb=False` skips the CB fetch altogether — CB 賣回權/到期 is Sponsor-tier data, so an
    unentitled caller gets the free events only, without the paid feed ever being read.
    """
    reference_date = as_of if as_of is not None else date_.today()
    feed_events, feed_result = await fetch_feed_events(
        router, symbol, start_date=start_date, end_date=end_date, tls_policy=tls_policy
    )
    dividend_events, _dividend_result = await fetch_dividend_events(
        router,
        symbol,
        start_date=start_date,
        end_date=end_date,
        as_of=reference_date,
        tls_policy=tls_policy,
    )
    cb_put_events: tuple[CalendarEvent, ...] = ()
    if include_cb:
        cb_put_events, _cb_put_result = await fetch_cb_put_events(
            router,
            symbol,
            start_date=start_date,
            end_date=end_date,
            as_of=reference_date,
            tls_policy=tls_policy,
        )
    merged = tuple(
        event
        for event in (*rule_engine_events, *feed_events, *dividend_events, *cb_put_events)
        if _within_range(event, start_date, end_date)
    )
    return merged, feed_result


__all__ = [
    "fetch_cb_put_events",
    "fetch_dividend_events",
    "fetch_feed_events",
    "get_calendar_events",
]
