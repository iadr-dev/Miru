"""TWSE (listed market) history of the disposition and attention-stock announcements.

`www.twse.com.tw/rwd/zh/announcement/{punish,notice}?startDate=YYYYMMDD&endDate=YYYYMMDD`, the
reports behind the exchange's own search pages. Unlike the current-day OpenAPI dumps the daily
jobs read, they take a date range. The disposition report returns every disposition whose period
overlaps the range (a month query therefore repeats a period that spans two months; the caller
deduplicates), the attention report every flag announced inside it.

Each parser returns rows in the stored `DispositionEventRow` / `AttentionStockRow` shape.
"""

from __future__ import annotations

from datetime import date as date_
from typing import Final

from sources.exchange_whole_market._common import (
    TAIWAN_MARKET,
    ExchangeFetchError,
    JsonObject,
    NoDataPublishedError,
    Row,
    as_list,
    cell,
    clean_cell,
    column_index,
    table_rows,
    to_number,
)

from ._dates import parse_roc_date, parse_roc_period

HOST: Final = "www.twse.com.tw"
_BASE: Final = f"https://{HOST}/rwd/zh/announcement"
DISPOSITION_URL: Final = f"{_BASE}/punish"
ATTENTION_URL: Final = f"{_BASE}/notice"
SECURITY_TYPE: Final = "twse"
_STAT_OK: Final = "ok"
# What TWSE's other reports answer on a request that matches nothing.
_NO_MATCH: Final = "沒有符合條件"


def query(start: date_, end: date_) -> dict[str, str]:
    return {
        "startDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
        "response": "json",
    }


def _data_rows(body: JsonObject, label: str) -> tuple[list[object], list[list[object]]]:
    """`(fields, rows)` of the report. An explicit "nothing matches" answer, or a report that says
    it holds no row, is `NoDataPublishedError`; anything else that is not a whole report is an
    `ExchangeFetchError`."""
    stat = clean_cell(body.get("stat", ""))
    if _NO_MATCH in stat:
        raise NoDataPublishedError
    if stat.lower() != _STAT_OK:
        raise ExchangeFetchError(f"{label}: unexpected stat {stat[:40]!r}")
    fields = as_list(body.get("fields"))
    data = as_list(body.get("data"))
    if not fields or data is None:
        raise ExchangeFetchError(f"{label}: response has no report table")
    rows = table_rows({"data": data})
    if len(rows) != len(data):
        raise ExchangeFetchError(f"{label}: a row of the report is not a list")
    total = body.get("total")
    if isinstance(total, int) and total != len(rows):
        raise ExchangeFetchError(f"{label}: report says {total} rows, holds {len(rows)}")
    if not rows:
        raise NoDataPublishedError
    return fields, rows


def parse_disposition(body: JsonObject) -> list[Row]:
    fields, rows = _data_rows(body, "punish")
    announced = column_index(fields, "公布日期")
    code = column_index(fields, "證券代號")
    name = column_index(fields, "證券名稱")
    condition = column_index(fields, "處置條件")
    period = column_index(fields, "處置起迄時間")
    measure = column_index(fields, "處置措施")
    content = column_index(fields, "處置內容")
    parsed: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, code))
        announced_on = parse_roc_date(cell(row, announced))
        window = parse_roc_period(cell(row, period))
        if not symbol or announced_on is None or window is None:
            raise ExchangeFetchError(f"punish: unreadable row for {symbol or 'a security'}")
        parsed.append(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "company_name": clean_cell(cell(row, name)),
                "announced_date": announced_on.isoformat(),
                "reason": clean_cell(cell(row, condition)),
                "period_start": window[0].isoformat(),
                "period_end": window[1].isoformat(),
                "measure": clean_cell(cell(row, measure)),
                "detail": str(cell(row, content) or "") or None,
                "security_type": SECURITY_TYPE,
            }
        )
    return parsed


def parse_attention(body: JsonObject) -> list[Row]:
    fields, rows = _data_rows(body, "notice")
    code = column_index(fields, "證券代號")
    name = column_index(fields, "證券名稱")
    info = column_index(fields, "注意交易資訊")
    day = column_index(fields, "日期")
    close = column_index(fields, "收盤價")
    pe = column_index(fields, "本益比")
    parsed: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, code))
        flagged = parse_roc_date(cell(row, day))
        if not symbol or flagged is None:
            raise ExchangeFetchError(f"notice: unreadable row for {symbol or 'a security'}")
        parsed.append(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "company_name": clean_cell(cell(row, name)),
                "flagged_date": flagged.isoformat(),
                "reason": clean_cell(cell(row, info)),
                "close": to_number(cell(row, close)) or None,
                "pe": to_number(cell(row, pe)),
                "security_type": SECURITY_TYPE,
            }
        )
    return parsed
