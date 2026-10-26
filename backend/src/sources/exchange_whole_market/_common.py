"""Shared building blocks of the exchange whole-market adapters: number parsing, the response
envelope checks, the endpoint record, and the error type the HTTP client and the parsers raise."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_
from typing import Final, NamedTuple, cast

import structlog

logger = structlog.get_logger(__name__)

type Row = dict[str, object]
type JsonObject = dict[str, object]

# Cells the exchanges print where a number does not exist (halted, no trade, loss-making PER).
_MISSING_CELLS: Final = frozenset({"", "-", "--", "---", "X", "N/A"})
_TAG: Final = re.compile(r"<[^>]*>")

TAIWAN_MARKET: Final = "TW"


class ExchangeFetchError(Exception):
    """A request or a response the adapter cannot use. `detail` is safe to journal: it names the
    dataset and the failure, never a payload."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NoDataPublished(Exception):  # noqa: N818 - an answer, not an error
    """The exchange answered, explicitly, that it published nothing for the requested date (TWSE's
    "沒有符合條件的資料" `stat`, a TPEx report with no rows). Distinct from `ExchangeFetchError`: a
    parser raises it for that one answer only, never for a block page, a bad status or a layout
    change, so the adapter can tell a market closure from trouble."""


@dataclass(frozen=True)
class Endpoint:
    """One exchange endpoint for one dataset: how to ask for a date, how to read the answer."""

    label: str  # e.g. "T86" - for the journal and the log
    url: str
    query: Callable[[date_], dict[str, str]]
    parse: Callable[[JsonObject, date_], list[Row]]


def clean_cell(value: object) -> str:
    """A table cell as text: HTML tags and surrounding (incl. full-width) blanks removed."""
    return _TAG.sub("", str(value)).strip()


def to_number(value: object) -> float | None:
    """`'1,234.5'` -> 1234.5; `'--'`, blanks and anything unparseable -> None."""
    text = clean_cell(value).replace(",", "")
    if text in _MISSING_CELLS:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_count(value: object) -> int | None:
    number = to_number(value)
    return None if number is None else round(number)


def as_object(value: object) -> JsonObject | None:
    return cast("JsonObject", value) if isinstance(value, dict) else None


def as_list(value: object) -> list[object] | None:
    return cast("list[object]", value) if isinstance(value, list) else None


def table_rows(table: JsonObject) -> list[list[object]]:
    """The `data` rows of a table that are themselves lists (anything else is skipped)."""
    data = as_list(table.get("data")) or []
    rows: list[list[object]] = []
    for raw in data:
        row = as_list(raw)
        if row is not None:
            rows.append(row)
    return rows


def column_index(fields: list[object], *names: str) -> int:
    """Index of the first column whose cleaned header equals one of `names`."""
    cleaned = [clean_cell(field) for field in fields]
    for name in names:
        if name in cleaned:
            return cleaned.index(name)
    raise ExchangeFetchError(f"expected column {names[0]!r} is missing from the response")


def cell(row: list[object], index: int) -> object:
    return row[index] if index < len(row) else None


def check_response_date(body: JsonObject, run_date: date_, label: str) -> None:
    """The root `date` (`YYYYMMDD`) must be the requested one: an exchange that answers with a
    different day must never be filed under the requested date."""
    answered = body.get("date")
    if answered is not None and str(answered) != run_date.strftime("%Y%m%d"):
        raise ExchangeFetchError(f"{label}: answered for {answered}, not {run_date.isoformat()}")


def is_symbol(code: str) -> bool:
    """A real security code starts with a digit (FinMind's own rule for its whole-market dump)."""
    return code[:1].isdigit()


class BarColumns(NamedTuple):
    """Column indexes of a daily-quotes table."""

    code: int
    open: int
    high: int
    low: int
    close: int
    volume: int
    turnover: int


def bars_from_rows(rows: list[list[object]], run_date: date_, columns: BarColumns) -> list[Row]:
    """EOD bars (FinMind's price row shape). A row with no valid price (a stock that did not
    trade prints '--'), no volume, or a code that is not a security (an index label) is skipped."""
    bars: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, columns.code))
        prices = [
            to_number(cell(row, i))
            for i in (columns.open, columns.high, columns.low, columns.close)
        ]
        shares = to_count(cell(row, columns.volume))
        if not is_symbol(symbol) or any(p is None or p <= 0 for p in prices) or shares is None:
            continue
        bars.append(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "trade_date": run_date.isoformat(),
                "open": prices[0],
                "high": prices[1],
                "low": prices[2],
                "close": prices[3],
                "volume": shares,
                "turnover": to_count(cell(row, columns.turnover)),
            }
        )
    return bars


def flows_from_rows(
    rows: list[list[object]],
    run_date: date_,
    *,
    code: int,
    columns: dict[str, tuple[int, int]],
    report: str,
) -> list[Row]:
    """Institutional flow in FinMind's long shape: one row per stock per investor category, from
    `columns` = `{investor: (buy column, sell column)}`. A stock with an unreadable count is
    dropped whole (logged) rather than written with some categories missing."""
    flows: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, code))
        if not is_symbol(symbol):
            continue
        counts = {
            investor: (to_count(cell(row, buy)), to_count(cell(row, sell)))
            for investor, (buy, sell) in columns.items()
        }
        if any(b is None or s is None for b, s in counts.values()):
            logger.warning("exchange_row_dropped", report=report, symbol=symbol)
            continue
        flows.extend(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "trade_date": run_date.isoformat(),
                "investor": investor,
                "buy": buy,
                "sell": sell,
            }
            for investor, (buy, sell) in counts.items()
        )
    return flows


def margin_row(
    symbol: str,
    run_date: date_,
    blocks: tuple[list[int], list[int]],
    *,
    offset: int,
    note: str | None,
) -> Row:
    """A margin/short row. `blocks` is (margin purchase, short sale), each in this order: buy,
    sell, cash (or securities) repayment, yesterday's balance, today's balance, next-day limit."""
    margin, short = blocks
    return {
        "symbol": symbol,
        "market": TAIWAN_MARKET,
        "trade_date": run_date.isoformat(),
        "margin_purchase_buy": margin[0],
        "margin_purchase_sell": margin[1],
        "margin_purchase_cash_repayment": margin[2],
        "margin_purchase_yesterday_balance": margin[3],
        "margin_purchase_today_balance": margin[4],
        "margin_purchase_limit": margin[5],
        "short_sale_buy": short[0],
        "short_sale_sell": short[1],
        "short_sale_cash_repayment": short[2],
        "short_sale_yesterday_balance": short[3],
        "short_sale_today_balance": short[4],
        "short_sale_limit": short[5],
        "offset_loan_and_short": offset,
        "note": note,
    }


class ValuationColumns(NamedTuple):
    code: int
    dividend_yield: int
    per: int
    pbr: int


def valuations_from_rows(
    rows: list[list[object]], run_date: date_, columns: ValuationColumns
) -> list[Row]:
    """PER/PBR/yield rows; a '-' (loss-making PER, no dividend) is 0.0, as FinMind's mapper."""
    ratios: list[Row] = []
    for row in rows:
        symbol = clean_cell(cell(row, columns.code))
        if not is_symbol(symbol):
            continue
        ratios.append(
            {
                "symbol": symbol,
                "market": TAIWAN_MARKET,
                "trade_date": run_date.isoformat(),
                "dividend_yield": to_number(cell(row, columns.dividend_yield)) or 0.0,
                "per": to_number(cell(row, columns.per)) or 0.0,
                "pbr": to_number(cell(row, columns.pbr)) or 0.0,
            }
        )
    return ratios


__all__ = [
    "TAIWAN_MARKET",
    "BarColumns",
    "Endpoint",
    "ExchangeFetchError",
    "JsonObject",
    "Row",
    "ValuationColumns",
    "as_list",
    "as_object",
    "bars_from_rows",
    "cell",
    "check_response_date",
    "clean_cell",
    "column_index",
    "flows_from_rows",
    "is_symbol",
    "margin_row",
    "table_rows",
    "to_count",
    "to_number",
    "valuations_from_rows",
]
