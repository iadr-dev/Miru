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
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
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
# t187ap12_L — 內部人股權轉讓事前申報 (insider advance-transfer-plan declaration). A related
# filing, t187ap13_L ("reason not transferred"), is a distinct dataset intentionally not covered
# here — see `InsiderTransferDeclarationRow`'s own docstring.
_INSIDER_TRANSFER_DECLARATION = f"{_BASE_URL}/opendata/t187ap12_L"

# 自結損益公告 (t138sb01) — a DIFFERENT MOPS host (`mopsov.twse.com.tw`, the legacy HTML-form SPA
# this module's own header docstring says is dead for `t187ap0x_L`-style reports) than every
# other endpoint in this module (`openapi.twse.com.tw`'s JSON open-data catalog). Confirmed live
# (2026-09-05): t138sb01 was never migrated to the open-data catalog — it is still the classic
# ASP-style POST-and-parse-HTML form, one whole-market table per (market, period) POST, no
# per-row announcement date. No date-range/whole-history query exists upstream, so this is
# fetched bounded per-symbol (filtered client-side from that one period's whole-market table),
# never a whole-market ingest — see `fetch_self_assessed_earnings`'s own docstring.
_SELF_ASSESSED_EARNINGS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t138sb01"
_SELF_ASSESSED_MARKET_CODE = {
    "twse": "1",  # 上市
    "tpex": "2",  # 上櫃
}

_MIN_ROC_DATE_LEN = 5
_MIN_ROC_YEARMONTH_LEN = 3
_TRANSFER_WINDOW_PARTS = 2  # "有效轉讓期間" is exactly "YYYMMDD~YYYMMDD", one `~`

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
_INSIDER_TRANSFER_DECLARATION_CAVEAT = (
    "MOPS insider advance-transfer-plan declaration (內部人股權轉讓事前申報) dump is the current "
    "whole-market filing feed as published today — it is not a historical archive; declarations "
    "roll off the upstream dataset over time, so absence of a past filing here is not evidence "
    "it never happened. A declared intent to transfer, not a confirmation the transfer occurred."
)
_SELF_ASSESSED_EARNINGS_CAVEAT = (
    "MOPS 自結損益公告 (t138sb01) is a company's own preliminary/unaudited P&L figure for the "
    "stated period, filed voluntarily and ahead of the audited financial statement — not a "
    "confirmed final result, and a company that has not filed for a period simply has no row "
    "here (absence is not evidence of zero profit/loss). Fetched bounded per-symbol for the "
    "board's own roster only, filtered from that period's whole-market table — never a "
    "whole-market ingest (the upstream form has no whole-history date-range query)."
)

# TWSE's own 重大訊息 disclosure-rule clause citation (符合條款), bucketed into a small set of
# human labels for a whole-market list view — never exposed as a raw clause number to a reader.
# Live-verified against today's dump: 第12款(24)/第20款(22)/第36款+第35款(13)/第11款(3) are the
# only clauses with more than a couple of rows; everything else buckets to 其他.
_CATEGORY_BY_CLAUSE: dict[str, str] = {
    "第12款": "法說會",
    "第20款": "資產處分/併購",
    "第36款": "庫藏股/減資",
    "第35款": "庫藏股/減資",
    "第11款": "財務/營運重大變動",
}
_DEFAULT_CATEGORY = "其他"


def _announcement_category(qualifying_clause: object) -> str:
    """`qualifying_clause` -> a human-readable category bucket — never raises, never exposes the
    raw clause number un-bucketed (see `_CATEGORY_BY_CLAUSE`'s own module comment)."""
    if not isinstance(qualifying_clause, str):
        return _DEFAULT_CATEGORY
    for clause, category in _CATEGORY_BY_CLAUSE.items():
        if clause in qualifying_clause:
            return category
    return _DEFAULT_CATEGORY


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


_ISO_YEARMONTH_PARTS = 2


def _iso_yearmonth_to_roc(period: str) -> str | None:
    """ISO "YYYY-MM" (e.g. "2025-07") -> t138sb01's own 5-digit ROC year-month POST param
    (e.g. "11407"). `None` for anything that doesn't parse as exactly year-dash-month."""
    parts = period.split("-")
    if len(parts) != _ISO_YEARMONTH_PARTS:
        return None
    year_str, month_str = parts
    if not (year_str.isdigit() and month_str.isdigit()):
        return None
    roc_year = int(year_str) - 1911
    if roc_year <= 0:
        return None
    return f"{roc_year}{int(month_str):02d}"


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


def _map_announcement_row(
    row: dict[str, object], *, symbol: str, market: Market
) -> dict[str, object]:
    """One raw `t187ap04_L` row -> the mapped dict both the per-symbol fetch path
    (`MopsAdapter._fetch_material_announcements`) and the whole-market ingest path
    (`fetch_whole_market_material_announcements`) share — kept as one function so the two never
    drift in field mapping."""
    announced_date_raw = str(row.get("發言日期", ""))
    announced_date = _roc_to_iso_date(announced_date_raw)
    announced_time_raw = str(row.get("發言時間", ""))
    qualifying_clause = _text(row.get("符合條款"))
    return {
        "symbol": symbol,
        "market": market.value,
        "company_name": _text(row.get("公司名稱")),
        "announced_date": announced_date.isoformat() if announced_date else None,
        "announced_time": _fmt_time(announced_time_raw) if announced_time_raw else None,
        "subject": _text(_field(row, "主旨")),
        "detail": _text(row.get("說明")),
        "qualifying_clause": qualifying_clause,
    }


def _split_roc_date_range(raw: object) -> tuple[date | None, date | None]:
    """`有效轉讓期間`'s raw value is `"YYYMMDD~YYYMMDD"` (two ROC dates joined by `~`) — split and
    parse each half independently; either half missing/malformed yields `None` for that half
    rather than raising, same defensive posture every other parser in this module takes."""
    text = str(raw) if raw is not None else ""
    parts = text.split("~")
    if len(parts) != _TRANSFER_WINDOW_PARTS:
        return None, None
    return _roc_to_iso_date(parts[0]), _roc_to_iso_date(parts[1])


def _map_insider_transfer_declaration_row(row: dict[str, object]) -> dict[str, object] | None:
    """One raw `t187ap12_L` row -> the mapped dict for `InsiderTransferDeclarationRow` — `None`
    when the row is missing its symbol or a valid transfer window (both required fields on that
    model), so a malformed upstream row is dropped here rather than reaching
    `_validate_rows_against` with a guaranteed validation failure."""
    symbol = row.get("公司代號")
    if not isinstance(symbol, str) or not symbol:
        return None
    window_start, window_end = _split_roc_date_range(row.get("有效轉讓期間"))
    if window_start is None or window_end is None:
        return None
    transferee = _text(row.get("受讓人"))
    planned_shares_own = _to_int(row.get("預定轉讓總股數-自有持股"))
    planned_shares_trust = _to_int(row.get("預定轉讓總股數-保留運用決定權信託股數"))
    # `預定轉讓方式及股數-轉讓股數` is genuinely blank upstream for a 贈與 (gift, non-market)
    # transfer — confirmed live: the same disclosure carries the real share count in
    # `預定轉讓總股數-自有持股`/`...-信託股數` instead. Fall back to that sum rather than
    # dropping the row (which `_to_int(None)` would otherwise force, since `transfer_shares` is
    # a required field on `InsiderTransferDeclarationRow`).
    transfer_shares = _to_int(_field(row, "預定轉讓方式及股數-轉讓股數"))
    if (
        transfer_shares is None
        and planned_shares_own is not None
        and planned_shares_trust is not None
    ):
        transfer_shares = planned_shares_own + planned_shares_trust
    return {
        "symbol": symbol,
        "market": Market.TW.value,
        "company_name": _text(row.get("公司名稱")),
        "filer_role": _text(row.get("申報人身分")),
        "person_name": _text(row.get("姓名")),
        "transfer_method": _text(_field(row, "預定轉讓方式及股數-轉讓方式")),
        "transfer_shares": transfer_shares,
        "max_daily_exchange_shares": _to_int(row.get("每日於盤中交易最大得轉讓股數")),
        "transferee": transferee or None,  # blank string (open-market transfer) -> None
        "current_shares_own": _to_int(row.get("目前持有股數-自有持股")),
        "current_shares_trust": _to_int(row.get("目前持有股數-保留運用決定權信託股數")),
        "planned_shares_own": planned_shares_own,
        "planned_shares_trust": planned_shares_trust,
        "projected_shares_own_after": _to_int(row.get("預定轉讓後持股-自有持股")),
        "projected_shares_trust_after": _to_int(row.get("預定轉讓後持股-保留運用決定權信託股數")),
        "transfer_window_start": window_start.isoformat(),
        "transfer_window_end": window_end.isoformat(),
    }


class _SelfAssessedEarningsTableParser(HTMLParser):
    """Parses `ajax_t138sb01`'s response — one or two `<table>` blocks (個別 then 合併, either
    may be absent for a period/market with no filings of that kind), each with a header row
    (公司代號/公司簡稀/.../差異原因說明) followed by data rows. Collects every `<table>`'s rows as
    plain cell-text lists; the caller (`_parse_self_assessed_earnings_html`) locates the header
    row within each table by content, not a fixed position, the same defensive posture
    `sitca.py::_FeeTableParser`'s sibling `_header_index` helper uses."""

    def __init__(self) -> None:
        super().__init__()
        self._in_cell = False
        self._cell_parts: list[str] = []
        self._row: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "table":
            self._current_table = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._in_cell = False
            self._row.append("".join(self._cell_parts).strip())
        elif tag == "tr":
            if self._row and self._current_table is not None:
                self._current_table.append(self._row)
            self._row = []
        elif tag == "table" and self._current_table is not None:
            self.tables.append(self._current_table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)


_SELF_ASSESSED_HEADER_MARKER = "公司代號"
_SELF_ASSESSED_UNAVAILABLE_MARKER = "不適用"  # financial-industry rows: 營業損益 columns N/A
_SELF_ASSESSED_MIN_DATA_COLUMNS = 6  # 公司代號/公司簡稱 + 4 profit columns, 差異原因說明 optional


def _self_assessed_period_from_title(rows: list[list[str]]) -> tuple[str, bool] | None:
    """The `(period, consolidated)` a table's own title row states, e.g.
    "2025年7月\\n（合併）" -> `("2025-07", True)`. `None` if no title row is found or it doesn't
    parse — this is read from the table's OWN stated period (never assumed to equal the caller's
    requested `period`, since 年度 filings could in principle mix periods within one response,
    though live responses observed so far are one period per table)."""
    for row in rows:
        for cell in row:
            match = re.search(r"(\d{4})年(\d{1,2})月", cell)
            if match:
                year, month = match.groups()
                consolidated = "合併" in cell
                return f"{int(year):04d}-{int(month):02d}", consolidated
    return None


def _parse_self_assessed_earnings_html(html_text: str) -> list[dict[str, object]]:
    """The full `ajax_t138sb01` response -> one dict per data row (individual + consolidated
    tables both walked), each carrying `revenue_period`/`consolidated` plus the six
    公司代號/公司簡稱/損益 columns located by header content (never a fixed index — TWSE has
    reflowed this table's column count before, e.g. inserting a repeated header row mid-table on
    a long page, confirmed live in the 2025-07 上市 response).

    The upstream response is a FLAT sequence of `<table>` elements, not one table nesting a
    title — a title-only table ("2025年7月（個別）") is immediately followed by its own separate
    data table (confirmed live: `_SelfAssessedEarningsTableParser` sees them as sibling tables,
    not parent/child). So this walks every table in order, remembering the last title seen and
    applying it to the next data table that has none of its own."""
    parser = _SelfAssessedEarningsTableParser()
    parser.feed(html_text)
    out: list[dict[str, object]] = []
    current_period: tuple[str, bool] | None = None
    for table in parser.tables:
        title = _self_assessed_period_from_title(table)
        header_idx = next(
            (i for i, row in enumerate(table) if _SELF_ASSESSED_HEADER_MARKER in row), None
        )
        if header_idx is None:
            if title is not None:
                current_period = title
            continue
        if current_period is None:
            continue
        period, consolidated = current_period
        for row in table[header_idx + 1 :]:
            if len(row) < _SELF_ASSESSED_MIN_DATA_COLUMNS or _SELF_ASSESSED_HEADER_MARKER in row:
                continue  # a repeated header row mid-table (seen live) is not a data row
            symbol = row[0].strip()
            if not symbol or not symbol[0].isdigit():
                continue
            monthly_op, ytd_op, monthly_pretax, ytd_pretax = (
                None if _SELF_ASSESSED_UNAVAILABLE_MARKER in cell else _to_int(cell)
                for cell in row[2:6]
            )
            out.append(
                {
                    "symbol": symbol,
                    "market": Market.TW.value,
                    "company_name": row[1].strip(),
                    "revenue_period": period,
                    "consolidated": consolidated,
                    "monthly_operating_profit": monthly_op,
                    "ytd_operating_profit": ytd_op,
                    "monthly_pretax_profit": monthly_pretax,
                    "ytd_pretax_profit": ytd_pretax,
                    "variance_note": (
                        row[6].strip()
                        if len(row) > _SELF_ASSESSED_MIN_DATA_COLUMNS and row[6].strip()
                        else None
                    ),
                }
            )
    return out


async def _fetch_self_assessed_earnings_table(
    *, market_code: str, roc_yearmonth: str, tls_policy: TLSPolicy
) -> tuple[list[dict[str, object]], SourceHealth | None]:
    """One POST to `ajax_t138sb01` for one (market, monthly-period) combination, parsed into
    every row across both its 個別/合併 tables. `CK1`=market code, `CK2`="1" (monthly report —
    quarterly `CK2`="3"/`YM3` is not wired here, since the board's own roster only ever needs
    the monthly figure), `YM1`=the ROC year-month. `code1` deliberately left blank — this fetches
    the WHOLE period's table (still bounded to one period, not a whole-history scan) and the
    caller filters to its one requested symbol client-side, the same shape as every other
    whole-market-then-filter adapter path in this module."""
    body = {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "1",
        "off": "1",
        "keyword4": "",
        "code1": "",
        "TYPEK2": "",
        "checkbtn": "",
        "queryName": "",
        "CK1": market_code,
        "CK2": "1",
        "YM1": roc_yearmonth,
        "YM3": "",
    }
    try:
        async with build_async_client(tls_policy=tls_policy) as client:
            response = await client.post(
                _SELF_ASSESSED_EARNINGS_URL,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return [], SourceHealth(
            status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
        )
    return _parse_self_assessed_earnings_html(response.text), None


class MopsAdapter:
    """The `MOPS` source adapter — real-time disclosure JSON, not the dead HTML scraper."""

    source_id = "MOPS"
    timing_class = TimingClass.EVENT_DRIVEN  # the priority capability's class; see per-result

    def supports(self) -> set[tuple[Capability, Market]]:
        return {
            (Capability.CORPORATE_ACTIONS, Market.TW),
            (Capability.SHAREHOLDING, Market.TW),
            (Capability.PROFILE, Market.TW),
            (Capability.SELF_ASSESSED_EARNINGS, Market.TW),
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
        if cap is Capability.SELF_ASSESSED_EARNINGS:
            return await self._dispatch_self_assessed_earnings(
                sym, tls_policy=tls_policy, params=params
            )
        return _unavailable(cap, sym)

    async def _dispatch_self_assessed_earnings(
        self, sym: Symbol, *, tls_policy: TLSPolicy, params: dict[str, object]
    ) -> SourceResult:
        period = params.get("period")
        security_type = params.get("security_type")
        if not isinstance(period, str) or not isinstance(security_type, str):
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail="self-assessed earnings requires 'period' and 'security_type'",
                ),
                timing_class=TimingClass.REFERENCE,
                caveat=_SELF_ASSESSED_EARNINGS_CAVEAT,
            )
        return await self._fetch_self_assessed_earnings(
            sym, tls_policy=tls_policy, period=period, security_type=security_type
        )

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

        announcements = [
            _map_announcement_row(row, symbol=sym.id, market=sym.market) for row in matches
        ]

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

    async def _fetch_self_assessed_earnings(
        self, sym: Symbol, *, tls_policy: TLSPolicy, period: str, security_type: str
    ) -> SourceResult:
        """`period` is ISO "YYYY-MM" (the month this report covers, not today); `security_type`
        is `"twse"`/`"tpex"` (which market-code POST param to use — see
        `_SELF_ASSESSED_MARKET_CODE`). Fetches that ONE period's whole-market table (both 個別
        and 合併 sections) then filters client-side to `sym.id` — same "whole-market call,
        filtered to one symbol" shape every other adapter in this module uses, just over HTML
        instead of JSON because `t138sb01` was never migrated to the open-data catalog (see this
        module's own header + the URL constant's comment)."""
        market_code = _SELF_ASSESSED_MARKET_CODE.get(security_type)
        if market_code is None:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"self-assessed earnings has no market code for '{security_type}'",
                ),
                timing_class=TimingClass.REFERENCE,
                caveat=_SELF_ASSESSED_EARNINGS_CAVEAT,
            )
        roc_yearmonth = _iso_yearmonth_to_roc(period)
        if roc_yearmonth is None:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"self-assessed earnings got an unparseable period '{period}'",
                ),
                timing_class=TimingClass.REFERENCE,
                caveat=_SELF_ASSESSED_EARNINGS_CAVEAT,
            )

        rows, error = await _fetch_self_assessed_earnings_table(
            market_code=market_code, roc_yearmonth=roc_yearmonth, tls_policy=tls_policy
        )
        if error is not None:
            return _result(
                payload={},
                health=error,
                timing_class=TimingClass.REFERENCE,
                caveat=_SELF_ASSESSED_EARNINGS_CAVEAT,
            )

        matches = [row for row in rows if row["symbol"] == sym.id]
        if not matches:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"company {sym.id} has no self-assessed earnings filing for {period}",
                ),
                timing_class=TimingClass.REFERENCE,
                caveat=_SELF_ASSESSED_EARNINGS_CAVEAT,
            )

        return _result(
            payload={"rows": matches},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
            timing_class=TimingClass.REFERENCE,
            caveat=_SELF_ASSESSED_EARNINGS_CAVEAT,
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


async def _fetch_whole_market_rows(
    url: str, *, tls_policy: TLSPolicy
) -> tuple[list[dict[str, object]], SourceHealth | None]:
    """Module-level twin of `MopsAdapter._fetch_rows` — same one-GET-plus-shape-check logic, but
    callable by the whole-market ingest functions below without constructing an adapter
    instance (they're ingest-only, never router-dispatched, so there's no `MopsAdapter()` to
    reuse in that call path)."""
    try:
        async with build_async_client(tls_policy=tls_policy) as client:
            response = await client.get(url)
            response.raise_for_status()
            body: object = response.json()
    except httpx.HTTPError as exc:
        return [], SourceHealth(
            status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
        )

    if not isinstance(body, list):
        return [], SourceHealth(
            status=HealthStatus.UNREACHABLE, detail="MOPS OpenAPI response is not a list"
        )
    untyped_rows = cast("list[object]", body)
    for row in untyped_rows:
        if not isinstance(row, dict):
            return [], SourceHealth(
                status=HealthStatus.UNREACHABLE, detail="MOPS OpenAPI row is not an object"
            )
    return cast("list[dict[str, object]]", untyped_rows), None


async def fetch_whole_market_material_announcements(*, tls_policy: TLSPolicy) -> SourceResult:
    """One whole-market call, every 重大訊息 disclosure row in today's dump — the ingest layer's
    sole legitimate caller (mirrors `fetch_whole_market_etf_nav`'s own governance framing: never
    a per-symbol fan-out). Unlike `MopsAdapter._fetch_material_announcements`, this does NOT
    filter to one symbol — every row in the dump is mapped, plus a derived `category` bucket
    (`_announcement_category`) `MaterialAnnouncementRow` carries that the per-symbol
    `MaterialAnnouncement` model does not."""
    rows, error = await _fetch_whole_market_rows(_MATERIAL_ANNOUNCEMENTS, tls_policy=tls_policy)
    if error is not None:
        return _result(
            payload={"rows": []},
            health=error,
            timing_class=TimingClass.EVENT_DRIVEN,
            caveat=_ANNOUNCEMENTS_CAVEAT,
        )
    mapped_rows: list[dict[str, object]] = []
    for row in rows:
        symbol = row.get("公司代號")
        if not isinstance(symbol, str) or not symbol:
            continue
        mapped = _map_announcement_row(row, symbol=symbol, market=Market.TW)
        mapped["category"] = _announcement_category(row.get("符合條款"))
        mapped_rows.append(mapped)
    return _result(
        payload={"rows": mapped_rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat=_ANNOUNCEMENTS_CAVEAT,
    )


async def fetch_whole_market_insider_transfer_declarations(
    *, tls_policy: TLSPolicy
) -> SourceResult:
    """One whole-market call, every 內部人股權轉讓事前申報 (`t187ap12_L`) row in today's dump —
    same ingest-layer whole-market shape as `fetch_whole_market_material_announcements`. Scoped
    to `t187ap12_L` only; see `InsiderTransferDeclarationRow`'s own docstring for why
    `t187ap13_L` is out of scope."""
    rows, error = await _fetch_whole_market_rows(
        _INSIDER_TRANSFER_DECLARATION, tls_policy=tls_policy
    )
    if error is not None:
        return _result(
            payload={"rows": []},
            health=error,
            timing_class=TimingClass.EVENT_DRIVEN,
            caveat=_INSIDER_TRANSFER_DECLARATION_CAVEAT,
        )
    mapped_rows = [
        mapped for row in rows if (mapped := _map_insider_transfer_declaration_row(row)) is not None
    ]
    return _result(
        payload={"rows": mapped_rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        timing_class=TimingClass.EVENT_DRIVEN,
        caveat=_INSIDER_TRANSFER_DECLARATION_CAVEAT,
    )


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


__all__ = [
    "MopsAdapter",
    "fetch_whole_market_insider_transfer_declarations",
    "fetch_whole_market_material_announcements",
]
