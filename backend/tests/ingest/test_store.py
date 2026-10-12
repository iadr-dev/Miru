"""Atomic partition swap tests.

Proves: a partition is only readable after the swap completes; a crash before the final
rename leaves the previous (or no) partition intact and readable; a re-write of the same date
overwrites rather than accumulates.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ingest.reader import ParquetMarketDataReader
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


def test_write_partition_with_zero_rows_does_not_crash(tmp_path: object) -> None:
    """A genuinely empty trading day (zero rows, not zero *columns*) must still write and read
    back successfully — `pd.DataFrame([])` has zero columns, which DuckDB's `COPY` used to
    reject outright ("Need a DataFrame with at least one column")."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]

    written = store.write_partition("TaiwanStockPrice", "TW", date(2024, 1, 2), [])

    assert written == 0
    assert store.has_partition("TaiwanStockPrice", "TW", date(2024, 1, 2))
    assert store.read_partition("TaiwanStockPrice", "TW", date(2024, 1, 2)) == []


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


def test_reader_glob_reads_across_mixed_empty_and_real_schema_partitions(tmp_path: object) -> None:
    """A dataset with some empty trading days (the `_empty`-placeholder schema) alongside real
    ones (the actual row schema) must still be readable by `ParquetMarketDataReader`, the
    cross-date glob reader — reproduces the real `schema mismatch in glob` crash found against
    the live US backfill (an empty US market holiday partition sitting beside a real-data one)."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        "us_price_eod", "US", date(2023, 9, 21), [{"symbol": "AAPL", "close": 175.0}]
    )
    store.write_partition("us_price_eod", "US", date(2023, 11, 23), [])  # empty-schema partition
    store.write_partition(
        "us_price_eod", "US", date(2023, 11, 24), [{"symbol": "AAPL", "close": 190.0}]
    )

    reader = ParquetMarketDataReader(root=tmp_path)
    try:
        rows = reader.read_range(
            "us_price_eod", "US", [date(2023, 9, 21), date(2023, 11, 23), date(2023, 11, 24)]
        )
    finally:
        reader.close()

    assert [r["symbol"] for r in rows] == ["AAPL", "AAPL"]


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


def test_read_range_frame_projects_columns_and_skips_gaps_and_empties(tmp_path: object) -> None:
    """`read_range_frame` is `read_range`'s columnar counterpart (added for `job_scoring_batch`'s
    whole-market multi-day windows, where `read_range`'s per-row `dict` boxing OOMs — see that
    method's own docstring). Proves it: reads only the requested columns (not the full row
    shape), skips a never-written date the same way `read_range` does, and survives an empty-
    schema partition sitting in the window (the same `_empty`-placeholder case
    `test_reader_glob_reads_across_mixed_empty_and_real_schema_partitions` covers for the row
    reader)."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        date(2024, 1, 2),
        [{"symbol": "2330", "trade_date": "2024-01-02", "close": 593.0, "volume": 100}],
    )
    store.write_partition("TaiwanStockPrice", "TW", date(2024, 1, 3), [])  # empty-schema day
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        date(2024, 1, 4),
        [{"symbol": "0050", "trade_date": "2024-01-04", "close": 130.8, "volume": 200}],
    )
    # 2024-01-05 is never written at all — a gap, same as `read_range`'s own gap case.

    frame = store.read_range_frame(
        "TaiwanStockPrice",
        "TW",
        [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
        columns=("symbol", "close"),
    )

    assert list(frame.columns) == ["symbol", "close"]  # only the requested columns
    assert sorted(frame["symbol"].tolist()) == ["0050", "2330"]
    assert sorted(frame["close"].tolist()) == [130.8, 593.0]


def test_read_range_frame_with_no_written_dates_returns_empty_frame(tmp_path: object) -> None:
    """No partition in the window has ever been written — a genuine gap, not an error; the
    result is an empty `DataFrame` shaped to the requested columns, not a crash or a `None`."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]

    frame = store.read_range_frame(
        "TaiwanStockPrice", "TW", [date(2024, 1, 2)], columns=("symbol", "close")
    )

    assert list(frame.columns) == ["symbol", "close"]
    assert len(frame) == 0


def test_read_range_latest_by_key_keeps_newest_snapshot_per_key(tmp_path: object) -> None:
    """Weekly full re-pulls store the same logical row in every snapshot. The newest copy of each
    key wins (a restated value replaces the old one), a key present only in an older snapshot
    is still returned, and an empty-schema partition or never-written date in the window is
    skipped without error."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        "us_financials",
        "US",
        date(2024, 1, 2),
        [
            {"symbol": "AAPL", "period_end": "2023-12-31", "item": "Revenue", "value": 100.0},
            {"symbol": "AAPL", "period_end": "2023-12-31", "item": "Cost", "value": 40.0},
        ],
    )
    store.write_partition("us_financials", "US", date(2024, 1, 3), [])  # empty-schema day
    store.write_partition(
        "us_financials",
        "US",
        date(2024, 1, 4),
        [{"symbol": "AAPL", "period_end": "2023-12-31", "item": "Revenue", "value": 105.0}],
    )
    # 2024-01-05 is never written at all.

    rows = store.read_range_latest_by_key(
        "us_financials",
        "US",
        [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
        columns=("symbol", "period_end", "item", "value"),
        key_columns=("symbol", "period_end", "item"),
    )

    by_item = {row["item"]: row["value"] for row in rows}
    assert by_item == {"Revenue": 105.0, "Cost": 40.0}
    assert len(rows) == 2


def test_read_range_latest_by_key_with_no_written_dates_returns_empty(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]

    rows = store.read_range_latest_by_key(
        "us_financials",
        "US",
        [date(2024, 1, 2)],
        columns=("symbol", "value"),
        key_columns=("symbol",),
    )

    assert rows == []


def test_a_transient_permission_error_on_the_swap_rename_is_ridden_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A brief Windows refusal to rename a just-written directory is ridden out."""
    from ingest import store as store_module  # noqa: PLC0415

    real_rename = Path.rename
    refusals = {"left": 2}

    def flaky_rename(self: Path, target: Path) -> Path:
        if refusals["left"] > 0 and self.parent.name == ".tmp":
            refusals["left"] -= 1
            raise PermissionError("access denied")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)
    store = ParquetStore(root=tmp_path)

    rows = [{"symbol": "2330", "market": "TW", "trade_date": "2026-01-05", "close": 1.0}]
    store.write_partition("ds", "TW", date(2026, 1, 5), rows)

    assert refusals["left"] == 0
    assert store.read_partition("ds", "TW", date(2026, 1, 5))[0]["symbol"] == "2330"
