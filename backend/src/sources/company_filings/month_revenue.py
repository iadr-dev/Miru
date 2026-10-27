"""Monthly revenue (月營收) from the MOPS static report pages `t21sc03`.

MOPS publishes, for every month (checked back to 2006), one static page per market with the
revenue of every company that filed for that month:
`https://mopsov.twse.com.tw/nas/t21/{sii|otc|rotc}/t21sc03_<ROC year>_<month>.html` (Big5, one
`<tr>` per company, amounts in thousands of TWD). Unlike the OpenAPI dump, which only ever holds
the latest month, the page is addressable by month, so a backfill can reach any past period.

How a run date maps to a period. FinMind files the revenue of month M under the FIRST day of
month M+1 (`trade_date` is that day, never any other), so the period of the 1st is the month
before it and any other date holds no revenue rows by definition. The date PARTITION is that day
when it is a trading day, else the next trading day (`ingest/jobs/period_dates.py`): the job runs
under the partition date but asks for the 1st, so the rows keep FinMind's `trade_date`. The
statutory deadline for the filing is the 10th, and a page fetched earlier is a partial market that
the gap-fill could never complete (a partition with rows is not re-fetched; only the operator's
additive refresh, `ingest/refresh.py`, adds companies later), so the 1st is only served from the
11th of its own month.

Row shape = `sources/finmind/fundamental.py::_map_month_revenue`: revenue in TWD (the page is in
thousands), `revenue_year`/`revenue_month` of the revenue month, `country` "Taiwan". `create_time`
(when FinMind first saw the filing) has no official counterpart and stays `None`, which the row
model allows and FinMind itself leaves empty for older rows.

Markets: listed (`sii`), OTC (`otc`) and emerging (`rotc`), the three FinMind covers (the fourth
page, `pub`, lists public non-trading companies FinMind does not carry). Plain `otc`/`rotc` pages
(not the `_0` ones) include the foreign-registered companies, as FinMind does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_
from typing import Final

import structlog

from sources.exchange_whole_market._common import TAIWAN_MARKET, ExchangeFetchError, Row, clean_cell

logger = structlog.get_logger(__name__)

HOST: Final = "mopsov.twse.com.tw"
_BASE: Final = f"https://{HOST}/nas/t21"
ENCODING: Final = "big5hkscs"
# (page directory, label in the journal). Listed and OTC always exist; emerging may be empty for
# a month before that market reported revenue.
MARKETS: Final = (("sii", "listed"), ("otc", "OTC"), ("rotc", "emerging"))
REQUIRED_MARKETS: Final = frozenset({"sii", "otc"})
# Revenue is due on the 10th; from the 11th the month is complete.
_FILING_DEADLINE_DAY: Final = 10
_ROC_OFFSET: Final = 1911
# code, name, 3 revenues, 2 growth rates, 2 cumulatives, growth (+ a remark since the 2010s)
_MIN_CELLS: Final = 10
_CODE, _CURRENT = 0, 2
_NO_DATA_MARKER: Final = "查無資料"
_TITLE: Final = re.compile(r"<font size='5'><b>([^<]*)</b>", re.IGNORECASE)
# A company row opens with its code in a `<td>`; the 合計 and 總計 rows open with a `<th>`.
_ROW: Final = re.compile(r"<tr align=right>(<td align=center>.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL: Final = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_THOUSAND: Final = 1000


@dataclass(frozen=True)
class RevenuePeriod:
    """The revenue month a run date stands for, and the date its rows are filed under."""

    year: int
    month: int
    run_date: date_


def period_for(run_date: date_) -> RevenuePeriod | None:
    """The month whose revenue FinMind files under `run_date`: the previous month, for the 1st
    of a month; `None` for any other day (no revenue row has that date)."""
    if run_date.day != 1:
        return None
    year, month = (
        (run_date.year - 1, 12) if run_date.month == 1 else (run_date.year, run_date.month - 1)
    )
    return RevenuePeriod(year, month, run_date)


def is_complete(period: RevenuePeriod, today: date_) -> bool:
    """Whether every company has had until the deadline (the 10th of the following month)."""
    return today > period.run_date.replace(day=_FILING_DEADLINE_DAY)


def page_url(market_dir: str, period: RevenuePeriod) -> str:
    return f"{_BASE}/{market_dir}/t21sc03_{period.year - _ROC_OFFSET}_{period.month}.html"


def parse_page(html: str, market_dir: str, period: RevenuePeriod) -> list[Row]:
    """The revenue rows of one page. A month nobody has filed for is the page's own "查無資料"
    (an empty list); a page that is neither that nor a report of the requested month (a block
    or challenge page, a changed layout) is an error, never an empty month."""
    label = f"t21sc03 {market_dir} {period.year}-{period.month:02d}"
    title = _TITLE.search(html)
    wanted = f"{period.year - _ROC_OFFSET}年{period.month}月"
    if title is None or wanted not in title.group(1):
        raise ExchangeFetchError(f"{label}: not the report page for {wanted}")
    rows = _data_rows(html, period)
    if rows:
        return rows
    if _NO_DATA_MARKER in html:
        return []
    raise ExchangeFetchError(f"{label}: the page has no revenue rows and is not an empty report")


def _data_rows(html: str, period: RevenuePeriod) -> list[Row]:
    rows: list[Row] = []
    seen: set[str] = set()
    for match in _ROW.finditer(html):
        cells = [clean_cell(c) for c in _CELL.findall(match.group(1))]
        if len(cells) < _MIN_CELLS:
            continue
        code = cells[_CODE]
        thousands = _thousands(cells[_CURRENT])
        if not code or thousands is None:
            logger.warning("revenue_row_dropped", code=code, month=f"{period.year}-{period.month}")
            continue
        if code in seen:
            logger.warning("revenue_duplicate_row", code=code)
            continue
        seen.add(code)
        rows.append(
            {
                "symbol": code,
                "market": TAIWAN_MARKET,
                "trade_date": period.run_date.isoformat(),
                "country": "Taiwan",
                "revenue": thousands * _THOUSAND,
                "revenue_month": period.month,
                "revenue_year": period.year,
                "create_time": None,
            }
        )
    return rows


def _thousands(text: str) -> int | None:
    cleaned = text.replace(",", "")
    return int(cleaned) if re.fullmatch(r"-?\d+", cleaned) else None


__all__ = [
    "ENCODING",
    "HOST",
    "MARKETS",
    "REQUIRED_MARKETS",
    "RevenuePeriod",
    "is_complete",
    "page_url",
    "parse_page",
    "period_for",
]
