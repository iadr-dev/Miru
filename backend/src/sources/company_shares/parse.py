"""Reading the two open-data company-basics dumps into share counts.

TWSE `opendata/t187ap03_L` (listed companies) and TPEx `mopsfin_t187ap03_O` (OTC companies) each
publish, per company, the number of issued common shares and a table date (ROC `YYYMMDD`). Both are
JSON arrays of flat objects with Chinese (TWSE) or English (TPEx) keys.

Share counts are issued COMMON shares: preferred shares and private placements are separate fields
and are not added, which matches the market value the exchanges print (verified against stored
`TaiwanStockMarketValue` rows: `close x issued common shares` is exact for about 85% of the stocks
and within 1% for most of the rest, the difference being shares issued or cancelled since).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Final

from sources.exchange_whole_market._common import ExchangeFetchError, JsonObject

LISTED: Final = "twse"
OTC: Final = "tpex"

# A real listed board has about 1100 companies and the OTC board about 900; a dump far below that
# is a truncated or wrong page, never a market.
MIN_LISTED_COMPANIES: Final = 500
MIN_OTC_COMPANIES: Final = 500

_ROC_DATE: Final = re.compile(r"^(\d{2,3})(\d{2})(\d{2})$")
_ROC_OFFSET: Final = 1911
_DIGITS: Final = re.compile(r"^\d+$")
# Taiwan Depositary Receipts: the dump carries the ORIGINAL shares of the foreign issuer, not the
# receipts in issue, so close x shares is not the receipts' market value. Codes 91xx / 91xxxx.
_DEPOSITARY_RECEIPT_PREFIX: Final = "91"

_LISTED_KEYS: Final = ("公司代號", "公司簡稱", "已發行普通股數或TDR原股發行股數", "出表日期")
_OTC_KEYS: Final = ("SecuritiesCompanyCode", "CompanyAbbreviation", "IssueShares", "Date")


@dataclass(frozen=True)
class ShareEntry:
    symbol: str
    name: str
    listing_type: str  # LISTED / OTC
    shares: int


@dataclass(frozen=True)
class ParsedDump:
    """One board's dump: its companies with a usable share count, how many were left out and why."""

    as_of: date
    entries: tuple[ShareEntry, ...]
    without_shares: int  # a blank, zero or non-numeric share count: never invented
    depositary_receipts: int


def roc_date(text: str) -> date:
    """`1151026` -> 2026-10-26. A value that is not a ROC date is an error, not a guess."""
    match = _ROC_DATE.match(text.strip())
    if match is None:
        raise ExchangeFetchError(f"company basics: unreadable table date {text!r}")
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year + _ROC_OFFSET, month, day)
    except ValueError as exc:
        raise ExchangeFetchError(f"company basics: unreadable table date {text!r}") from exc


def _text(row: JsonObject, key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def _parse(
    rows: list[JsonObject], keys: tuple[str, str, str, str], listing_type: str, minimum: int
) -> ParsedDump:
    code_key, name_key, shares_key, date_key = keys
    if len(rows) < minimum:
        raise ExchangeFetchError(
            f"company basics ({listing_type}): {len(rows)} companies, expected at least {minimum}"
        )
    if code_key not in rows[0] or shares_key not in rows[0]:
        raise ExchangeFetchError(f"company basics ({listing_type}): layout changed")
    entries: dict[str, ShareEntry] = {}
    without_shares = receipts = 0
    dumped: set[date] = set()
    for row in rows:
        symbol = _text(row, code_key)
        if not symbol:
            continue
        dumped.add(roc_date(_text(row, date_key)))
        if listing_type == LISTED and symbol.startswith(_DEPOSITARY_RECEIPT_PREFIX):
            receipts += 1
            continue
        raw_shares = _text(row, shares_key).replace(",", "")
        if not _DIGITS.match(raw_shares) or int(raw_shares) <= 0:
            without_shares += 1
            continue
        entries[symbol] = ShareEntry(symbol, _text(row, name_key), listing_type, int(raw_shares))
    if not dumped:
        raise ExchangeFetchError(f"company basics ({listing_type}): no companies")
    return ParsedDump(min(dumped), tuple(entries.values()), without_shares, receipts)


def parse_listed(rows: list[JsonObject], *, minimum: int = MIN_LISTED_COMPANIES) -> ParsedDump:
    return _parse(rows, _LISTED_KEYS, LISTED, minimum)


def parse_otc(rows: list[JsonObject], *, minimum: int = MIN_OTC_COMPANIES) -> ParsedDump:
    return _parse(rows, _OTC_KEYS, OTC, minimum)
