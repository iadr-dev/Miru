"""NCDR/DGPA closure adapter — work-suspension / market-closure evidence (typhoon days and
similar force-majeure suspensions), event-driven, Taiwan-wide (not per-symbol).

The upstream is NCDR's public CAP (Common Alerting Protocol) Atom/JSON feed for
"停班停課" (work/school suspension) alerts — the same civil-service announcements that
TWSE/TPEX honor when deciding whether a trading day actually opens. It is a live feed of
currently-active alerts (each carrying its own `effective`/`expires` window), not a
historical archive — no auth, no query parameters.

Each feed entry's `summary` field is a free-text HTML string of the shape
"[停班停課通知]<area>:<M/D>停止上班、停止上課。<...>" — the area name and the affected date(s)
are embedded in that text rather than broken into separate structured fields, so this adapter
parses them out with a small regex rather than treating `summary` as opaque prose. `effective`
supplies the year (the embedded M/D has no year of its own).

**Output shape is deliberately aligned with `calendar.trading_days.TradingDayCalendar`'s
`closure_dates: frozenset[date]` overlay** so a caller can go straight from this adapter's
payload to that primitive with no reshaping: `payload["closure_dates"]` is a list of ISO-8601
date strings (`date.fromisoformat` on each element yields exactly the `frozenset[date_]` that
constructor expects). `payload["events"]` carries the richer per-announcement record (area,
status text, the alert id, effective/expires) for anything that wants more than the bare date
set.

Because there is no per-symbol scope here, `fetch()` still takes a `Symbol` (to satisfy the
`SourceAdapter` protocol) but ignores its `id` — only `sym.market` gates whether this adapter
answers at all (Taiwan-wide, `Market.TW` only).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TypeAlias, cast

import httpx
import structlog

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

# NCDR's CAP Atom/JSON feed, filtered to AlertType=33 (work/school suspension notices).
_BASE_URL = "https://alerts.ncdr.nat.gov.tw/JSONAtomFeed.ashx?AlertType=33"

_SUSPENDED_KEYWORDS = ("停止上班", "停止上課")

# Matches "<area>:<month>/<day><suspension text>" inside an entry's summary HTML, e.g.
# "基隆市:7/10停止上班、停止上課。" — area name, then the affected month/day. Area names in this
# feed are a handful of CJK characters (a Taiwan county/city name, e.g. 基隆市) — bounded at 10
# rather than unbounded `+` so this can't exhibit super-linear backtracking on a pathological
# no-colon input, without changing what any real feed entry matches.
_AREA_DATE_RE = re.compile(r"([一-鿿]{1,10}):(\d{1,2})/(\d{1,2})")

_CAVEAT = (
    "NCDR closure feed lists currently-active work/school-suspension alerts (typhoon/disaster "
    "days) — a live feed of in-force announcements, not a historical archive or a "
    "confirmed-in-advance schedule."
)

# The one cast() target type every raw JSON object-of-unknowns value below uses.
_UntypedDict: TypeAlias = dict[str, object]


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _year_from_effective(effective: object, *, now: datetime) -> int:
    """`effective` is a locale-formatted string like "2026/7/9 下午 08:20:00" — pull
    just the leading year; fall back to the current year if the field is missing/malformed
    rather than failing the whole fetch over one bad entry."""
    if isinstance(effective, str):
        match = re.match(r"(\d{4})/", effective)
        if match:
            return int(match.group(1))
    return now.year


def _parse_entry(entry: dict[str, object], *, now: datetime) -> dict[str, object] | None:
    summary_field = entry.get("summary")
    summary_text = ""
    if isinstance(summary_field, dict):
        summary_text = str(cast("dict[str, object]", summary_field).get("#text", ""))
    elif isinstance(summary_field, str):
        summary_text = summary_field
    summary_text = _strip_html(summary_text)

    if not any(keyword in summary_text for keyword in _SUSPENDED_KEYWORDS):
        return None

    match = _AREA_DATE_RE.search(summary_text)
    if match is None:
        return None
    area, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    year = _year_from_effective(entry.get("effective"), now=now)
    try:
        closure_date = datetime(year, month, day, tzinfo=UTC).date()
    except ValueError:
        return None

    return {
        "date": closure_date.isoformat(),
        "area": area,
        "status": summary_text,
        "alert_id": entry.get("id"),
        "effective": entry.get("effective"),
        "expires": entry.get("expires"),
        # `calendar/events.py::_feed_event_from_row`'s expected shape — a whole-market marker
        # (no `stock_id`), always CONFIRMED (this feed only ever lists alerts already in force,
        # never a future-scheduled one), titled with the affected area since NCDR issues one
        # alert per region rather than one nationwide alert.
        "type": "market_closure",
        "state": "confirmed",
        "event_date": closure_date.isoformat(),
        "title_zh": f"{area} 停班停課",
        "title_en": f"{area} market/work closure",
    }


class NcdrClosureAdapter:
    """The `NCDR_DGPA_CLOSURE_CAP` source adapter — Taiwan-wide closure evidence."""

    source_id = "NCDR_DGPA_CLOSURE_CAP"
    timing_class = TimingClass.EVENT_DRIVEN

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.CALENDAR_EVENTS, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        if "entry" not in raw:
            # An empty-alerts day legitimately omits `entry` entirely — not a contract
            # violation, just nothing currently in force.
            return raw
        entries_field = raw["entry"]
        if isinstance(entries_field, dict):
            return raw  # a single active alert serializes as one object, not a 1-item list
        if not isinstance(entries_field, list):
            raise ValueError("NCDR closure feed 'entry' is neither an object nor a list")
        untyped_entries = cast("list[object]", entries_field)
        for row in untyped_entries:
            if not isinstance(row, dict):
                raise ValueError("NCDR closure feed entry is not an object")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if cap is not Capability.CALENDAR_EVENTS or sym.market is not Market.TW:
            return _unavailable(cap, sym)

        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(_BASE_URL)
                response.raise_for_status()
                body: object = response.json()
        except httpx.HTTPError as exc:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )

        if not isinstance(body, dict):
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE,
                    detail="NCDR closure feed response is not an object",
                ),
            )

        try:
            validated = self.validate(cast("dict[str, object]", body))
        except ValueError as exc:
            return _result(
                payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
            )

        entries_field = validated.get("entry", [])
        if isinstance(entries_field, dict):
            raw_entries: list[dict[str, object]] = [cast("dict[str, object]", entries_field)]
        else:
            raw_entries = cast("list[dict[str, object]]", entries_field)

        now = datetime.now(UTC)
        events: list[dict[str, object]] = []
        closure_dates: set[str] = set()
        for entry in raw_entries:
            parsed = _parse_entry(entry, now=now)
            if parsed is None:
                continue
            events.append(parsed)
            closure_dates.add(cast("str", parsed["date"]))

        status_out = HealthStatus.OK if events else HealthStatus.STALE
        detail = "ok" if events else "no active suspension alerts in the current feed"
        return _result(
            payload={"closure_dates": sorted(closure_dates), "events": events},
            health=SourceHealth(status=status_out, detail=detail),
        )


def _unavailable(cap: Capability, sym: Symbol) -> SourceResult:
    return _result(
        payload={},
        health=SourceHealth(
            status=HealthStatus.UNAVAILABLE_TIER,
            detail=f"NCDR_DGPA_CLOSURE_CAP does not serve {cap.value} for {sym.market.value}",
        ),
    )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=NcdrClosureAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["NcdrClosureAdapter"]
