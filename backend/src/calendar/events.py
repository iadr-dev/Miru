"""Layer 2/3 composition for the events calendar.

Composes forward events for one `(symbol, market)` from:

- **Layer 1** — the rule engine (`calendar/rules.py`), zero feeds.
- **Layer 2 (`calendar_events`-registered feeds)** — reached via the `calendar_events`
  capability on the data-access router (never a source directly — `dataaccess/router.py`
  resolves the configured source priority list for that capability). No adapter is
  registered for `(calendar_events, TW)` today beyond `sources/ncdr_closure.py` (typhoon
  closures, not the 除權息/CB events this composes) — the router degrades to its own
  `unavailable` `SourceResult` in that case (never raises), so that path stays empty.
- **Layer 2 (already-wired underlying capabilities)** — `Capability.DIVIDENDS` (除權息/ETF配息,
  FinMind `TaiwanStockDividend`) and `Capability.CB_PUT_PROVISION` (CB 賣回權/到期, FinMind
  `TaiwanStockConvertibleBondPutProvision`) already exist behind the router for other tools
  (`get_dividend_schedule`, `get_convertible_bond_profile`); this module fetches each of them
  directly (one router call per capability) and maps their rows into `CalendarEvent`s, rather
  than inventing a new `sources/`-level adapter that would have to call back into
  `DataAccessRouter` itself. A `sources/` adapter may never depend on `dataaccess/` — it is the
  layer *below* it (`tests/arch/test_layering.py`'s `LAYER_ORDER` places `sources` last,
  `dataaccess` second-to-last; a `sources`→`dataaccess` edge would be an upward-layer import,
  the exact thing that test forbids) — so composing multiple existing capabilities' data into
  one calendar view belongs here, in `calendar/`, which already sits above `dataaccess/` and is
  designed to be the composition point: the calendar module composes, adapters do I/O. No new
  network call is introduced by any of this — every fetch below reuses an adapter already wired
  for its own capability.

股東會 (AGM) and 法說會 (investor conference) are **not** composed here: MOPS's only real TW
adapter today (`sources/mops.py`'s `CORPORATE_ACTIONS` capability, the 重大訊息 material-
announcement stream) returns free-text `subject`/`detail` fields, not a structured
company/date/event-type triple — some announcements happen to describe an upcoming board
meeting or filing date in prose, but there is no reliable, non-guessing way to pick "this
announcement is an AGM/法說會 schedule" out of that stream. Wiring those two event types
would require either a real MOPS AGM/法說會-schedule endpoint (`t100sb02_1`) or free-text
classification this module deliberately does not attempt. This is an honest, disclosed gap,
not a silently dropped feature.

This module performs **no I/O itself** — it only calls `DataAccessRouter.fetch()` (possibly
several times, once per composed capability) and merges the results with Layer-1 output.
"""

from __future__ import annotations

from calendar.models import CalendarEvent, EventState, EventType
from datetime import date as date_
from typing import cast

from pydantic import ValidationError

from dataaccess.router import DataAccessRouter
from schemas.capabilities import ConvertibleBondPutProvisionRow, DividendEvent
from schemas.enums import Capability, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceResult

# Feed-sourced calendar rows (from a real `calendar_events`-registered adapter, e.g. a future
# MOPS AGM/法說會 scraper) are expected under this payload key, matching the
# `row_key="rows"`/list-of-dict convention already used by sources/finmind.py et al.
_FEED_ROW_KEY = "events"

_STATE_BY_LABEL: dict[str, EventState] = {state.value: state for state in EventState}
_TYPE_BY_LABEL: dict[str, EventType] = {etype.value: etype for etype in EventType}

# The one cast() target type every already-validated-list-of-dict payload row uses below.
type _TypedRows = list[dict[str, object]]


def _state_for_date(event_date: date_, *, as_of: date_) -> EventState:
    """A feed-confirmed date that has not yet happened is `scheduled`; once `as_of` has passed
    it, the underlying settlement/payment has already occurred, so it reads as `released`
    (honest per-event state, never a raw field carried straight through)."""
    return EventState.SCHEDULED if event_date >= as_of else EventState.RELEASED


def _dividend_calendar_events(
    rows: list[dict[str, object]], symbol: Symbol, source_id: str, *, as_of: date_
) -> list[CalendarEvent]:
    """Map `Capability.DIVIDENDS` rows (`DividendEvent`-shaped) into `EventType.
    DIVIDEND_EX_RIGHTS` calendar events — one per ex-dividend date actually present on the row
    (cash and stock ex-dividend dates are independent fields and may both, either, or neither
    be set on a given announcement)."""
    events: list[CalendarEvent] = []
    for row in rows:
        try:
            dividend = DividendEvent.model_validate(row)
        except ValidationError:
            continue  # a malformed row must not blank the whole calendar view
        for label_zh, label_en, ex_date in (
            ("除息交易日", "Cash Ex-Dividend Date", dividend.cash_ex_dividend_date),
            ("除權交易日", "Stock Ex-Dividend Date", dividend.stock_ex_dividend_date),
        ):
            if ex_date is None:
                continue
            events.append(
                CalendarEvent(
                    id=f"dividend-{symbol.id}-{ex_date.isoformat()}-{label_en}",
                    market=symbol.market,
                    stock_id=symbol.id,
                    type=EventType.DIVIDEND_EX_RIGHTS,
                    title_zh=f"{label_zh}（{dividend.fiscal_year}年度）",
                    title_en=f"{label_en} (FY{dividend.fiscal_year})",
                    event_date=ex_date,
                    state=_state_for_date(ex_date, as_of=as_of),
                    source=source_id,
                )
            )
    return events


def _cb_put_calendar_events(
    rows: list[dict[str, object]], symbol: Symbol, source_id: str, *, as_of: date_
) -> list[CalendarEvent]:
    """Map `Capability.CB_PUT_PROVISION` rows (`ConvertibleBondPutProvisionRow`-shaped) into
    `EventType.CB_PUT_OR_MATURITY` calendar events.

    `Capability.CB_PUT_PROVISION` is keyed on a bond's `cb_id`, never an equity `stock_id` (see
    `sources/finmind/_shared.py::_row_cb_symbol`) — this only produces events when the caller's
    `symbol.id` is itself a `cb_id` (the same convention `get_convertible_bond_profile` already
    uses); calling `get_calendar_events` for an equity symbol simply yields no CB rows here, not
    a wrong or fabricated mapping."""
    events: list[CalendarEvent] = []
    for row in rows:
        try:
            put_row = ConvertibleBondPutProvisionRow.model_validate(row)
        except ValidationError:
            continue
        events.append(
            CalendarEvent(
                id=f"cb-put-{symbol.id}-{put_row.date.isoformat()}",
                market=symbol.market,
                stock_id=symbol.id,
                type=EventType.CB_PUT_OR_MATURITY,
                title_zh=f"{put_row.name} 賣回權時程",
                title_en=f"{put_row.name} Put Provision",
                event_date=put_row.date,
                state=_state_for_date(put_row.date, as_of=as_of),
                source=source_id,
            )
        )
    return events


async def fetch_dividend_events(  # noqa: PLR0913 — each kwarg (router/symbol/range/as-of/tls)
    # is independently meaningful; matches this module's existing `get_calendar_events` shape.
    router: DataAccessRouter,
    symbol: Symbol,
    *,
    start_date: date_,
    end_date: date_,
    as_of: date_,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
) -> tuple[tuple[CalendarEvent, ...], SourceResult]:
    """除權息/ETF配息 — `Capability.DIVIDENDS`, already wired for `get_dividend_schedule`."""
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
    typed_rows = cast(_TypedRows, rows)
    events = _dividend_calendar_events(typed_rows, symbol, result.source_id, as_of=as_of)
    return tuple(events), result


async def fetch_cb_put_events(  # noqa: PLR0913 — see `fetch_dividend_events` above.
    router: DataAccessRouter,
    symbol: Symbol,
    *,
    start_date: date_,
    end_date: date_,
    as_of: date_,
    tls_policy: TLSPolicy = TLSPolicy.STRICT,
) -> tuple[tuple[CalendarEvent, ...], SourceResult]:
    """CB 賣回權/到期 — `Capability.CB_PUT_PROVISION`, already wired for
    `get_convertible_bond_profile`. Sponsor-tier data at the source; this composition performs
    no entitlement check itself (that happens at the tool-dispatch seam, same as every other
    sponsor capability)."""
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
    typed_rows = cast(_TypedRows, rows)
    events = _cb_put_calendar_events(typed_rows, symbol, result.source_id, as_of=as_of)
    return tuple(events), result


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

    Returns both the parsed events (possibly empty) and the raw `SourceResult` so the caller
    can surface source health/currentness — degrades gracefully when no adapter is configured
    for this capability/market (the router's own `unavailable` result, never a hard failure).
    """
    result = await router.fetch(
        Capability.CALENDAR_EVENTS,
        symbol,
        tls_policy=tls_policy,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    if result.health.status is not HealthStatus.OK:
        return (), result

    rows = result.payload.get(_FEED_ROW_KEY)
    if not isinstance(rows, list):
        return (), result
    typed_rows = cast(_TypedRows, rows)
    events = tuple(
        event
        for row in typed_rows
        if (event := _feed_event_from_row(row, symbol, result.source_id)) is not None
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
