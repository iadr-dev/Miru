"""Finance-calendar adapter — real, third-party US/global macro-release event data.

Wraps `financecalendar.com`'s free, keyless, CORS-open JSON API
(`https://www.financecalendar.com/wp-json/fc/v1`), confirmed live: `/calendar` (date-range +
impact/category filters) returns real upcoming/recent macro releases — US GDP, PCE inflation,
ISM Manufacturing PMI, the US jobs report (NFP), FOMC rate decisions, plus international
central-bank/CPI events — each with `consensus`/`prior`/`actual` fields (populated as the release
date approaches/passes; `null` well in advance, per that API's own documented behavior).

**Distinct from, and additive to, `Capability.ECONOMIC_CALENDAR`** (yfinance `Calendars`,
`EconomicCalendarRow`, already wired into `get_calendar_events_feed`) — that feed's coverage of
headline US releases is unverified/thin; this one was specifically confirmed live to carry real
GDP/CPI/PCE/PMI/NFP/FOMC rows. Neither replaces the other.

**Attribution requirement, not a governance exception**: this upstream's own terms require
"link to financecalendar.com wherever the data appears" — the same cite-the-source posture every
other third-party feed in this codebase already follows (`sources/news.py`'s own `_CAVEAT`), made
explicit here since the upstream states it as a condition of free use rather than leaving it
implicit.

Not a `(capability, symbol)`-scoped fetch — a calendar query has no symbol — so this is a plain
class reached via `dataaccess/financecalendar.py`'s gateway, the same non-router carve-out shape
`sources/news.py::NewsAdapter` and `sources/web_search.py` already establish.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import cast

import httpx
import structlog

from schemas.capabilities import MacroReleaseEventRow
from schemas.enums import DataClass, Market, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_BASE_URL = "https://www.financecalendar.com/wp-json/fc/v1"
_SOURCE_ID = "FINANCE_CALENDAR"

_CAVEAT = (
    "Third-party macro-release calendar data (financecalendar.com) — consensus/prior/actual are "
    "cited upstream figures, not our own verified release schedule; never restate as a trading "
    "signal. Attribution required by the source's own terms: data courtesy of "
    "financecalendar.com."
)

# The upstream's own documented cap on a `from`/`to` date-range window.
_MAX_DAYS_AHEAD = 92

type _UntypedList = list[object]
type _UntypedDict = dict[str, object]


def _parse_calendar_body(raw: object) -> list[MacroReleaseEventRow]:
    if not isinstance(raw, dict):
        return []
    body = cast(_UntypedDict, raw)
    raw_events = body.get("events")
    if not isinstance(raw_events, list):
        return []
    rows: list[MacroReleaseEventRow] = []
    for raw_event in cast(_UntypedList, raw_events):
        if not isinstance(raw_event, dict):
            continue
        event = cast(_UntypedDict, raw_event)
        name = str(event.get("name", "")).strip()
        title = str(event.get("title", "")).strip()
        date_str = event.get("date")
        if not name or not title or not isinstance(date_str, str):
            continue
        time_utc_raw = event.get("time_utc")
        time_utc = None
        if isinstance(time_utc_raw, str):
            try:
                time_utc = datetime.fromisoformat(time_utc_raw)
            except ValueError:
                time_utc = None
        rows.append(
            MacroReleaseEventRow(
                market=Market.GLOBAL,
                date=date.fromisoformat(date_str),
                time_utc=time_utc,
                name=name,
                title=title,
                impact=str(event.get("impact", "")).strip(),
                category=str(event.get("category", "")).strip(),
                consensus=_optional_str(event.get("consensus")),
                prior=_optional_str(event.get("prior")),
                actual=_optional_str(event.get("actual")),
                url=_optional_str(event.get("url")),
            )
        )
    return rows


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


class FinanceCalendarAdapter:
    """The `FINANCE_CALENDAR` source adapter — one real HTTP call per `fetch_calendar`, never a
    background poller (governance's user-driven-only rule, same as `NewsAdapter`)."""

    source_id = _SOURCE_ID
    timing_class = TimingClass.REFERENCE

    async def fetch_calendar(
        self,
        *,
        impact: str | None = None,
        days_ahead: int = 14,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """`/calendar` — upcoming events from today through `days_ahead` days out (capped at the
        upstream's own 92-day window), optionally filtered to one `impact` level."""
        today = datetime.now(UTC).date()
        end = today + timedelta(days=min(days_ahead, _MAX_DAYS_AHEAD))
        params: dict[str, str] = {"from": today.isoformat(), "to": end.isoformat()}
        if impact is not None:
            params["impact"] = impact

        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(f"{_BASE_URL}/calendar", params=params)
                response.raise_for_status()
                raw: object = response.json()
        except httpx.HTTPError as exc:
            logger.warning("finance_calendar_fetch_failed", error=str(exc))
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"{type(exc).__name__}: {exc}"
                ),
            )

        rows = _parse_calendar_body(raw)
        status = HealthStatus.OK if rows else HealthStatus.STALE
        detail = "ok" if rows else "no events in this window"
        return _result(
            payload={"events": [row.model_dump(mode="json") for row in rows]},
            health=SourceHealth(status=status, detail=detail),
        )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=_SOURCE_ID,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["FinanceCalendarAdapter"]
