"""Read-only Parquet reader tests, validated against the REAL writer's exact layout.

Every fixture partition is produced by `ParquetStore.write_partition` (the leader writer), so the
reader is proven against the true on-disk shape — the Hive-partitioned `v=<uuid>/part.parquet` +
`_manifest.json` pointer — not a hand-rolled guess. Covers: round-trip, manifest version
resolution (current pointer, never a stale/orphaned version), graceful empty degradation for a
never-ingested cell, and range reads that skip gaps.
"""

from __future__ import annotations

import json
from datetime import date

from ingest.reader import ParquetMarketDataReader
from ingest.store import ParquetStore


def test_reader_round_trips_writer_output(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    reader = ParquetMarketDataReader(root=tmp_path)  # type: ignore[arg-type]
    rows = [
        {"symbol": "2330", "market": "TW", "trade_date": "2024-01-02", "close": 593.0},
        {"symbol": "0050", "market": "TW", "trade_date": "2024-01-02", "close": 130.8},
    ]

    store.write_partition("TaiwanStockPrice", "TW", date(2024, 1, 2), rows)

    read_back = reader.read_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    assert {r["symbol"] for r in read_back} == {"2330", "0050"}
    assert reader.has_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    reader.close()


def test_reader_resolves_current_manifest_version_not_stale(tmp_path: object) -> None:
    """A re-write moves the manifest pointer to a new version and sweeps the old. The reader must
    read the CURRENT version (the writer's second write), never the swept-or-stale one."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    reader = ParquetMarketDataReader(root=tmp_path)  # type: ignore[arg-type]

    store.write_partition(
        "TaiwanStockPrice", "TW", date(2024, 1, 2), [{"symbol": "2330", "close": 593.0}]
    )
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        date(2024, 1, 2),
        [{"symbol": "2330", "close": 595.0}, {"symbol": "0050", "close": 130.8}],
    )

    read_back = reader.read_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    assert len(read_back) == 2  # the current version, not an accumulation of both writes
    assert {r["symbol"]: r["close"] for r in read_back}["2330"] == 595.0
    reader.close()


def test_reader_ignores_orphaned_version_dir(tmp_path: object) -> None:
    """A leftover `v=<orphan>` dir the manifest does not point at must never be read — resolution
    goes through the manifest pointer, not a blind glob of every version dir present."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    reader = ParquetMarketDataReader(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        "TaiwanStockPrice", "TW", date(2024, 1, 2), [{"symbol": "2330", "close": 593.0}]
    )

    partition_dir = reader.root / "dataset=TaiwanStockPrice" / "market=TW" / "date=2024-01-02"
    current_version = json.loads((partition_dir / "_manifest.json").read_text())["version"]
    orphan_dir = partition_dir / "v=orphaned-never-pointed-at"
    orphan_dir.mkdir()
    # Copy the real parquet so the orphan is a valid-but-unreferenced version.
    (orphan_dir / "part.parquet").write_bytes(
        (partition_dir / f"v={current_version}" / "part.parquet").read_bytes()
    )

    read_back = reader.read_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    assert len(read_back) == 1  # only the manifest-pointed version, orphan ignored
    reader.close()


def test_reader_never_ingested_cell_is_empty_not_crash(tmp_path: object) -> None:
    reader = ParquetMarketDataReader(root=tmp_path)  # type: ignore[arg-type]
    assert reader.read_partition("TaiwanStockPrice", "TW", date(1999, 1, 1)) == []
    assert not reader.has_partition("TaiwanStockPrice", "TW", date(1999, 1, 1))
    reader.close()


def test_reader_range_skips_gaps(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    reader = ParquetMarketDataReader(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        "TaiwanStockPrice", "TW", date(2024, 1, 2), [{"symbol": "2330", "close": 593.0}]
    )
    store.write_partition(
        "TaiwanStockPrice", "TW", date(2024, 1, 4), [{"symbol": "2330", "close": 600.0}]
    )

    rows = reader.read_range(
        "TaiwanStockPrice", "TW", [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    )
    assert [r["close"] for r in rows] == [593.0, 600.0]  # 1/3 (a gap) is skipped, not an error
    reader.close()
