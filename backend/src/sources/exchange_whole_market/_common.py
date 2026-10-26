"""Shared building blocks of the exchange whole-market adapters: number parsing, the response
envelope checks, the endpoint record, and the error type the HTTP client and the parsers raise."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_
from typing import Final, cast

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


__all__ = [
    "TAIWAN_MARKET",
    "Endpoint",
    "ExchangeFetchError",
    "JsonObject",
    "Row",
    "as_list",
    "as_object",
    "cell",
    "check_response_date",
    "clean_cell",
    "column_index",
    "is_symbol",
    "table_rows",
    "to_count",
    "to_number",
]
