"""SITCA (中華民國證券投資信託暨顧問商業同業公會) fee-disclosure adapter — ETF/fund internal-fee
(內扣費用) reference data, the one field the TWSE/TPEx/TDCC/FinMind sources do not carry.

Source: the association's public monthly report `IN2105.aspx` (基金基本資料表) in its
`費率、銀行與規定` column mode (`ddlQ_Column=2`). It is a classic ASP.NET postback form, served with
no auth: this adapter GETs the page once to read the `__VIEWSTATE` / `__VIEWSTATEGENERATOR` /
`__EVENTVALIDATION` hidden fields, then POSTs them back with the most-recent month and fee-column
mode selected, and parses the returned per-fund table. Whole table in one response; `fetch()`
still serves one symbol per call and filters to the requested fund client-side (same shape as
this package's other whole-market-dump-then-filter adapters).

**No ticker column exists in this report at all** (confirmed live against the actual header row:
`類型代號, 基金統編, 基金名稱, ...` — 類型代號 is a type/code, e.g. `AH13`, not a TWSE ticker).
SITCA identifies every fund only by 基金統編 (its tax ID) and 基金名稱. `fetch()` therefore
resolves the requested ticker to its tax ID via `sources/etf_pcf.py::fetch_ticker_tax_id_map`
(TWSE's own `t187ap47_L` disclosure carries both a ticker and a tax ID per fund — the one place
in this codebase the two are linked) and matches SITCA rows by that tax ID, never by name — an
exact-ID join, not a fuzzy one, so a share-class variant or issuer-prefix collision in fund names
never causes a wrong-fund match.

What it yields per fund: 經理費 (management fee %), 保管費 (custody fee %), 保管機構 (custodian
bank). A true total-expense-ratio (TER) is NOT a disclosed column — it is approximated as
經理費 + 保管費 in the tool layer, floored (excludes other operating costs), and caveated there.
NAV/IOPV is not here (nor anywhere aggregated) — that gap is unchanged by this adapter.

**TLS:** SITCA's own certificate chain is missing a Subject Key Identifier, so every fetch here
runs under `TLSPolicy.UNSAFE_EXPLICIT` unconditionally (`_fetch_fee_table_rows` ignores the
caller's policy) — see that function's own docstring. Scoped to this one source only.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html.parser import HTMLParser

import httpx
import structlog

from schemas.capabilities import EtfExpenseInfo
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.etf_pcf import fetch_ticker_tax_id_map
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_FEE_REPORT_URL = "https://www.sitca.org.tw/ROC/Industry/IN2105.aspx?pid=IN2212_02"
_COLUMN_MODE_FEE = "2"  # ddlQ_Column option: 費率、銀行與規定
_YEARMONTH_FIELD = "ctl00$ContentPlaceHolder1$ddlQ_YYYYMM"
_COLUMN_FIELD = "ctl00$ContentPlaceHolder1$ddlQ_Column"
_QUERY_BUTTON_FIELD = "ctl00$ContentPlaceHolder1$BtnQuery"
_HIDDEN_FIELDS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")

# SITCA publishes a month's report with a lag of a month or more, so the dropdown's newest option
# trails the calendar. A requested month beyond it is "not published yet", not a layout change.
MONTH_NOT_PUBLISHED_DETAIL = "requested month not yet published by SITCA"
MONTH_NOT_PUBLISHED_KEY = "month_not_published"

_CAVEAT = (
    "SITCA (投信投顧公會) 月頻費率揭露 — 經理費/保管費為公告值，非即時；內扣總費用率(TER)於工具層"
    "近似為 經理費+保管費，未含其他營運費用，為下限估計而非經會計師查核之總費用率。"
)


def _percent_to_float(value: str) -> float | None:
    """`"1.50%"` -> `1.5`. `None` for blanks or non-numeric cells (never coerced to 0)."""
    text = value.replace("%", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class _HiddenFieldParser(HTMLParser):
    """Extracts the ASP.NET hidden postback fields from the initial GET so they can be replayed."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attr = {k: (v or "") for k, v in attrs}
        name = attr.get("name", "")
        if name in _HIDDEN_FIELDS:
            self.fields[name] = attr.get("value", "")


class _FeeTableParser(HTMLParser):
    """Parses the fee-mode result table into per-fund rows keyed by fund code. Leading columns are
    fund-code / short-name / ... / management-fee / custody-fee / ... / custodian; rather than
    depend on a fragile fixed column index across upstream layout tweaks, this collects each row's
    cells and the caller locates the fund code + the fee/custodian cells by pattern."""

    def __init__(self) -> None:
        super().__init__()
        self._in_cell = False
        self._row: list[str] = []
        self._cell_parts: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("td", "th"):
            self._in_cell = True
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._in_cell = False
            self._row.append("".join(self._cell_parts).strip())
        elif tag == "tr":
            if self._row:
                self.rows.append(self._row)
            self._row = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)


_MANAGEMENT_FEE_HEADER = "基金管理費率"
_CUSTODY_FEE_HEADER = "基金保管費率"
_CUSTODIAN_HEADER = "保管銀行"
_TAX_ID_HEADER = "基金統編"


def _header_index(rows: list[list[str]], contains: str) -> int | None:
    """The column index whose header cell contains `contains`. The header is the row that carries
    all three fee/custodian labels — located by content, not a fixed row position, since ad rows
    or a title row may precede it. `None` if no such header row is found."""
    for row in rows:
        if _MANAGEMENT_FEE_HEADER in row and _CUSTODY_FEE_HEADER in row:
            for idx, cell in enumerate(row):
                if contains in cell:
                    return idx
            return None
    return None


def _cell(row: list[str], idx: int | None) -> str | None:
    return row[idx] if idx is not None and idx < len(row) else None


def _fee_row_for_fund(
    row: list[str], indices: tuple[int, int, int], *, ticker: str
) -> EtfExpenseInfo:
    """Build one fund's `EtfExpenseInfo` from an already-located data `row`, its
    `(mgmt_idx, custody_idx, custodian_idx)` header-column indices, and the TWSE `ticker` this
    row's 基金統編 was already resolved to by the caller — the shared per-row extraction both
    `_extract_fee_row` (per-symbol) and `_extract_all_fee_rows` (whole-market) call, so the two
    never drift on the fee-math/caveat convention or on using a real ticker as `symbol` (SITCA's
    own row has no ticker column at all — see module docstring)."""
    mgmt_idx, custody_idx, custodian_idx = indices
    management_fee = _percent_to_float(_cell(row, mgmt_idx) or "")
    custody_fee = _percent_to_float(_cell(row, custody_idx) or "")
    custodian_raw = _cell(row, custodian_idx)
    custodian = custodian_raw.strip() if custodian_raw and custodian_raw.strip() else None
    ter = (
        management_fee + custody_fee
        if management_fee is not None and custody_fee is not None
        else None
    )
    return EtfExpenseInfo(
        symbol=ticker,
        market=Market.TW,
        management_fee_pct=management_fee,
        custody_fee_pct=custody_fee,
        total_expense_ratio_pct_approx=ter,
        custodian_bank=custodian,
    )


def _fee_header_indices(rows: list[list[str]]) -> tuple[int, int, int, int] | None:
    mgmt_idx = _header_index(rows, _MANAGEMENT_FEE_HEADER)
    custody_idx = _header_index(rows, _CUSTODY_FEE_HEADER)
    custodian_idx = _header_index(rows, _CUSTODIAN_HEADER)
    tax_id_idx = _header_index(rows, _TAX_ID_HEADER)
    if mgmt_idx is None or custody_idx is None or custodian_idx is None or tax_id_idx is None:
        return None
    return mgmt_idx, custody_idx, custodian_idx, tax_id_idx


def _extract_fee_row(rows: list[list[str]], tax_id: str, *, ticker: str) -> EtfExpenseInfo | None:
    """Find the data row whose 基金統編 cell is `tax_id` (the caller has already resolved the
    requested `ticker` to its tax ID via `etf_pcf.py::fetch_ticker_tax_id_map` — SITCA's own rows
    carry no ticker at all, only 基金統編 + 基金名稱) and read 經理費/保管費/保管機構 by their
    header-column indices — NOT by "any cell containing %", because the 申購手續費/買回費用 cells
    also contain percentages and would otherwise be misread as fees. Returns `None` if no row
    matches, or if the header row (hence the column layout) could not be located."""
    indices = _fee_header_indices(rows)
    if indices is None:
        return None
    mgmt_idx, custody_idx, custodian_idx, tax_id_idx = indices
    for row in rows:
        if not row or _cell(row, tax_id_idx) != tax_id:
            continue
        return _fee_row_for_fund(row, (mgmt_idx, custody_idx, custodian_idx), ticker=ticker)
    return None


# 基金統編 is a plain digit-string tax ID — a real data row's tax-ID cell is non-empty digits;
# header/title/ad/blank rows have an empty or non-numeric cell there.
_TAX_ID_RE = re.compile(r"^\d+$")


def _extract_all_fee_rows(
    rows: list[list[str]], tax_id_to_ticker: dict[str, str]
) -> list[EtfExpenseInfo]:
    """The whole-market counterpart of `_extract_fee_row` — every fund's fee row in one pass,
    for `fetch_whole_market_etf_fees`'s ingest-layer caller. Same header-column-index lookup
    (never "any cell containing %"). SITCA's report covers every mutual fund too, not just
    TWSE-listed ETFs — a row whose 基金統編 has no entry in `tax_id_to_ticker` (TWSE's own
    ticker-carrying disclosure, `etf_pcf.py::fetch_ticker_tax_id_map`) is silently dropped rather
    than emitted under a fabricated or tax-ID-as-symbol identifier."""
    indices = _fee_header_indices(rows)
    if indices is None:
        return []
    mgmt_idx, custody_idx, custodian_idx, tax_id_idx = indices
    infos: list[EtfExpenseInfo] = []
    for row in rows:
        tax_id = (_cell(row, tax_id_idx) or "").strip()
        if not _TAX_ID_RE.match(tax_id):
            continue
        ticker = tax_id_to_ticker.get(tax_id)
        if ticker is None:
            continue
        infos.append(_fee_row_for_fund(row, (mgmt_idx, custody_idx, custodian_idx), ticker=ticker))
    return infos


async def _fetch_fee_table_rows(
    *, tls_policy: TLSPolicy, year_month: str | None = None
) -> tuple[list[list[str]] | None, str | None]:
    """The shared GET-hidden-fields-then-POST-query network round trip both `SitcaAdapter.fetch`
    (per-symbol) and `fetch_whole_market_etf_fees` (whole-market) need — one request shape,
    parsed once, so the two callers never diverge on the postback form assembly. Returns
    `(parsed_rows, error_detail)`; exactly one is `None`.

    `year_month` (`YYYYMM`) selects a specific past disclosure month from the report's own
    dropdown — confirmed live this session that SITCA's postback genuinely serves any past month
    in the option list, not just the latest, so this is a real backfill lever, not a no-op.
    `None` (the default) keeps the existing latest-month behavior.

    Ignores the caller's `tls_policy` and always uses `UNSAFE_EXPLICIT` — SITCA's own certificate
    chain is missing a Subject Key Identifier (confirmed live, still failing under `STRICT` as of
    this fix), the same defect `sources/tpex_openapi.py` once had and later self-resolved when the
    upstream fixed its chain. Scoped to this one call site only: every other adapter still gets
    whatever policy its caller passes, and `sources/tls.py::build_async_client` loudly logs every
    `UNSAFE_EXPLICIT` call, so this is never a silent downgrade."""
    del tls_policy
    try:
        async with build_async_client(tls_policy=TLSPolicy.UNSAFE_EXPLICIT) as client:
            get_response = await client.get(_FEE_REPORT_URL)
            get_response.raise_for_status()
            published = _MONTH_OPTION_RE.findall(get_response.text)
            if year_month is not None and published and year_month not in published:
                return None, MONTH_NOT_PUBLISHED_DETAIL
            form = _build_postback_form(get_response.text, year_month=year_month)
            if form is None:
                return None, "SITCA form hidden fields / month options not found"
            post_response = await client.post(
                _FEE_REPORT_URL,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            post_response.raise_for_status()
    except httpx.HTTPError as exc:
        return None, f"transport error: {type(exc).__name__}"

    table = _FeeTableParser()
    table.feed(post_response.text)
    return table.rows, None


class SitcaAdapter:
    """The `SITCA` source adapter — ETF/fund internal-fee disclosure (see module docstring)."""

    source_id = "SITCA"
    timing_class = TimingClass.OFFICIAL_EOD

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.ETF_EXPENSE_RATIO, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        info = raw.get("info")
        if info is not None and not isinstance(info, dict):
            raise ValueError("SITCA fee payload is not an object")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if cap is not Capability.ETF_EXPENSE_RATIO or sym.market is not Market.TW:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"SITCA does not serve {cap.value} for {sym.market.value}",
                ),
            )

        rows, error = await _fetch_fee_table_rows(tls_policy=tls_policy)
        if rows is None:
            return _result(
                payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=error or "")
            )

        ticker_to_tax_id = await fetch_ticker_tax_id_map(tls_policy=tls_policy)
        tax_id = ticker_to_tax_id.get(sym.id)
        if tax_id is None:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"no TWSE 基金統一編號 found for ticker {sym.id}",
                ),
            )

        info = _extract_fee_row(rows, tax_id, ticker=sym.id)
        if info is None:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"fund {sym.id} not found in SITCA fee report",
                ),
            )
        return _result(
            payload={"info": info.model_dump(mode="json")},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


async def fetch_whole_market_etf_fees(
    *, tls_policy: TLSPolicy, year_month: str | None = None
) -> SourceResult:
    """One whole-market call, every fund's fee row in one response — the ingest layer's sole
    legitimate caller of this function (mirrors `FinMindAdapter.fetch_whole_market`'s own
    governance framing: never a per-symbol fan-out). SITCA's own report already returns every
    fund in one page; `SitcaAdapter.fetch` above simply discards every row but the requested
    fund's after parsing — this function is the same fetch, kept whole. Also fetches TWSE's
    ticker->基金統一編號 map (`etf_pcf.py::fetch_ticker_tax_id_map`) so every emitted row's
    `symbol` is a real TWSE ticker, never SITCA's own tax ID or type code.

    `year_month` (`YYYYMM`) selects a specific past disclosure month for backfill callers
    (`job_etf_expense_ratio`'s monthly walk) — `None` keeps fetching the latest month, the
    original single-snapshot behavior."""
    rows, error = await _fetch_fee_table_rows(tls_policy=tls_policy, year_month=year_month)
    if rows is None and error == MONTH_NOT_PUBLISHED_DETAIL:
        return _result(
            payload={"rows": [], MONTH_NOT_PUBLISHED_KEY: True},
            health=SourceHealth(status=HealthStatus.STALE, detail=MONTH_NOT_PUBLISHED_DETAIL),
        )
    if rows is None:
        return _result(
            payload={"rows": []},
            health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=error or ""),
        )
    ticker_to_tax_id = await fetch_ticker_tax_id_map(tls_policy=tls_policy)
    tax_id_to_ticker = {tax_id: ticker for ticker, tax_id in ticker_to_tax_id.items()}
    infos = _extract_all_fee_rows(rows, tax_id_to_ticker)
    if not infos:
        return _result(
            payload={"rows": []},
            health=SourceHealth(status=HealthStatus.STALE, detail="no fund fee rows parsed"),
        )
    return _result(
        payload={"rows": [info.model_dump(mode="json") for info in infos]},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


_MONTH_OPTION_RE = re.compile(r'<option[^>]*\svalue="(\d{6})"', re.IGNORECASE)


def _build_postback_form(html_text: str, *, year_month: str | None = None) -> dict[str, str] | None:
    """Assemble the POST body: the replayed hidden fields + the selected month option +
    fee-column mode + the query button. `None` when the hidden fields or any month option is
    absent (an upstream layout change) — surfaced as UNREACHABLE rather than a silent bad post.

    `year_month` picks a specific past month from the dropdown's own option list (confirmed live
    to return real per-fund rows for any past month, not just the latest) — `None` falls back to
    `max(months)`, the report's default latest-disclosed month. A `year_month` not present in the
    dropdown's own options is `None` too (never sent as an unvalidated raw value to the postback),
    surfaced by the caller as a missing-data result rather than a guessed query."""
    hidden = _HiddenFieldParser()
    hidden.feed(html_text)
    if not all(field in hidden.fields for field in _HIDDEN_FIELDS):
        return None
    months = _MONTH_OPTION_RE.findall(html_text)
    if not months:
        return None
    if year_month is not None:
        if year_month not in months:
            return None
        selected_month = year_month
    else:
        selected_month = max(months)
    form: dict[str, str] = dict(hidden.fields)
    form[_YEARMONTH_FIELD] = selected_month
    form[_COLUMN_FIELD] = _COLUMN_MODE_FEE
    form[_QUERY_BUTTON_FIELD] = "查詢"
    return form


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=SitcaAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = [
    "MONTH_NOT_PUBLISHED_DETAIL",
    "MONTH_NOT_PUBLISHED_KEY",
    "SitcaAdapter",
    "fetch_whole_market_etf_fees",
]
