"""What a daily snapshot of the attention / disposition lists holds, cut from the exchanges' range
reports. Pure functions shared by the daily attention job (`ingest/jobs/risk_lists.py`) and the
operator history rebuild (`ingest/event_history.py`), so the live and the rebuilt series are one
series by construction (module 22 section 1f)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date as date_
from typing import Final

type Row = dict[str, object]

# The two exchanges a whole-market snapshot is made of (`security_type` of a row).
EXCHANGES: Final = frozenset({"twse", "tpex"})


def disposition_snapshot(history: Iterable[Row], day: date_) -> list[Row]:
    """The rows of the disposition list on `day`: announced on or before it, period not over."""
    day_iso = day.isoformat()
    current = {
        (str(row["security_type"]), str(row["symbol"]), str(row["period_start"])): row
        for row in history
        if str(row["announced_date"]) <= day_iso <= str(row["period_end"])
    }
    return [current[key] for key in sorted(current)]


def attention_snapshot(history: Iterable[Row], day: date_, previous_day: date_) -> list[Row]:
    """The rows of the attention list on `day`: the flags announced on `day` and on the trading
    day before it, one per security and announcement day."""
    wanted = {day.isoformat(), previous_day.isoformat()}
    current = {
        (str(row["flagged_date"]), str(row["security_type"]), str(row["symbol"])): row
        for row in history
        if str(row["flagged_date"]) in wanted
    }
    return [current[key] for key in sorted(current)]


def newest_by_exchange(history: Iterable[Row], date_key: str) -> dict[str, date_]:
    """The newest announcement day each exchange's rows reach (no row, not listed)."""
    newest: dict[str, str] = {}
    for row in history:
        kind, day = str(row["security_type"]), str(row[date_key])
        newest[kind] = max(newest.get(kind, day), day)
    return {kind: date_.fromisoformat(day) for kind, day in newest.items()}


def published_through(history: Iterable[Row], date_key: str) -> date_ | None:
    """The last announcement day BOTH exchanges' rows reach (the older of the two newest days), or
    None without any row: past that day the reports may simply not have been posted yet."""
    newest = newest_by_exchange(history, date_key)
    return min(newest.values()) if newest else None


def exchanges_of(rows: Iterable[Row]) -> frozenset[str]:
    """The exchanges (`security_type`) a set of snapshot rows holds."""
    return frozenset(str(row.get("security_type")) for row in rows) & EXCHANGES


__all__ = [
    "EXCHANGES",
    "Row",
    "attention_snapshot",
    "disposition_snapshot",
    "exchanges_of",
    "newest_by_exchange",
    "published_through",
]
