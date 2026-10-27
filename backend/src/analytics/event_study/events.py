"""Turn stored event rows into dated `StudyEvent`s, one pure function per source dataset.

Every extractor takes rows in snapshot order (oldest first). The stored feeds are daily snapshots
that repeat the same announcement for days, so duplicates collapse on a natural key and a later
snapshot's values win (a period end that moved after a halt, a corrected filing).
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Sequence
from datetime import date as date_
from datetime import timedelta
from typing import Final

from analytics.event_study.models import EventType, ExtractionResult, StudyEvent
from analytics.symbol_universe import is_scoreable_symbol
from schemas.capabilities import (
    AttentionStockRow,
    CapitalReductionReferencePriceRow,
    DispositionEventRow,
    MaterialAnnouncementRow,
)

_ROC_OFFSET: Final[int] = 1911
# "115/08/10~115/10/01" with an ASCII or full-width tilde / dash between the two dates.
_FULLWIDTH_TILDE = chr(0xFF5E)
_FULLWIDTH_HYPHEN = chr(0xFF0D)
_RANGE_SEPARATORS = f"~{_FULLWIDTH_TILDE}{_FULLWIDTH_HYPHEN}-"
_ROC_DATE = r"(\d{2,3})/(\d{1,2})/(\d{1,2})"
_ROC_RANGE = re.compile(rf"{_ROC_DATE}\s*[{_RANGE_SEPARATORS}]\s*{_ROC_DATE}")
_PLANNED_PERIOD = re.compile(r"預定買回(?:之)?期間\s*[:：]")
_ACTUAL_PERIOD = re.compile(r"本次實際買回期間\s*[:：]")
# A filing that is not the board's decision to start a buyback.
_NOT_A_START = ("註銷", "減資", "執行", "屆滿", "代子公司")
_END_MARKERS = ("執行完畢", "屆滿")
_SUBSIDIARY = "代子公司"


class EventDateFilter:
    """The caller's optional symbol and event-date range, applied before any price work."""

    __slots__ = ("end", "start", "symbol")

    def __init__(
        self, *, symbol: str | None = None, start: date_ | None = None, end: date_ | None = None
    ) -> None:
        self.symbol = symbol
        self.start = start
        self.end = end

    def keeps(self, symbol: str, event_date: date_) -> bool:
        if self.symbol is not None and symbol != self.symbol:
            return False
        if self.start is not None and event_date < self.start:
            return False
        return self.end is None or event_date <= self.end


def _roc_date(year: str, month: str, day: str) -> date_ | None:
    try:
        return date_(int(year) + _ROC_OFFSET, int(month), int(day))
    except ValueError:
        return None


def parse_roc_range_after(marker: re.Pattern[str], text: str) -> tuple[date_, date_] | None:
    """The first `YYY/M/D~YYY/M/D` range that follows `marker` on the same line, as dates."""
    found = marker.search(text)
    if found is None:
        return None
    line = text[found.end() :].split("\n", 1)[0]
    parts = _ROC_RANGE.search(line)
    if parts is None:
        return None
    start = _roc_date(parts[1], parts[2], parts[3])
    end = _roc_date(parts[4], parts[5], parts[6])
    return (start, end) if start is not None and end is not None else None


def _finish(
    candidates: dict[tuple[str, date_], StudyEvent],
    flt: EventDateFilter,
    tally: ExtractionResult,
) -> ExtractionResult:
    """Apply the caller's filter to the candidates; `tally` carries the exclusion counts so far."""
    kept: list[StudyEvent] = []
    outside = 0
    for event in candidates.values():
        if flt.keeps(event.symbol, event.event_date):
            kept.append(event)
        else:
            outside += 1
    kept.sort(key=lambda e: (e.event_date, e.symbol))
    return ExtractionResult(
        events=tuple(kept),
        rows_seen=tally.rows_seen,
        excluded_non_equity=tally.excluded_non_equity,
        excluded_extended=tally.excluded_extended,
        excluded_unparsed=tally.excluded_unparsed,
        excluded_outside_filter=outside,
    )


def disposition_events(
    rows: Sequence[DispositionEventRow],
    kind: EventType,
    flt: EventDateFilter,
    calendar: Sequence[date_],
) -> ExtractionResult:
    """`disposition_start`: the first day of the disposition period. `disposition_end`: the
    first day after the period's last day (the first day of normal trading again); a period that
    is followed immediately by another one for the same stock is an extension, not a release, and
    is left out. `calendar` is the sorted list of stored trading days, used to find that day."""
    periods: dict[tuple[str, date_], DispositionEventRow] = {}
    non_equity = 0
    for row in rows:
        if not is_scoreable_symbol(row.symbol):
            non_equity += 1
            continue
        periods[(row.symbol, row.period_start)] = row
    if kind is EventType.DISPOSITION_START:
        starts = {
            key: StudyEvent(row.symbol, row.company_name or None, row.period_start)
            for key, row in periods.items()
        }
        return _finish(starts, flt, ExtractionResult((), len(rows), non_equity))

    periods_by_symbol: dict[str, list[DispositionEventRow]] = {}
    for period in periods.values():
        periods_by_symbol.setdefault(period.symbol, []).append(period)
    releases: dict[tuple[str, date_], StudyEvent] = {}
    extended = 0
    for row in periods.values():
        release_from = row.period_end + timedelta(days=1)
        idx = bisect_left(calendar, release_from)
        first_free_day = calendar[idx] if idx < len(calendar) else release_from
        if any(
            other.period_start > row.period_start and other.period_start <= first_free_day
            for other in periods_by_symbol[row.symbol]
        ):
            extended += 1
            continue
        releases[(row.symbol, release_from)] = StudyEvent(
            row.symbol, row.company_name or None, release_from
        )
    return _finish(
        releases, flt, ExtractionResult((), len(rows), non_equity, excluded_extended=extended)
    )


def attention_events(rows: Sequence[AttentionStockRow], flt: EventDateFilter) -> ExtractionResult:
    """`attention_listed`: the date a stock was put on the attention list (`flagged_date`)."""
    found: dict[tuple[str, date_], StudyEvent] = {}
    non_equity = 0
    for row in rows:
        if not is_scoreable_symbol(row.symbol):
            non_equity += 1
            continue
        found[(row.symbol, row.flagged_date)] = StudyEvent(
            row.symbol, row.company_name or None, row.flagged_date
        )
    return _finish(found, flt, ExtractionResult((), len(rows), non_equity))


def capital_reduction_events(
    rows: Sequence[CapitalReductionReferencePriceRow], flt: EventDateFilter
) -> ExtractionResult:
    """`capital_reduction_resume`: the day trading resumes after a capital reduction (the row's
    `trade_date`, the day the post-reduction reference price applies)."""
    found: dict[tuple[str, date_], StudyEvent] = {}
    non_equity = 0
    for row in rows:
        if not is_scoreable_symbol(row.symbol):
            non_equity += 1
            continue
        found[(row.symbol, row.trade_date)] = StudyEvent(row.symbol, None, row.trade_date)
    return _finish(found, flt, ExtractionResult((), len(rows), non_equity))


def _is_buyback_start(row: MaterialAnnouncementRow) -> bool:
    subject = row.subject
    return (
        "董事會決議" in subject
        and "買回" in subject
        and not any(word in subject for word in _NOT_A_START)
    )


def _is_buyback_end(row: MaterialAnnouncementRow) -> bool:
    subject = row.subject
    return (
        "買回" in subject
        and _SUBSIDIARY not in subject
        and any(word in subject for word in _END_MARKERS)
    )


def buyback_events(
    rows: Sequence[MaterialAnnouncementRow], kind: EventType, flt: EventDateFilter
) -> ExtractionResult:
    """`buyback_start`: first day of the planned buy-back period in the board's resolution.
    `buyback_end`: last day of the actual buy-back period in a completion / period-expiry
    filing. Both come from the material-announcement text, so only filings whose date range
    parses are used (the rest are counted as unparsed)."""
    start_kind = kind is EventType.BUYBACK_START
    marker = _PLANNED_PERIOD if start_kind else _ACTUAL_PERIOD
    found: dict[tuple[str, date_], StudyEvent] = {}
    non_equity = 0
    unparsed = 0
    for row in rows:
        if not (_is_buyback_start(row) if start_kind else _is_buyback_end(row)):
            continue
        if not is_scoreable_symbol(row.symbol):
            non_equity += 1
            continue
        period = parse_roc_range_after(marker, row.detail)
        if period is None:
            unparsed += 1
            continue
        when = period[0] if start_kind else period[1]
        found[(row.symbol, when)] = StudyEvent(row.symbol, row.company_name or None, when)
    return _finish(
        found, flt, ExtractionResult((), len(rows), non_equity, excluded_unparsed=unparsed)
    )
