"""Shared building blocks for the TDCC OpenData package — CSV coercion helpers, the per-
dataset config record every dataset definition fills in, and the `SourceResult` envelope
constructor.

Every TDCC OpenData endpoint (`getOD.ashx?id=<dataset_id>`) returns a plain CSV file, not a
JSON envelope — confirmed live against the real upstream (`Content-Type: text/csv;
charset=UTF-8`, though most datasets are actually UTF-8-with-BOM and a handful of the e-voting
datasets, ids `6-1`/`6-2`, are genuinely Big5-encoded despite that response header — verified
byte-for-byte, not assumed). `_DatasetConfig.encoding` names which decode a given dataset
actually needs.

Most TDCC date-like fields are already plain Gregorian `YYYYMMDD` strings (`資料日期`,
`更新日期`, `淨值日`, `股東會日期`, `投票起日`/`投票迄日`) — unlike MOPS's ROC-date fields, these
need no calendar conversion. The one ROC-based field this package touches is dataset `2-1`'s
`資料年月` (5-digit ROC year-month, e.g. `"11503"` -> ROC 115 = Gregorian 2026, month 03).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from schemas.enums import DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import SourceHealth, SourceResult

_MIN_ROC_YEARMONTH_LEN = 3
_MIN_YYYYMMDD_LEN = 8


def _to_float(value: str, default: float = 0.0) -> float:
    """Coerce a raw CSV cell to float, tolerating thousands-separator commas."""
    stripped = value.strip().replace(",", "")
    if not stripped:
        return default
    try:
        return float(stripped)
    except ValueError:
        return default


def _to_int(value: str, default: int = 0) -> int:
    return int(_to_float(value, float(default)))


def _yyyymmdd_to_date(raw: str) -> date | None:
    """Plain Gregorian `YYYYMMDD` string (e.g. "20260812") -> `date`. Most TDCC date fields use
    this form directly — no ROC-calendar conversion needed, unlike MOPS's date fields."""
    digits = raw.strip()
    if len(digits) < _MIN_YYYYMMDD_LEN or not digits.isdigit():
        return None
    try:
        return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _roc_yearmonth_to_iso(roc: str) -> str | None:
    """5-digit ROC year-month string (e.g. "11503") -> ISO "YYYY-MM" (e.g. "2026-03"). The one
    ROC-based field this package's datasets carry (id `2-1`'s `資料年月`)."""
    digits = roc.strip()
    if len(digits) < _MIN_ROC_YEARMONTH_LEN or not digits.isdigit():
        return None
    year = int(digits[:-2]) + 1911
    month = digits[-2:]
    return f"{year:04d}-{month}"


def parse_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """Parse a TDCC OpenData CSV body into `(header, rows)`, each row a `{header_name: cell}`
    dict. Cell values are NOT stripped here — several upstream columns (symbol/fund codes,
    Chinese names) carry meaningful trailing padding spaces that individual field readers
    decide whether to strip, mirroring `sources/mops.py`'s own "don't over-normalize" stance."""
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    header = [cell.strip() for cell in rows[0]]
    body: list[dict[str, str]] = []
    for raw_row in rows[1:]:
        if not raw_row or all(cell.strip() == "" for cell in raw_row):
            continue
        body.append(dict(zip(header, raw_row, strict=False)))
    return header, body


@dataclass(frozen=True)
class _DatasetConfig:
    dataset_id: str  # the `id` query param, e.g. "1-1"
    data_class: DataClass
    timing_class: TimingClass
    caveat: str
    row_key: str  # key under which normalized rows are stored in SourceResult.payload
    row_mapper: Callable[[list[dict[str, str]], Symbol | None], list[dict[str, object]]]
    symbol_column: str | None  # upstream CSV header name to filter/match on; None => market-level
    encoding: str = "utf-8-sig"  # this package's one confirmed exception: ids 6-1/6-2 are Big5
    market: Market = Market.TW  # every dataset in this package's first pass is TW-scoped


def _result(
    *,
    data_class: DataClass,
    timing_class: TimingClass,
    caveat: str,
    payload: dict[str, object],
    health: SourceHealth,
) -> SourceResult:
    """Build the `SourceResult` envelope common to every TDCC fetch outcome."""
    return SourceResult(
        source_id="TDCC",
        data_class=data_class,
        timing_class=timing_class,
        fetched_at=datetime.now(UTC),
        currentness_caveat=caveat,
        payload=payload,
        health=health,
    )


__all__ = [
    "_DatasetConfig",
    "_result",
    "_roc_yearmonth_to_iso",
    "_to_float",
    "_to_int",
    "_yyyymmdd_to_date",
    "parse_csv",
]
