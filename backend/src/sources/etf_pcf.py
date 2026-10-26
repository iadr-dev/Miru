"""ETF fund-info adapter — the concrete, verifiable slice of the "issuer PCF / 投信投顧公會 /
TWSE `fund/`" ETF gap.

What this adapter actually builds (documented honestly, not guessed):

- **TWSE `rwd/zh/ETF/list`** — the listed-ETF roster: issuer, listing date, tracked index.
- **TWSE OpenAPI `opendata/t187ap47_L`** — fund basic-data disclosure: fund type, units
  outstanding (發行單位數/轉換數 — one AUM component), manager company. Covers every listed
  ETF type (被動/傳統, 主動式, 槓桿/反向, 境外), not just active ETFs.

What this adapter does **not** build (the genuine remaining gap, left unfilled rather than
guessed): per-constituent holdings/weights (the issuer PCF file / 投信投顧公會 daily
composition report) and NAV/IOPV. No single free, stable, machine-readable endpoint for
per-ETF daily constituent weights was identified with confidence — inventing one would violate
the "never guess a URL" rule. `ETF_HOLDINGS` results from this adapter are therefore
**fund-level metadata only** (`EtfFundInfo`); the reverse "被 N 支 ETF 持有" view stays
unimplemented for the passive/傳統 side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast

import httpx
import structlog

from schemas.capabilities import EtfFundInfo
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_LIST_URL = "https://www.twse.com.tw/rwd/zh/ETF/list?response=json"
_FUND_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"

_CAVEAT = (
    "TWSE fund-level ETF reference data (roster + units-outstanding disclosure) — refreshed on "
    "the issuer's reporting cadence, not a live figure; carries no per-constituent "
    "holdings/weights (that issuer-PCF slice remains an unfilled gap) and no NAV/IOPV."
)


@dataclass(frozen=True)
class _ListRow:
    listed_date: date | None
    symbol: str
    name: str
    issuer: str
    tracked_index: str


_DOT_DATE_PARTS = 3  # YYYY.MM.DD

# The one cast() target type every raw JSON list-of-unknowns value below uses.
type _UntypedList = list[object]


_ROC_COMPACT_MIN_LEN = 6  # ROC yyyMMdd is 6-7 digits (e.g. 1150330); reject shorter garbage
_ROC_YEAR_OFFSET = 1911  # ROC year + 1911 = Gregorian year


def _parse_roc_compact_date(value: str) -> date | None:
    """`t187ap47_L` dates are ROC-compact digit strings (e.g. `1150330` = ROC 115/03/30 =
    2026-03-30): last 4 digits are MMDD, the remainder is the ROC year. Distinct from the
    `YYYY.MM.DD` dotted form the ETF-list source uses (`_parse_roc_or_dot_date`)."""
    digits = value.strip()
    if not digits.isdigit() or len(digits) < _ROC_COMPACT_MIN_LEN:
        return None
    try:
        year = int(digits[:-4]) + _ROC_YEAR_OFFSET
        month = int(digits[-4:-2])
        day = int(digits[-2:])
        return date(year, month, day)
    except ValueError:
        return None


def _parse_roc_or_dot_date(value: str) -> date | None:
    """TWSE `fund/list` dates are `YYYY.MM.DD` (Gregorian); dual-listing rows (e.g. a
    NTD/RMB share class pair) embed `<br>`-joined values — take the first."""
    first = value.split("<br>", maxsplit=1)[0].strip()
    parts = first.split(".")
    if len(parts) != _DOT_DATE_PARTS:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _parse_list_rows(raw: dict[str, object]) -> dict[str, _ListRow]:
    fields = raw.get("fields")
    data = raw.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise ValueError("TWSE ETF list response missing 'fields'/'data'")
    field_names = [str(f) for f in cast(_UntypedList, fields)]
    try:
        idx_date = field_names.index("上市日期")
        idx_code = field_names.index("證券代號")
        idx_name = field_names.index("證券簡稱")
        idx_issuer = field_names.index("發行人")
        idx_index = field_names.index("標的指數")
    except ValueError as exc:
        raise ValueError(f"TWSE ETF list response missing expected column: {exc}") from exc

    rows: dict[str, _ListRow] = {}
    for raw_row in cast(_UntypedList, data):
        if not isinstance(raw_row, list):
            continue
        row = cast(_UntypedList, raw_row)
        if len(row) <= max(idx_date, idx_code, idx_name, idx_issuer, idx_index):
            continue
        # A dual-listing row packs two codes as `A<br>B` — the first is the primary/NTD line.
        code = str(row[idx_code]).split("<br>", maxsplit=1)[0].strip()
        if not code:
            continue
        rows[code] = _ListRow(
            listed_date=_parse_roc_or_dot_date(str(row[idx_date])),
            symbol=code,
            name=str(row[idx_name]).split("<br>", maxsplit=1)[0].strip(),
            issuer=str(row[idx_issuer]),
            tracked_index=str(row[idx_index]),
        )
    return rows


def _parse_fund_info_rows(raw: object) -> dict[str, dict[str, object]]:
    if not isinstance(raw, list):
        raise ValueError("TWSE t187ap47_L response is not a list")
    rows: dict[str, dict[str, object]] = {}
    for raw_row in cast(_UntypedList, raw):
        if not isinstance(raw_row, dict):
            continue
        row = cast("dict[str, object]", raw_row)
        code = str(row.get("基金代號", "")).strip()
        if code:
            rows[code] = row
    return rows


def _as_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


async def fetch_ticker_tax_id_map(*, tls_policy: TLSPolicy) -> dict[str, str]:
    """`基金代號` (TWSE-listed ticker) -> `基金統一編號` (tax ID), from `t187ap47_L` alone (no
    `ETF/list` call needed). The one place a ticker and a tax ID are linked in this codebase —
    `sources/sitca.py` uses this to resolve a ticker to the tax ID SITCA's own fee report keys
    funds by (`基金統編`, the same tax ID, not a ticker at all), since SITCA carries no ticker
    column. Empty dict (never raises) on any transport/shape failure — callers treat an
    unresolvable ticker as "not found", not a hard error."""
    try:
        async with build_async_client(tls_policy=tls_policy) as client:
            response = await client.get(_FUND_INFO_URL)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError:
        return {}
    try:
        fund_info_rows = _parse_fund_info_rows(body)
    except ValueError:
        return {}
    result: dict[str, str] = {}
    for code, row in fund_info_rows.items():
        tax_id = str(row.get("基金統一編號", "")).strip()
        if tax_id:
            result[code] = tax_id
    return result


def _build_fund_info(
    sym: Symbol, listing: _ListRow | None, fund_info: dict[str, object] | None
) -> EtfFundInfo:
    """Merge whichever of the two TWSE sources actually has a row for this symbol — either can
    be `None` (already checked by the caller that at least one is not) — into one `EtfFundInfo`.
    Pulled out of `EtfPcfAdapter.fetch` purely to keep that method's branching shallow; no
    behavior of its own beyond the field-by-field fallback each line already documents."""
    listing_name = listing.name if listing else ""
    fund_name = str(fund_info.get("基金簡稱", "")) if fund_info else listing_name
    fund_type = str(fund_info.get("基金類型", "")) if fund_info else "unknown"
    units = _as_int_or_none(fund_info.get("發行單位數/轉換數")) if fund_info else None
    # `基金經理人` is the manager PERSON (e.g. 梁恩溢), not the investment-trust company — a real
    # mislabel this now corrects: it populates manager_name, and manager_company stays None since
    # t187ap47_L carries no clean company column (only inside 基金中文名稱).
    manager_name = str(fund_info.get("基金經理人", "")) if fund_info else None
    inception = _parse_roc_compact_date(str(fund_info.get("成立日期", ""))) if fund_info else None
    custodian = str(fund_info.get("保管機構", "")) if fund_info else None
    return EtfFundInfo(
        symbol=sym.id,
        market=sym.market,
        fund_name=fund_name or sym.id,
        fund_type=fund_type,
        tracked_index=(listing.tracked_index if listing else None) or None,
        units_outstanding=units,
        listed_date=listing.listed_date if listing else None,
        inception_date=inception,
        manager_name=manager_name or None,
        custodian_bank=custodian or None,
    )


class EtfPcfAdapter:
    """The `ETF_PCF` source adapter — fund-level ETF reference data (see module docstring for
    exactly what is/isn't covered)."""

    source_id = "ETF_PCF"
    timing_class = TimingClass.OFFICIAL_EOD

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.ETF_HOLDINGS, Market.TW)}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        info = raw.get("info")
        if info is not None and not isinstance(info, dict):
            raise ValueError("ETF fund-info payload is not an object")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if cap is not Capability.ETF_HOLDINGS or sym.market is not Market.TW:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"ETF_PCF does not serve {cap.value} for {sym.market.value}",
                ),
            )

        try:
            async with build_async_client(tls_policy=tls_policy) as client:
                list_response = await client.get(_LIST_URL)
                list_response.raise_for_status()
                fund_info_response = await client.get(_FUND_INFO_URL)
                fund_info_response.raise_for_status()
        except httpx.HTTPError as exc:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNREACHABLE, detail=f"transport error: {type(exc).__name__}"
                ),
            )

        try:
            list_rows = _parse_list_rows(list_response.json())
            fund_info_rows = _parse_fund_info_rows(fund_info_response.json())
        except ValueError as exc:
            return _result(
                payload={}, health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc))
            )

        listing = list_rows.get(sym.id)
        fund_info = fund_info_rows.get(sym.id)
        if listing is None and fund_info is None:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.STALE,
                    detail=f"symbol {sym.id} not found in either TWSE ETF source",
                ),
            )

        info = _build_fund_info(sym, listing, fund_info)
        return _result(
            payload={"info": info.model_dump(mode="json")},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=EtfPcfAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


__all__ = ["EtfPcfAdapter", "fetch_ticker_tax_id_map"]
