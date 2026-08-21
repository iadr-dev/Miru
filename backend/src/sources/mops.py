"""MOPS (公開資訊觀測站) disclosure-stream adapter — material announcements,
director/supervisor shareholding, and basic company-profile identity fields, no auth, no query
parameters.

The legacy MOPS HTML-form scraper this adapter was long assumed to require
(`mopsov.twse.com.tw/mops/web/<report_id>`, ROC-date/Big5 HTML parsing) is dead — the upstream
retired that endpoint when it migrated MOPS to a single-page application, and it now serves a
"page can not be accessed" stub. The same disclosure data is published as real JSON through
TWSE's own open-data catalog instead, on the same base domain as the EOD-snapshot adapter in
this package. Three datasets are covered here:

- `t187ap04_L` — 重大訊息 (material announcements). Whole-market current dump, one row per
  disclosure. `timing_class=EVENT_DRIVEN` — this is the catalyst stream: board resolutions,
  buybacks, insider changes, name changes, and similar ad-hoc filings, as they are disclosed.
- `t187ap11_L` — 董監事持股餘額 (director/supervisor shareholding). Whole-market current dump,
  one row per person per company for the latest reported month. `timing_class=REFERENCE` — a
  slow-moving monthly filing, not an event feed.
- `t187ap03_L` — 基本資料 (basic company-profile identity fields: chairman/capital/spokesperson/
  auditor/founding date/headquarters/website/industry code). Whole-market current dump, one row
  per company. `timing_class=REFERENCE` — this is exactly the "MOPS-only identity fields" set
  `tools/market_tools.py::_MOPS_ONLY_MISSING_FIELDS` previously always omitted for lack of any
  adapter; `Capability.PROFILE` (declared in `schemas/enums.py` but unwired until now) is this
  dataset's capability.

All three dumps are the *whole market* in one response, matching this source family's shape
elsewhere in this package: `fetch()` still serves one symbol per call — it makes exactly one
upstream GET and filters to the requested company code client-side. The two disclosure streams
are naturally one-to-many per company (unlike the single-bar EOD case); the profile dataset is
one-to-one per company, so `fetch()` returns at most one row for it.

Known raw-JSON quirk confirmed against a live response: some Chinese field keys carry a
trailing space (`"主旨 "`, `"選任時持股 "` at the time of writing) while otherwise-identical rows
have been observed without it — `_field()` below tries both the bare and space-suffixed key so a
future upstream cleanup does not silently break this adapter.
"""

from __future__ import annotations

import html
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
_COMPANY_PROFILE = f"{_BASE_URL}/opendata/t187ap03_L"

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
_PROFILE_CAVEAT = (
    "MOPS company-profile (基本資料) dump is the current whole-market snapshot as published "
    "today — identity fields (chairman/capital/spokesperson/...) change infrequently but this "
    "adapter carries no history, only whatever MOPS reports as of the dump's own 出表日期."
)


def _field(row: dict[str, object], name: str) -> object:
    """Read a Chinese-key field, tolerating a trailing-space variant of the same key —
    a real quirk observed in this upstream's raw JSON, not a defensive guess."""
    if name in row:
        return row[name]
    spaced = f"{name} "
    return row.get(spaced)


def _text(value: object) -> str | None:
    """Free-text fields (人名/公司名/地址/主旨...) in this upstream's raw JSON sometimes carry a
    literal, undecoded HTML numeric character reference for a CJK character (confirmed live:
    `t187ap11_L` served `"姓名":"&#28201;順德"` for what should read 溫順德) — `html.unescape` is a
    no-op on already-plain text, so this is safe to apply unconditionally rather than only when a
    `&#` pattern happens to be spotted."""
    if value is None:
        return None
    return html.unescape(str(value))


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


_GREGORIAN_YYYYMMDD_LEN = 8


def _gregorian_yyyymmdd_to_iso(raw: str) -> str | None:
    """8-digit plain-Gregorian date string (e.g. "19870221") -> ISO "YYYY-MM-DD"
    ("1987-02-21"). `t187ap03_L`'s own two date fields (成立日期/上市日期) use this format —
    unlike every other MOPS dataset in this module, which uses ROC-offset dates — confirmed
    against a live response (TSMC's 成立日期 is "19870221", a plain Gregorian year, not
    1987+1911)."""
    digits = raw.strip()
    if len(digits) != _GREGORIAN_YYYYMMDD_LEN or not digits.isdigit():
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8])).isoformat()
    except ValueError:
        return None


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
            (Capability.PROFILE, Market.TW),
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
        if cap is Capability.PROFILE:
            return await self._fetch_company_profile(sym, tls_policy=tls_policy)
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
                    "company_name": _text(row.get("公司名稱")),
                    "announced_date": announced_date.isoformat() if announced_date else None,
                    "announced_time": _fmt_time(announced_time_raw) if announced_time_raw else None,
                    "subject": _text(_field(row, "主旨")),
                    "detail": _text(row.get("說明")),
                    "qualifying_clause": _text(row.get("符合條款")),
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
                    "person_name": _text(row.get("姓名")),
                    "title": _text(row.get("職稱")),
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

    async def _fetch_company_profile(self, sym: Symbol, *, tls_policy: TLSPolicy) -> SourceResult:
        rows, error = await self._fetch_rows(_COMPANY_PROFILE, tls_policy=tls_policy)
        if error is not None:
            return _result(
                payload={}, health=error, timing_class=TimingClass.REFERENCE, caveat=_PROFILE_CAVEAT
            )

        matches = _rows_matching(rows, key="公司代號", stock_id=sym.id)
        if not matches:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"company {sym.id} not found in the current company-profile dump",
                ),
                timing_class=TimingClass.REFERENCE,
                caveat=_PROFILE_CAVEAT,
            )

        row = matches[0]  # one-to-one per company — the profile dataset has no history rows
        profile = {
            "symbol": sym.id,
            "market": sym.market.value,
            "company_name": _text(row.get("公司名稱")),
            "industry_classification_code": row.get("產業別"),
            "founding_date": _gregorian_yyyymmdd_to_iso(str(row.get("成立日期", ""))),
            "listed_date": _gregorian_yyyymmdd_to_iso(str(row.get("上市日期", ""))),
            "chairman": _text(row.get("董事長")),
            "general_manager": _text(row.get("總經理")),
            "spokesperson": _text(row.get("發言人")),
            "spokesperson_title": _text(row.get("發言人職稱")),
            "headquarters": _text(row.get("住址")),
            "phone": row.get("總機電話"),
            "website": row.get("網址"),
            "capital": _to_int(row.get("實收資本額")),
            "auditor": _text(row.get("簽證會計師事務所")),
        }

        return _result(
            payload={"profile": profile},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
            timing_class=TimingClass.REFERENCE,
            caveat=_PROFILE_CAVEAT,
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
