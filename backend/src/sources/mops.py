"""MOPS (公開資訊觀測站) disclosure-stream adapter — material announcements and
director/supervisor shareholding, no auth, no query parameters.

The legacy MOPS HTML-form scraper this adapter was long assumed to require
(`mopsov.twse.com.tw/mops/web/<report_id>`, ROC-date/Big5 HTML parsing) is dead — the upstream
retired that endpoint when it migrated MOPS to a single-page application, and it now serves a
"page can not be accessed" stub. The same disclosure data is published as real JSON through
TWSE's own open-data catalog instead, on the same base domain as the EOD-snapshot adapter in
this package. Two datasets are covered here:

- `t187ap04_L` — 重大訊息 (material announcements). Whole-market current dump, one row per
  disclosure. `timing_class=EVENT_DRIVEN` — this is the catalyst stream: board resolutions,
  buybacks, insider changes, name changes, and similar ad-hoc filings, as they are disclosed.
- `t187ap11_L` — 董監事持股餘額 (director/supervisor shareholding). Whole-market current dump,
  one row per person per company for the latest reported month. `timing_class=REFERENCE` — a
  slow-moving monthly filing, not an event feed.

Both dumps are the *whole market* in one response, matching this source family's shape
elsewhere in this package: `fetch()` still serves one symbol per call — it makes exactly one
upstream GET and filters to the requested company code client-side, returning **every** matching
row (both datasets are naturally one-to-many per company, unlike the single-bar EOD case).

Known raw-JSON quirk confirmed against a live response: some Chinese field keys carry a
trailing space (`"主旨 "`, `"選任時持股 "` at the time of writing) while otherwise-identical rows
have been observed without it — `_field()` below tries both the bare and space-suffixed key so a
future upstream cleanup does not silently break this adapter.

Company-profile data (chairman/spokesman/capital, `t187ap03_L`) is a distinct, larger surface
deliberately out of scope here — this adapter covers only the two disclosure-stream endpoints.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

import httpx
import structlog

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_BASE_URL = "https://openapi.twse.com.tw/v1"
_MATERIAL_ANNOUNCEMENTS = f"{_BASE_URL}/opendata/t187ap04_L"
_DIRECTOR_SHAREHOLDING = f"{_BASE_URL}/opendata/t187ap11_L"

_MIN_ROC_DATE_LEN = 5
_MIN_ROC_YEARMONTH_LEN = 3

_ANNOUNCEMENTS_CAVEAT = (
    "MOPS material-announcement (重大訊息) dump is the current whole-market disclosure feed as "
    "published today — it is not a historical archive; announcements roll off the upstream "
    "dataset over time, so absence of a past disclosure here is not evidence it never happened."
)
_SHAREHOLDING_CAVEAT = (
    "MOPS director/supervisor shareholding (董監事持股) dump reflects the single most recently "
    "reported month only — a slow-moving monthly regulatory filing, not a live shareholding "
    "figure, and carries no history beyond that one snapshot."
)


def _field(row: dict[str, object], name: str) -> object:
    """Read a Chinese-key field, tolerating a trailing-space variant of the same key —
    a real quirk observed in this upstream's raw JSON, not a defensive guess."""
    if name in row:
        return row[name]
    spaced = f"{name} "
    return row.get(spaced)


def _roc_to_iso_date(roc: str) -> date | None:
    """7-digit ROC date string (e.g. "1150625") -> Gregorian `date` (2026-06-25)."""
    digits = roc.strip()
    if len(digits) < _MIN_ROC_DATE_LEN or not digits.isdigit():
        return None
    year = int(digits[:-4]) + 1911
    month = digits[-4:-2]
    day = digits[-2:]
    try:
        return date(year, int(month), int(day))
    except ValueError:
        return None


def _roc_yearmonth_to_iso(roc: str) -> str | None:
    """5-digit ROC year-month string (e.g. "11506") -> ISO "YYYY-MM" (e.g. "2026-06")."""
    digits = roc.strip()
    if len(digits) < _MIN_ROC_YEARMONTH_LEN or not digits.isdigit():
        return None
    year = int(digits[:-2]) + 1911
    month = digits[-2:]
    return f"{year:04d}-{month}"


def _fmt_time(raw: str) -> str:
    """Zero-padded digit string (e.g. "55412") -> "HH:MM:SS" (e.g. "05:54:12")."""
    digits = raw.strip().zfill(6)
    return f"{digits[0:2]}:{digits[2:4]}:{digits[4:6]}"


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _rows_matching(
    rows: list[dict[str, object]], *, key: str, stock_id: str
) -> list[dict[str, object]]:
    return [row for row in rows if str(row.get(key, "")) == stock_id]


class MopsAdapter:
    """The `MOPS` source adapter — real-time disclosure JSON, not the dead HTML scraper."""

    source_id = "MOPS"
    timing_class = TimingClass.EVENT_DRIVEN  # the priority capability's class; see per-result

    def supports(self) -> set[tuple[Capability, Market]]:
        return {
            (Capability.CORPORATE_ACTIONS, Market.TW),
            (Capability.SHAREHOLDING, Market.TW),
        }

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        if "rows" not in raw:
            raise ValueError("MOPS OpenAPI response missing 'rows'")
        rows_field = raw["rows"]
        if not isinstance(rows_field, list):
            raise ValueError("MOPS OpenAPI 'rows' is not a list")
        untyped_rows = cast("list[object]", rows_field)
        for row in untyped_rows:
            if not isinstance(row, dict):
                raise ValueError("MOPS OpenAPI row is not an object")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if sym.market is not Market.TW:
            return _unavailable(cap, sym)
        if cap is Capability.CORPORATE_ACTIONS:
            return await self._fetch_material_announcements(sym, tls_policy=tls_policy)
        if cap is Capability.SHAREHOLDING:
            return await self._fetch_director_shareholding(sym, tls_policy=tls_policy)
        return _unavailable(cap, sym)

    async def _fetch_material_announcements(
        self, sym: Symbol, *, tls_policy: TLSPolicy
    ) -> SourceResult:
        rows, error = await self._fetch_rows(_MATERIAL_ANNOUNCEMENTS, tls_policy=tls_policy)
        if error is not None:
            return _result(
                payload={},
                health=error,
                timing_class=TimingClass.EVENT_DRIVEN,
                caveat=_ANNOUNCEMENTS_CAVEAT,
            )

        matches = _rows_matching(rows, key="公司代號", stock_id=sym.id)
        if not matches:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"company {sym.id} has no material announcement in today's dump",
                ),
                timing_class=TimingClass.EVENT_DRIVEN,
                caveat=_ANNOUNCEMENTS_CAVEAT,
            )

        announcements: list[dict[str, object]] = []
        for row in matches:
            announced_date_raw = str(row.get("發言日期", ""))
            announced_date = _roc_to_iso_date(announced_date_raw)
            announced_time_raw = str(row.get("發言時間", ""))
            announcements.append(
                {
                    "symbol": sym.id,
                    "market": sym.market.value,
                    "company_name": row.get("公司名稱"),
                    "announced_date": announced_date.isoformat() if announced_date else None,
                    "announced_time": _fmt_time(announced_time_raw) if announced_time_raw else None,
                    "subject": _field(row, "主旨"),
                    "detail": row.get("說明"),
                    "qualifying_clause": row.get("符合條款"),
                }
            )

        return _result(
            payload={"announcements": announcements},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
            timing_class=TimingClass.EVENT_DRIVEN,
            caveat=_ANNOUNCEMENTS_CAVEAT,
        )

    async def _fetch_director_shareholding(
        self, sym: Symbol, *, tls_policy: TLSPolicy
    ) -> SourceResult:
        rows, error = await self._fetch_rows(_DIRECTOR_SHAREHOLDING, tls_policy=tls_policy)
        if error is not None:
            return _result(
                payload={},
                health=error,
                timing_class=TimingClass.REFERENCE,
                caveat=_SHAREHOLDING_CAVEAT,
            )

        matches = _rows_matching(rows, key="公司代號", stock_id=sym.id)
        if not matches:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"company {sym.id} not found in the current shareholding dump",
                ),
                timing_class=TimingClass.REFERENCE,
                caveat=_SHAREHOLDING_CAVEAT,
            )

        holdings: list[dict[str, object]] = []
        for row in matches:
            as_of_raw = str(row.get("資料年月", ""))
            holdings.append(
                {
                    "symbol": sym.id,
                    "market": sym.market.value,
                    "as_of": _roc_yearmonth_to_iso(as_of_raw),
                    "person_name": row.get("姓名"),
                    "title": row.get("職稱"),
                    "shares_at_election": _to_int(_field(row, "選任時持股")),
                    "current_shares": _to_int(row.get("目前持股")),
                    "pledged_shares": _to_int(row.get("設質股數")),
                    "pledged_ratio": row.get("設質股數佔持股比例"),
                }
            )

        return _result(
            payload={"holdings": holdings},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
            timing_class=TimingClass.REFERENCE,
            caveat=_SHAREHOLDING_CAVEAT,
        )

    async def _fetch_rows(
        self, url: str, *, tls_policy: TLSPolicy
    ) -> tuple[list[dict[str, object]], SourceHealth | None]:
        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                response = await client.get(url)
                response.raise_for_status()
                body: object = response.json()
        except httpx.HTTPError as exc:
            return [], SourceHealth(
                status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
            )

        try:
            validated = self.validate({"rows": body})
        except ValueError as exc:
            return [], SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))

        rows_field = validated["rows"]
        if not isinstance(rows_field, list):  # pragma: no cover — validate() guarantees this
            raise TypeError("validate() contract violated: 'rows' is not a list")
        return cast("list[dict[str, object]]", rows_field), None


def _unavailable(cap: Capability, sym: Symbol) -> SourceResult:
    return _result(
        payload={},
        health=SourceHealth(
            status=HealthStatus.UNAVAILABLE_TIER,
            detail=f"MOPS does not serve {cap.value} for {sym.market.value}",
        ),
        timing_class=MopsAdapter.timing_class,
        caveat=_ANNOUNCEMENTS_CAVEAT,
    )


def _result(
    *,
    payload: dict[str, object],
    health: SourceHealth,
    timing_class: TimingClass,
    caveat: str,
) -> SourceResult:
    return SourceResult(
        source_id=MopsAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=timing_class,
        fetched_at=datetime.now(UTC),
        currentness_caveat=caveat,
        payload=payload,
        health=health,
    )


__all__ = ["MopsAdapter"]
