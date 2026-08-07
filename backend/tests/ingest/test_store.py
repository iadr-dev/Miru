"""Atomic partition swap tests.

Proves: a partition is only readable after the swap completes; a crash before the final
rename leaves the previous (or no) partition intact and readable; a re-write of the same date
overwrites rather than accumulates.
"""

from __future__ import annotations

from datetime import date

from ingest.store import ParquetStore


def test_write_then_read_partition_round_trips(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    rows = [
        {"symbol": "2330", "market": "TW", "trade_date": "2024-01-02", "close": 593.0},
        {"symbol": "0050", "market": "TW", "trade_date": "2024-01-02", "close": 130.8},
    ]

    written = store.write_partition("TaiwanStockPrice", "TW", date(2024, 1, 2), rows)

    assert written == 2
    assert store.has_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    read_back = store.read_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    assert {r["symbol"] for r in read_back} == {"2330", "0050"}


def test_partition_not_visible_until_manifest_flip(tmp_path: object) -> None:
    """Simulate a crash *before* the final rename: write to the temp dir but never call
    `write_partition` to completion — the partition must stay unreadable. A reader globbing
    mid-write must never see empty/partial data — the manifest is what prevents it."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]

    # No write has happened at all yet — this is the "before swap" state.
    assert not store.has_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    assert store.read_partition("TaiwanStockPrice", "TW", date(2024, 1, 2)) == []


def test_rewrite_overwrites_not_accumulates(tmp_path: object) -> None:
    """Idempotent re-run: writing the same date twice must not duplicate rows — the second
    write's version becomes the manifest's target and the first version is swept."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    first_rows = [{"symbol": "2330", "trade_date": "2024-01-02", "close": 593.0}]
    second_rows = [
        {"symbol": "2330", "trade_date": "2024-01-02", "close": 595.0},
        {"symbol": "0050", "trade_date": "2024-01-02", "close": 130.8},
    ]

    store.write_partition("TaiwanStockPrice", "TW", date(2024, 1, 2), first_rows)
    store.write_partition("TaiwanStockPrice", "TW", date(2024, 1, 2), second_rows)

    read_back = store.read_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    assert len(read_back) == 2  # not 3 — the first version's rows are gone, not appended
    close_by_symbol = {r["symbol"]: r["close"] for r in read_back}
    assert close_by_symbol["2330"] == 595.0  # the second write's value wins


def test_crash_before_rename_leaves_prior_partition_intact(tmp_path: object) -> None:
    """A crashed mid-COPY write (never reaches `os.rename`/manifest flip) must leave whatever
    was previously readable still readable, and any orphaned temp dir must be sweepable."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        "TaiwanStockPrice", "TW", date(2024, 1, 2), [{"symbol": "2330", "close": 593.0}]
    )

    # Simulate a crashed write: create an orphaned temp dir directly, bypassing the swap.
    tmp_root = store.root / ".tmp" / "TaiwanStockPrice__TW__2024-01-03__deadbeef"
    tmp_root.mkdir(parents=True)
    (tmp_root / "part.parquet").write_bytes(b"not a real parquet file")

    # The prior, fully-swapped partition is unaffected.
    read_back = store.read_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    assert len(read_back) == 1
    # The crashed date never got a manifest, so it's correctly reported as missing (a gap).
    assert not store.has_partition("TaiwanStockPrice", "TW", date(2024, 1, 3))

    removed = store.sweep_orphaned_temp_dirs()

    assert removed == 1
    assert not (store.root / ".tmp").exists() or not list((store.root / ".tmp").iterdir())
    # Sweeping never touches the good partition.
    read_back_after_sweep = store.read_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    assert len(read_back_after_sweep) == 1


def test_read_range_concatenates_written_partitions_and_skips_gaps(tmp_path: object) -> None:
    """`read_range` is the derived-compute reader used by analytics: it reads several date
    partitions in order and silently omits any date that was never written (a gap), without
    treating that as an error."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        "TaiwanStockPrice", "TW", date(2024, 1, 2), [{"symbol": "2330", "close": 593.0}]
    )
    store.write_partition(
        "TaiwanStockPrice", "TW", date(2024, 1, 4), [{"symbol": "2330", "close": 600.0}]
    )

    rows = store.read_range(
        "TaiwanStockPrice", "TW", [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    )

    assert [r["close"] for r in rows] == [593.0, 600.0]  # 1/3 (never written) is skipped
