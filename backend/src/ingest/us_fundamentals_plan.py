"""What `job_us_fundamentals` needs to fetch this week, so it does not refetch everything.

The job used to pull all nine capabilities for every symbol every run: 2,123 symbols x 9 calls at
about 2.2 s each is roughly twelve hours, to refresh data most of which has not changed (a
company's statements change once a quarter). Each capability now has a freshness window; a symbol's
capability is refetched only when the stored copy is older than that. The date each (symbol,
capability) was last fetched successfully is kept in its own small dataset, so an attempt that
legitimately returned nothing (a fund has no statements) is not retried every week, and a failed
attempt (no record) is retried at the next run.

OTC symbols get a longer window than listed ones. Yahoo has sparse, slow-moving data for most of
them (in the latest US scores about 8% of OTC rows use a fundamentals-based factor, against 42% of
listed rows), so refreshing them monthly keeps what is there at a fraction of the calls.

The first run after this lands has no state file. It starts from what the existing partitions say:
a symbol that has rows in a capability's latest partition counts as fetched on that partition's
date, so the run in progress when this shipped is not repeated in full.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_
from pathlib import Path

import duckdb

from ingest.store import ParquetStore
from schemas.enums import Capability, Market

STATE_DATASET = "us_fundamentals_state"


@dataclass(frozen=True)
class Freshness:
    """Days a fetched copy stays good, for a listed symbol and for an OTC one."""

    listed_days: int
    otc_days: int


# A weekly run is exactly seven days apart; `due` allows one day of slack so a window of N days
# is honoured by the run N or more days later, not one run later still.
_SLACK_DAYS = 1

_STATEMENTS = Freshness(listed_days=25, otc_days=45)  # change when a company files (quarterly)
_WEEKLY = Freshness(listed_days=7, otc_days=30)  # consensus, holders, insiders, dividends, splits
_RARE = Freshness(listed_days=90, otc_days=90)  # sector classification

FRESHNESS: dict[Capability, Freshness] = {
    Capability.FINANCIALS: _STATEMENTS,
    Capability.BALANCE_SHEET: _STATEMENTS,
    Capability.CASH_FLOW_STATEMENT: _STATEMENTS,
    Capability.DIVIDENDS: _WEEKLY,
    Capability.SPLIT_PRICE: _WEEKLY,
    Capability.ANALYST_CONSENSUS: _WEEKLY,
    Capability.INSTITUTIONAL_HOLDERS: _WEEKLY,
    Capability.INSIDER_ACTIVITY: _WEEKLY,
    Capability.SECTOR_CLASS: _RARE,
}

FetchState = dict[tuple[str, str], date_]


def is_otc(exchange: str | None) -> bool:
    return bool(exchange) and str(exchange).upper().startswith("OTC")


def listed_first(symbols: list[str], exchanges: dict[str, str]) -> list[str]:
    """Listed symbols before OTC ones (stable within each), so an interrupted run has already
    refreshed the names most people look at."""
    return sorted(symbols, key=lambda s: is_otc(exchanges.get(s)))


def due_capabilities(
    state: FetchState,
    symbol: str,
    exchange: str | None,
    run_date: date_,
    capabilities: list[Capability],
) -> list[Capability]:
    """The capabilities of `symbol` whose stored copy is missing or older than its window."""
    due: list[Capability] = []
    for cap in capabilities:
        fetched_on = state.get((symbol, cap.value))
        if fetched_on is None:
            due.append(cap)
            continue
        window = FRESHNESS[cap]
        days = window.otc_days if is_otc(exchange) else window.listed_days
        if (run_date - fetched_on).days >= days - _SLACK_DAYS:
            due.append(cap)
    return due


def _read_pairs(files: list[Path], sql: str) -> list[tuple[object, ...]]:
    if not files:
        return []
    con = duckdb.connect()
    try:
        return con.execute(sql, [[str(f) for f in files]]).fetchall()
    except duckdb.Error:  # a partition without the expected column counts as "knows nothing"
        return []
    finally:
        con.close()


def load_fetch_state(
    store: ParquetStore, run_date: date_, datasets: dict[Capability, str]
) -> FetchState:
    """When each (symbol, capability) was last fetched, from the state dataset and, for pairs it
    does not know, from the capability datasets' own latest partitions (see the module docs)."""
    market = Market.US.value
    state: FetchState = {}
    latest = store.latest_partition_on_or_before(STATE_DATASET, market, run_date)
    if latest is not None:
        for symbol, capability, fetched_on in _read_pairs(
            latest[1],
            "SELECT symbol, capability, fetched_on FROM read_parquet(?, union_by_name=True)",
        ):
            state[(str(symbol), str(capability))] = date_.fromisoformat(str(fetched_on))
    for cap, dataset in datasets.items():
        found = store.latest_partition_on_or_before(dataset, market, run_date)
        if found is None:
            continue
        partition_date, files = found
        for (symbol,) in _read_pairs(
            files, "SELECT DISTINCT symbol FROM read_parquet(?, union_by_name=True)"
        ):
            state.setdefault((str(symbol), cap.value), partition_date)
    return state
