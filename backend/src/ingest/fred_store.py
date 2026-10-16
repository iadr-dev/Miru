"""The US macro Parquet datasets: their names, layout and the read side the tools use.

Layout (market `US`, one partition per ingest run date, see `ingest/jobs/fred.py`):

- `fred_observations` - a **full snapshot** of every catalog series' stored window (five years)
  under every stored units transform, one row per `(series_id, units, observation_date)`. A run that
  changes nothing writes no new partition, so a reader just takes the newest partition: no
  cross-partition merge, and a revised value is visible exactly when its snapshot lands. Older
  snapshots stay on disk as the revision trail (`realtime_start` says when each value was current).
- `fred_series_meta` - one row per series, written on **every** successful run, so the newest
  partition's date is "when the data was last checked" even on a day nothing changed.
- `fred_release_dates` - the release calendar (recent and upcoming) for the releases the catalog's
  series belong to, replaced on every run.
- `fred_vintages` - for the revision-tracked series, every value each recent observation held and
  when (levels only).

Everything here is a pure read of the store; none of it touches the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_

import duckdb

from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import MacroReleaseDateRow, MacroSeriesMetaRow, MacroVintageRow
from schemas.enums import Market

US_MACRO_OBSERVATIONS_DATASET = "fred_observations"
US_MACRO_META_DATASET = "fred_series_meta"
US_MACRO_RELEASE_DATES_DATASET = "fred_release_dates"
US_MACRO_VINTAGES_DATASET = "fred_vintages"

MARKET = Market.US.value


@dataclass(frozen=True)
class ObservationPoint:
    observation_date: date_
    value: float


def latest_partition_date(store: ParquetStore, dataset: str) -> date_ | None:
    dates = store.list_partition_dates(dataset, MARKET)
    return max(dates) if dates else None


def read_latest_partition(
    store: ParquetStore, dataset: str
) -> tuple[date_ | None, list[dict[str, object]]]:
    """The newest non-empty partition of `dataset` and its rows; `(None, [])` if never ingested."""
    latest = latest_partition_date(store, dataset)
    if latest is None:
        return None, []
    return latest, store.read_partition(dataset, MARKET, latest)


def read_meta(store: ParquetStore) -> tuple[date_ | None, dict[str, MacroSeriesMetaRow]]:
    """`(date the data was last checked, metadata by series id)` from the newest meta snapshot."""
    latest, raw = read_latest_partition(store, US_MACRO_META_DATASET)
    return latest, {row.series_id: row for row in rows_from_store(MacroSeriesMetaRow, raw)}


def read_release_dates(store: ParquetStore) -> list[MacroReleaseDateRow]:
    _, raw = read_latest_partition(store, US_MACRO_RELEASE_DATES_DATASET)
    return sorted(
        rows_from_store(MacroReleaseDateRow, raw), key=lambda r: (r.release_date, r.release_name)
    )


def read_vintages(store: ParquetStore, series_id: str) -> list[MacroVintageRow]:
    _, raw = read_latest_partition(store, US_MACRO_VINTAGES_DATASET)
    return rows_from_store(MacroVintageRow, [r for r in raw if r.get("series_id") == series_id])


def read_observations(
    store: ParquetStore,
    pairs: Sequence[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], list[ObservationPoint]]:
    """The newest observations snapshot as `(series_id, units) -> points, oldest first`.

    `pairs` restricts the read to those `(series_id, units)` combinations (filtered inside DuckDB,
    so a single-series request never materialises the whole snapshot). Reads only the three
    columns it needs; the points are plain tuples-in-a-dataclass rather than validated models
    because the whole-dashboard read touches tens of thousands of rows."""
    latest = latest_partition_date(store, US_MACRO_OBSERVATIONS_DATASET)
    if latest is None:
        return {}
    files = [
        str(path) for path in store.partition_files(US_MACRO_OBSERVATIONS_DATASET, MARKET, latest)
    ]
    if not files:
        return {}
    sql = (
        "SELECT series_id, units, observation_date, value FROM read_parquet(?, union_by_name=true)"
    )
    params: list[object] = [files]
    if pairs is not None:
        if not pairs:
            return {}
        clause = " OR ".join("(series_id = ? AND units = ?)" for _ in pairs)
        sql += f" WHERE {clause}"
        for series_id, units in pairs:
            params.extend((series_id, units))
    sql += " ORDER BY series_id, units, observation_date"
    con = duckdb.connect()
    try:
        fetched = con.execute(sql, params).fetchall()
    finally:
        con.close()
    grouped: dict[tuple[str, str], list[ObservationPoint]] = {}
    for series_id, units, observation_date, value in fetched:
        if value is None:
            continue
        day = observation_date if isinstance(observation_date, date_) else None
        if day is None:
            day = date_.fromisoformat(str(observation_date)[:10])
        grouped.setdefault((str(series_id), str(units)), []).append(
            ObservationPoint(day, float(value))
        )
    return grouped


__all__ = [
    "MARKET",
    "US_MACRO_META_DATASET",
    "US_MACRO_OBSERVATIONS_DATASET",
    "US_MACRO_RELEASE_DATES_DATASET",
    "US_MACRO_VINTAGES_DATASET",
    "ObservationPoint",
    "latest_partition_date",
    "read_latest_partition",
    "read_meta",
    "read_observations",
    "read_release_dates",
    "read_vintages",
]
