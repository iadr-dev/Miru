"""The event-study store loader over daily-snapshot partitions (characterization).

Pins what the loader does with the stored `attention_stocks` / `disposition_events` snapshots, so
a longer history written in the same row shape and partition keys is read with no loader change:
every stored snapshot is read oldest first, an announcement repeated by many snapshots is one
event, a later snapshot's values replace an earlier one's, and the coverage names the first and
last snapshot read.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from analytics.event_study.events import EventDateFilter
from analytics.event_study.loader import load_snapshot_events, snapshot_dates
from analytics.event_study.models import EventType
from ingest.store import ParquetStore

MARKET = "TW"
CALENDAR = [date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8, 9, 10, 11, 14)]


def _disposition(symbol: str, announced: str, start: str, end: str, **extra: object) -> dict:
    return {
        "symbol": symbol,
        "market": MARKET,
        "company_name": "name",
        "announced_date": announced,
        "reason": "r",
        "period_start": start,
        "period_end": end,
        "measure": "m",
        "detail": None,
        "security_type": "twse",
        **extra,
    }


def _attention(symbol: str, flagged: str) -> dict:
    return {
        "symbol": symbol,
        "market": MARKET,
        "company_name": "name",
        "flagged_date": flagged,
        "reason": "r",
        "close": 10.0,
        "pe": None,
        "security_type": "tpex",
    }


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path)


def test_an_announcement_repeated_by_many_snapshots_is_one_event(store: ParquetStore) -> None:
    row = _disposition("2330", "2026-09-02", "2026-09-03", "2026-09-09")
    for day in (date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7)):
        store.write_partition("disposition_events", MARKET, day, [row])

    result, coverage = load_snapshot_events(
        store, EventType.DISPOSITION_START, EventDateFilter(), CALENDAR
    )

    assert [(e.symbol, e.event_date) for e in result.events] == [("2330", date(2026, 9, 3))]
    assert coverage.dataset == "disposition_events"
    assert (coverage.first_snapshot, coverage.last_snapshot) == (
        date(2026, 9, 3),
        date(2026, 9, 7),
    )
    assert coverage.n_snapshots == 3


def test_a_later_snapshot_moves_the_period_end(store: ParquetStore) -> None:
    store.write_partition(
        "disposition_events",
        MARKET,
        date(2026, 9, 3),
        [_disposition("2330", "2026-09-02", "2026-09-03", "2026-09-09")],
    )
    store.write_partition(
        "disposition_events",
        MARKET,
        date(2026, 9, 8),
        [_disposition("2330", "2026-09-02", "2026-09-03", "2026-09-10")],
    )

    result, _ = load_snapshot_events(store, EventType.DISPOSITION_END, EventDateFilter(), CALENDAR)

    assert [e.event_date for e in result.events] == [date(2026, 9, 11)]


def test_attention_events_are_one_per_symbol_and_flagged_day(store: ParquetStore) -> None:
    store.write_partition(
        "attention_stocks",
        MARKET,
        date(2026, 9, 3),
        [_attention("3374", "2026-09-02"), _attention("3374", "2026-09-03")],
    )
    store.write_partition(
        "attention_stocks",
        MARKET,
        date(2026, 9, 4),
        [_attention("3374", "2026-09-03"), _attention("3441", "2026-09-04")],
    )

    result, coverage = load_snapshot_events(
        store, EventType.ATTENTION_LISTED, EventDateFilter(), CALENDAR
    )

    assert [(e.symbol, e.event_date) for e in result.events] == [
        ("3374", date(2026, 9, 2)),
        ("3374", date(2026, 9, 3)),
        ("3441", date(2026, 9, 4)),
    ]
    assert coverage.n_snapshots == 2
    assert snapshot_dates(store, "attention_stocks") == [date(2026, 9, 3), date(2026, 9, 4)]


def test_warrants_never_become_events(store: ParquetStore) -> None:
    store.write_partition(
        "disposition_events",
        MARKET,
        date(2026, 9, 3),
        [
            _disposition("2330", "2026-09-02", "2026-09-03", "2026-09-09"),
            _disposition("03123P", "2026-09-02", "2026-09-03", "2026-09-09"),
            _disposition("24552", "2026-09-02", "2026-09-03", "2026-09-09"),
        ],
    )

    result, _ = load_snapshot_events(
        store, EventType.DISPOSITION_START, EventDateFilter(), CALENDAR
    )

    assert [e.symbol for e in result.events] == ["2330"]
    assert result.excluded_non_equity == 2


def test_a_store_without_snapshots_reads_nothing(store: ParquetStore) -> None:
    result, coverage = load_snapshot_events(
        store, EventType.ATTENTION_LISTED, EventDateFilter(), CALENDAR
    )

    assert result.events == ()
    assert coverage.first_snapshot is None
    assert coverage.n_snapshots == 0
