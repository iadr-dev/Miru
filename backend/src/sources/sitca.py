"""SITCA (中華民國證券投資信託暨顧問商業同業公會) fee-disclosure adapter — ETF/fund internal-fee
(內扣費用) reference data, the one field the TWSE/TPEx/TDCC/FinMind sources do not carry.

Source: the association's public monthly report `IN2105.aspx` (基金基本資料表) in its
`費率、銀行與規定` column mode (`ddlQ_Column=2`). It is a classic ASP.NET postback form, served with
no auth: this adapter GETs the page once to read the `__VIEWSTATE` / `__VIEWSTATEGENERATOR` /
`__EVENTVALIDATION` hidden fields, then POSTs them back with the most-recent month and fee-column
mode selected, and parses the returned per-fund table. Whole table in one response; `fetch()`
still serves one symbol per call and filters to the requested fund code client-side (same shape
as this package's other whole-market-dump-then-filter adapters).

What it yields per fund: 經理費 (management fee %), 保管費 (custody fee %), 保管機構 (custodian
bank). A true total-expense-ratio (TER) is NOT a disclosed column — it is approximated as
經理費 + 保管費 in the tool layer, floored (excludes other operating costs), and caveated there.
NAV/IOPV is not here (nor anywhere aggregated) — that gap is unchanged by this adapter.
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
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_FEE_REPORT_URL = "https://www.sitca.org.tw/ROC/Industry/IN2105.aspx?pid=IN2212_02"
_COLUMN_MODE_FEE = "2"  # ddlQ_Column option: 費率、銀行與規定
_YEARMONTH_FIELD = "ctl00$ContentPlaceHolder1$ddlQ_YYYYMM"
_COLUMN_FIELD = "ctl00$ContentPlaceHolder1$ddlQ_Column"
_QUERY_BUTTON_FIELD = "ctl00$ContentPlaceHolder1$BtnQuery"
_HIDDEN_FIELDS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")

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


_MANAGEMENT_FEE_HEADER = "經理費"
_CUSTODY_FEE_HEADER = "保管費"
_CUSTODIAN_HEADER = "保管機構"


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
    rows: list[list[str]], row: list[str], indices: tuple[int, int, int]
) -> EtfExpenseInfo:
    """Build one fund's `EtfExpenseInfo` from an already-located data `row` and its
    `(mgmt_idx, custody_idx, custodian_idx)` header-column indices — the shared per-row
    extraction both `_extract_fee_row` (per-symbol) and `_extract_all_fee_rows` (whole-market)
    call, so the two never drift on the fee-math/caveat convention."""
    del rows  # kept in the signature for a stable call shape; not read directly
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
        symbol=row[0].strip(),
        market=Market.TW,
        management_fee_pct=management_fee,
        custody_fee_pct=custody_fee,
        total_expense_ratio_pct_approx=ter,
        custodian_bank=custodian,
    )


def _fee_header_indices(rows: list[list[str]]) -> tuple[int, int, int] | None:
    mgmt_idx = _header_index(rows, _MANAGEMENT_FEE_HEADER)
    custody_idx = _header_index(rows, _CUSTODY_FEE_HEADER)
    custodian_idx = _header_index(rows, _CUSTODIAN_HEADER)
    if mgmt_idx is None or custody_idx is None or custodian_idx is None:
        return None
    return mgmt_idx, custody_idx, custodian_idx


def _extract_fee_row(rows: list[list[str]], fund_code: str) -> EtfExpenseInfo | None:
    """Find the data row whose first cell is `fund_code` and read 經理費/保管費/保管機構 by their
    header-column indices — NOT by "any cell containing %", because the 申購手續費/買回費用 cells
    also contain percentages and would otherwise be misread as fees. Returns `None` if no row
    matches the fund, or if the header row (hence the column layout) could not be located."""
    indices = _fee_header_indices(rows)
    if indices is None:
        return None
    for row in rows:
        if not row or row[0].strip() != fund_code:
            continue
        return _fee_row_for_fund(rows, row, indices)
    return None


# A fund code is `00`-prefixed (ETF) or a plain digit-string fund code (mutual fund) — both real,
# non-empty leading cells; header/title/blank rows have an empty or non-code-shaped first cell.
_FUND_CODE_RE = re.compile(r"^\d")


def _extract_all_fee_rows(rows: list[list[str]]) -> list[EtfExpenseInfo]:
    """The whole-market counterpart of `_extract_fee_row` — every fund's fee row in one pass,
    for `fetch_whole_market_etf_fees`'s ingest-layer caller. Same header-column-index lookup
    (never "any cell containing %"), just not filtered down to a single fund code."""
    indices = _fee_header_indices(rows)
    if indices is None:
        return []
    infos: list[EtfExpenseInfo] = []
    for row in rows:
        if not row or not _FUND_CODE_RE.match(row[0].strip()):
            continue
        infos.append(_fee_row_for_fund(rows, row, indices))
    return infos


async def _fetch_fee_table_rows(
    *, tls_policy: TLSPolicy
) -> tuple[list[list[str]] | None, str | None]:
    """The shared GET-hidden-fields-then-POST-query network round trip both `SitcaAdapter.fetch`
    (per-symbol) and `fetch_whole_market_etf_fees` (whole-market) need — one request shape,
    parsed once, so the two callers never diverge on the postback form assembly. Returns
    `(parsed_rows, error_detail)`; exactly one is `None`."""
    try:
        async with build_async_client(tls_policy=tls_policy) as client:
            get_response = await client.get(_FEE_REPORT_URL)
            get_response.raise_for_status()
            form = _build_postback_form(get_response.text)
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

        info = _extract_fee_row(rows, sym.id)
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


async def fetch_whole_market_etf_fees(*, tls_policy: TLSPolicy) -> SourceResult:
    """One whole-market call, every fund's fee row in one response — the ingest layer's sole
    legitimate caller of this function (mirrors `FinMindAdapter.fetch_whole_market`'s own
    governance framing: never a per-symbol fan-out). SITCA's own report already returns every
    fund in one page; `SitcaAdapter.fetch` above simply discards every row but the requested
    fund's after parsing — this function is the same fetch, kept whole."""
    rows, error = await _fetch_fee_table_rows(tls_policy=tls_policy)
    if rows is None:
        return _result(
            payload={"rows": []},
            health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=error or ""),
        )
    infos = _extract_all_fee_rows(rows)
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


def _build_postback_form(html_text: str) -> dict[str, str] | None:
    """Assemble the POST body: the replayed hidden fields + the most-recent month option +
    fee-column mode + the query button. `None` when the hidden fields or any month option is
    absent (an upstream layout change) — surfaced as UNREACHABLE rather than a silent bad post."""
    hidden = _HiddenFieldParser()
    hidden.feed(html_text)
    if not all(field in hidden.fields for field in _HIDDEN_FIELDS):
        return None
    months = _MONTH_OPTION_RE.findall(html_text)
    if not months:
        return None
    latest_month = max(months)
    form: dict[str, str] = dict(hidden.fields)
    form[_YEARMONTH_FIELD] = latest_month
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


__all__ = ["SitcaAdapter", "fetch_whole_market_etf_fees"]
