"""TPEx (OTC market) history of the disposition and attention-stock announcements.

`www.tpex.org.tw/www/zh-tw/bulletin/{disposal,attention}?startDate=YYYY/MM/DD&endDate=...`. The
answer is `{"stat": "ok", "date": "YYYYMMDD~YYYYMMDD", "tables": [{...}]}`; the report's own
subtitle says how many rows it holds (`總累計次數: N`), which is checked so a cut-short answer is
an error and never a quieter month. The disposition report prints one placeholder row per day
without a disposition (`本日無處置資料`); those are not rows.

Each parser returns rows in the stored `DispositionEventRow` / `AttentionStockRow` shape.
"""

from __future__ import annotations

import re
from datetime import date as date_
from typing import Final

from sources.exchange_whole_market._common import (
    TAIWAN_MARKET,
    ExchangeFetchError,
    JsonObject,
    NoDataPublishedError,
    Row,
    as_list,
    as_object,
    cell,
    clean_cell,
    column_index,
    table_rows,
    to_number,
)

from ._dates import parse_roc_date, parse_roc_period

HOST: Final = "www.tpex.org.tw"
_BASE: Final = f"https://{HOST}/www/zh-tw/bulletin"
DISPOSITION_URL: Final = f"{_BASE}/disposal"
ATTENTION_URL: Final = f"{_BASE}/attention"
SECURITY_TYPE: Final = "tpex"
_STAT_OK: Final = "ok"
_TOTAL_COUNT: Final = re.compile(r"總累計次數\s*[:：]\s*(\d+)")
# The company name carries the link to its page: '順藥(../../mainboard/listed/company-...)'.
_NAME_LINK: Final = re.compile(r"\((?:\.\./|https?://)[^)]*\)\s*$")


def query(start: date_, end: date_) -> dict[str, str]:
    return {
        "startDate": start.strftime("%Y/%m/%d"),
        "endDate": end.strftime("%Y/%m/%d"),
        "response": "json",
    }


def _company_name(value: object) -> str:
    return _NAME_LINK.sub("", clean_cell(value)).strip()


def _report(
    body: JsonObject, start: date_, end: date_, label: str
) -> tuple[list[object], list[list[object]], int | None]:
    stat = clean_cell(body.get("stat", ""))
    if stat.lower() != _STAT_OK:
        raise ExchangeFetchError(f"{label}: unexpected stat {stat[:40]!r}")
    asked = f"{start:%Y%m%d}~{end:%Y%m%d}"
    answered = body.get("date")
    if answered is not None and str(answered) != asked:
        raise ExchangeFetchError(f"{label}: answered for {answered}, not {asked}")
    tables = as_list(body.get("tables")) or []
    table = as_object(tables[0]) if tables else None
    fields = as_list(table.get("fields")) if table else None
    if table is None or not fields:
        raise ExchangeFetchError(f"{label}: response has no report table")
    subtitle = _TOTAL_COUNT.search(clean_cell(table.get("subtitle", "")))
    return fields, table_rows(table), int(subtitle[1]) if subtitle else None


def _checked(rows: list[list[object]], expected: int | None, label: str) -> list[list[object]]:
    if expected is not None and expected != len(rows):
        raise ExchangeFetchError(f"{label}: report says {expected} rows, holds {len(rows)}")
    if not rows:
        raise NoDataPublishedError
    return rows


def parse_disposition(body: JsonObject, start: date_, end: date_) -> list[Row]:
    fields, all_rows, expected = _report(body, start, end, "disposal")
    code = column_index(fields, "證券代號")
    announced = column_index(fields, "公布日期")
    name = column_index(fields, "證券名稱")
    period = column_index(fields, "處置起訖時間")
    reason = column_index(fields, "處置原因")
    measure = column_index(fields, "處置措施")
    content = column_index(fields, "處置內容")
    # A placeholder row of a day without a disposition has no security code.
    rows = _checked([r for r in all_rows if clean_cell(cell(r, code))], expected, "disposal")
    parsed: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, code))
        announced_on = parse_roc_date(cell(row, announced))
        window = parse_roc_period(cell(row, period))
        if announced_on is None or window is None:
            raise ExchangeFetchError(f"disposal: unreadable row for {symbol}")
        parsed.append(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "company_name": _company_name(cell(row, name)),
                "announced_date": announced_on.isoformat(),
                "reason": clean_cell(cell(row, reason)),
                "period_start": window[0].isoformat(),
                "period_end": window[1].isoformat(),
                # TPEx has no measures / reasons split: the condition text is `measure`, as in the
                # daily feed.
                "measure": clean_cell(cell(row, content)) or clean_cell(cell(row, measure)),
                "detail": None,
                "security_type": SECURITY_TYPE,
            }
        )
    return parsed


def parse_attention(body: JsonObject, start: date_, end: date_) -> list[Row]:
    fields, all_rows, expected = _report(body, start, end, "attention")
    rows = _checked(all_rows, expected, "attention")
    code = column_index(fields, "證券代號")
    name = column_index(fields, "證券名稱")
    info = column_index(fields, "注意交易資訊")
    day = column_index(fields, "公告日期")
    close = column_index(fields, "收盤價")
    pe = column_index(fields, "本益比")
    parsed: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, code))
        flagged = parse_roc_date(cell(row, day))
        if not symbol or flagged is None:
            raise ExchangeFetchError(f"attention: unreadable row for {symbol or 'a security'}")
        parsed.append(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "company_name": _company_name(cell(row, name)),
                "flagged_date": flagged.isoformat(),
                "reason": clean_cell(cell(row, info)),
                "close": to_number(cell(row, close)),
                "pe": to_number(cell(row, pe)),
                "security_type": SECURITY_TYPE,
            }
        )
    return parsed
