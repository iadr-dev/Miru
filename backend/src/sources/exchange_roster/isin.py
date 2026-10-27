"""The TWSE ISIN lists (`isin.twse.com.tw/isin/C_public.jsp?strMode=2|4|5`) read as the Taiwan
stock roster, in the shape the roster was stored in while FinMind's `TaiwanStockInfo` fed it.

The lists are Big5 (MS950) HTML, one per board: 2 listed (上市), 4 OTC (上櫃), 5 emerging (興櫃).
Each holds one table: `代號　名稱 | ISIN | 上市日 | 市場別 | 產業別 | CFICode | 備註`, grouped under
section rows (`股票`, `ETF`, `特別股`, ... and, by far the largest, the warrants). The emerging list
has no section rows. A page announces its own `最近更新日期` (last update date).

What a FinMind roster row is, and how it is rebuilt here (measured against the roster stored in
2026-10, 2,736 symbols in common):

- one security per row: `symbol`, `name`, `industry_category`, `security_type` (`twse`, `tpex`,
  `emerging`), `as_of` (a date, here the list's own last-update date);
- sections kept: stocks, preferred shares, ETFs, ETNs and the TWSE innovation board. Dropped:
  warrants (tens of thousands of short-lived series), depositary receipts, REITs and
  asset-backed securities, none of which the FinMind roster listed either;
- `industry_category` is the Chinese category name FinMind used, which is the exchange's name with
  a few spelling differences that depend on the board (`建材營造業` is `建材營造`; OTC writes
  `居家生活類`, the listed board `居家生活`). FinMind additionally repeated a stock under a
  composite parent category (`電子工業`, `化學生技醫療`) and the innovation board marker; those
  duplicate rows are not rebuilt: a reader keeps ONE category per symbol and the specific one
  is the useful one;
- ETFs and ETNs carry no exchange category; FinMind's own labels are used (`ETF` / `上櫃ETF`,
  `ETN` / `指數投資證券(ETN)`); a preferred share without one takes its common share's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_
from typing import Final

import structlog

from sources.exchange_whole_market._common import TAIWAN_MARKET, ExchangeFetchError, Row

logger = structlog.get_logger(__name__)

HOST: Final = "isin.twse.com.tw"
ENCODING: Final = "cp950"  # the pages say charset=MS950
_URL: Final = f"https://{HOST}/isin/C_public.jsp"

TWSE: Final = "twse"
TPEX: Final = "tpex"
EMERGING: Final = "emerging"


@dataclass(frozen=True)
class Board:
    """One ISIN list: the page parameter, the `security_type` its rows carry and the fewest rows
    a healthy page has (a page below it is a changed layout or a block page, not a small board)."""

    mode: int
    security_type: str
    label: str
    min_entries: int

    @property
    def url(self) -> str:
        return f"{_URL}?strMode={self.mode}"


BOARDS: Final = (
    Board(2, TWSE, "listed", 800),
    Board(4, TPEX, "OTC", 500),
    Board(5, EMERGING, "emerging", 150),
)

_UPDATED: Final = re.compile(r"最近更新日期[:：]\s*(\d{4})/(\d{2})/(\d{2})")
_HEADER: Final = ("有價證券代號及名稱", "產業別")
_ROW: Final = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_CELL: Final = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_TAG: Final = re.compile(r"<[^>]*>")
_SECTION_MARK: Final = "colspan=7"
_CELLS: Final = 7
# ISO 6166: two letters of the issuing country (`TW`; `KY` or `BM` for a foreign-registered
# company listed in Taipei, the `-KY` stocks), nine alphanumerics, a check digit.
_ISIN_CODE: Final = re.compile(r"[A-Z]{2}[A-Z0-9]{9}\d")
_NAME_SEPARATOR: Final = "　"  # the code and the name are joined by an ideographic space
_PREFERRED_CODE: Final = re.compile(r"(\d{4})[A-Z]")

STOCK_SECTION: Final = "股票"
PREFERRED_SECTION: Final = "特別股"
ETF_SECTION: Final = "ETF"
ETN_SECTION: Final = "ETN"
INNOVATION_SECTION: Final = "創新板"
# The sections the FinMind roster carried (the emerging list has none: its rows are stocks).
KEPT_SECTIONS: Final = frozenset(
    {STOCK_SECTION, PREFERRED_SECTION, ETF_SECTION, ETN_SECTION, INNOVATION_SECTION}
)

# The exchange's category name -> FinMind's, per board, for the names that differ.
_RENAMES_ALL: Final = {
    "其他業": "其他",
    "建材營造業": "建材營造",
    "貿易百貨業": "貿易百貨",
}
_RENAMES: Final = {
    TWSE: {**_RENAMES_ALL, "金融保險業": "金融保險"},
    TPEX: {
        **_RENAMES_ALL,
        "金融保險業": "金融業",
        "其他電子業": "其他電子類",
        "居家生活": "居家生活類",
        "數位雲端": "數位雲端類",
        "綠能環保": "綠能環保類",
        "運動休閒": "運動休閒類",
    },
    EMERGING: {**_RENAMES_ALL, "金融保險業": "金融業", "農業科技業": "農業科技"},
}
# What FinMind called the funds, which the exchange lists without a category.
_FUND_CATEGORY: Final = {
    (ETF_SECTION, TWSE): "ETF",
    (ETF_SECTION, TPEX): "上櫃ETF",
    (ETN_SECTION, TWSE): "ETN",
    (ETN_SECTION, TPEX): "指數投資證券(ETN)",
}
# Every category the FinMind roster used for a single security (the composite parents and the
# board marker excluded). A rebuilt category outside it is logged, never dropped: a new exchange
# category is news, not an error.
KNOWN_CATEGORIES: Final = frozenset(
    {
        "ETF",
        "ETN",
        "上櫃ETF",
        "指數投資證券(ETN)",
        "光電業",
        "其他",
        "其他電子業",
        "其他電子類",
        "化學工業",
        "半導體業",
        "塑膠工業",
        "居家生活",
        "居家生活類",
        "建材營造",
        "數位雲端",
        "數位雲端類",
        "文化創意業",
        "水泥工業",
        "汽車工業",
        "油電燃氣業",
        "玻璃陶瓷",
        "生技醫療業",
        "紡織纖維",
        "綠能環保",
        "綠能環保類",
        "航運業",
        "觀光餐旅",
        "貿易百貨",
        "資訊服務業",
        "農業科技",
        "農業科技業",
        "通信網路業",
        "運動休閒",
        "運動休閒類",
        "金融保險",
        "金融業",
        "鋼鐵工業",
        "造紙工業",
        "橡膠工業",
        "電器電纜",
        "電子通路業",
        "電子零組件業",
        "電機機械",
        "電腦及週邊設備業",
        "食品工業",
    }
)


@dataclass(frozen=True)
class IsinEntry:
    """One kept security of an ISIN list, as the page prints it."""

    code: str
    name: str
    industry: str  # the exchange's category name; "" where it prints none
    section: str | None
    security_type: str
    updated: date_


def parse_page(html: str, board: Board) -> list[IsinEntry]:
    """The kept securities of one list. A page that is not the list (a block or challenge page, a
    changed layout) or that holds far fewer securities than the board does is an error, never a
    small roster."""
    label = f"ISIN list {board.label}"
    updated = _updated(html)
    if updated is None or not all(word in html for word in _HEADER):
        raise ExchangeFetchError(f"{label}: not the ISIN list page")
    entries: list[IsinEntry] = []
    section: str | None = None
    skipped: set[str] = set()
    for match in _ROW.finditer(html):
        body = match.group(1)
        if _SECTION_MARK in body:
            section = _TAG.sub("", body).strip()
            continue
        if section is not None and section not in KEPT_SECTIONS:
            skipped.add(section)
            continue
        entry = _entry(body, section, board, updated)
        if entry is not None:
            entries.append(entry)
    if len(entries) < board.min_entries:
        raise ExchangeFetchError(
            f"{label}: {len(entries)} securities, fewer than the {board.min_entries} expected"
        )
    unknown = skipped - _DROPPED_SECTIONS
    if unknown:
        logger.warning("isin_unknown_sections_skipped", board=board.label, sections=sorted(unknown))
    return entries


# Sections seen on the lists that are deliberately left out; any other section name is logged.
_DROPPED_SECTIONS: Final = frozenset(
    {
        "上市認購(售)權證",
        "上櫃認購(售)權證",
        "臺灣存託憑證(TDR)",
        "受益證券-不動產投資信託",
        "受益證券-資產基礎證券",
    }
)


def _updated(html: str) -> date_ | None:
    match = _UPDATED.search(html)
    if match is None:
        return None
    try:
        return date_(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _entry(body: str, section: str | None, board: Board, updated: date_) -> IsinEntry | None:
    cells = [_TAG.sub("", c).strip() for c in _CELL.findall(body)]
    if len(cells) != _CELLS or not _ISIN_CODE.fullmatch(cells[1]):  # the header row, a stray row
        return None
    code, separator, name = cells[0].partition(_NAME_SEPARATOR)
    if not separator or not code or not name:
        logger.warning("isin_row_dropped", board=board.label, cell=cells[0])
        return None
    return IsinEntry(code, name.strip(), cells[4], section, board.security_type, updated)


def _category(entry: IsinEntry) -> str | None:
    """FinMind's category for one entry; `None` where the exchange prints none and the section
    gives no label either (a preferred share: it takes its common share's)."""
    fund = _FUND_CATEGORY.get((entry.section or "", entry.security_type))
    if fund is not None:
        return fund
    if not entry.industry:
        return None
    return _RENAMES[entry.security_type].get(entry.industry, entry.industry)


def to_rows(entries: list[IsinEntry]) -> list[Row]:
    """The roster rows, sorted by symbol, one per security. A code on more than one list keeps
    the first board's (listed, then OTC, then emerging: the order of `BOARDS`)."""
    unique: dict[str, IsinEntry] = {}
    for entry in entries:
        if entry.code in unique:
            logger.warning("isin_duplicate_code", code=entry.code)
            continue
        unique[entry.code] = entry
    categories = {code: _category(entry) for code, entry in unique.items()}
    rows: list[Row] = []
    unmapped: dict[str, int] = {}
    for code in sorted(unique):
        entry = unique[code]
        category = categories[code]
        if category is None:
            preferred = _PREFERRED_CODE.fullmatch(code)
            category = categories.get(preferred.group(1)) if preferred else None
        if category is None:
            category = ""
            logger.warning("isin_industry_missing", code=code, name=entry.name)
        elif category not in KNOWN_CATEGORIES:
            unmapped[category] = unmapped.get(category, 0) + 1
        rows.append(
            {
                "symbol": code,
                "market": TAIWAN_MARKET,
                "name": entry.name,
                "industry_category": category,
                "security_type": entry.security_type,
                "as_of": entry.updated.isoformat(),
            }
        )
    if unmapped:
        logger.warning("isin_industry_unmapped", categories=unmapped)
    return rows


__all__ = [
    "BOARDS",
    "EMERGING",
    "ENCODING",
    "HOST",
    "KEPT_SECTIONS",
    "KNOWN_CATEGORIES",
    "TPEX",
    "TWSE",
    "Board",
    "IsinEntry",
    "parse_page",
    "to_rows",
]
