"""Two writers on one partition, a reader during a flip, and the cleanup that must not hurt either.

The failure these pin: writer A flipped the manifest, writer B flipped it, then A's cleanup deleted
B's version, leaving the manifest pointing at a directory that no longer exists."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import duckdb

from ingest.store import ParquetStore

DAY = date(2024, 1, 2)


def _rows(tag: int) -> list[dict[str, object]]:
    return [{"symbol": "2330", "close": float(tag), "writer": tag}]


def _version_dirs(store: ParquetStore) -> list[Path]:
    partition = store.root / "dataset=TaiwanStockPrice" / "market=TW" / f"date={DAY.isoformat()}"
    return sorted(p for p in partition.iterdir() if p.is_dir() and p.name.startswith("v="))


def _manifest_target(store: ParquetStore) -> str:
    partition = store.root / "dataset=TaiwanStockPrice" / "market=TW" / f"date={DAY.isoformat()}"
    return json.loads((partition / "_manifest.json").read_text(encoding="utf-8"))["version"]


def test_the_previous_version_survives_the_flip_so_a_reader_in_flight_can_finish(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition("TaiwanStockPrice", "TW", DAY, _rows(1))
    first_target = _manifest_target(store)

    # A reader resolved the manifest, then the writer replaced the partition.
    store.write_partition("TaiwanStockPrice", "TW", DAY, _rows(2))

    old = next(p for p in _version_dirs(store) if p.name == f"v={first_target}")
    assert old.is_dir()  # still on disk inside the grace period
    con = duckdb.connect()
    try:
        got = con.execute("SELECT writer FROM read_parquet(?)", [str(old / "*.parquet")]).fetchall()
    finally:
        con.close()
    assert got == [(1,)]
    assert store.read_partition("TaiwanStockPrice", "TW", DAY)[0]["writer"] == 2


def test_stale_versions_are_removed_after_the_grace_period_but_never_the_current_one(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition("TaiwanStockPrice", "TW", DAY, _rows(1))
    store.write_partition("TaiwanStockPrice", "TW", DAY, _rows(2))
    assert len(_version_dirs(store)) == 2

    assert store.sweep_stale_versions() == 0  # both are young: nothing is touched
    assert len(_version_dirs(store)) == 2

    assert store.sweep_stale_versions(min_age_seconds=0) == 1  # past the grace period
    remaining = _version_dirs(store)
    assert [p.name for p in remaining] == [f"v={_manifest_target(store)}"]
    assert store.read_partition("TaiwanStockPrice", "TW", DAY)[0]["writer"] == 2


def test_even_a_zero_grace_sweep_keeps_the_version_the_manifest_points_at(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition("TaiwanStockPrice", "TW", DAY, _rows(7))
    assert store.sweep_stale_versions(min_age_seconds=0) == 0
    assert store.has_partition("TaiwanStockPrice", "TW", DAY)


def test_concurrent_writers_on_one_partition_never_leave_a_dangling_manifest(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    errors: list[str] = []

    def writer(tag: int) -> None:
        for round_no in range(4):
            try:
                store.write_partition("TaiwanStockPrice", "TW", DAY, _rows(tag * 10 + round_no))
            except BaseException:
                errors.append(traceback.format_exc())

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(writer, range(6)))

    assert errors == []
    assert store.has_partition("TaiwanStockPrice", "TW", DAY)  # manifest -> an existing version
    rows = store.read_partition("TaiwanStockPrice", "TW", DAY)
    assert len(rows) == 1  # exactly one writer's complete answer, not a mix and not nothing
    assert any(p.name == f"v={_manifest_target(store)}" for p in _version_dirs(store))


def test_writers_in_separate_processes_serialise_on_the_partition_lock(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import sys
        from datetime import date
        from ingest.store import ParquetStore
        store = ParquetStore(root=sys.argv[1])
        tag = int(sys.argv[2])
        for round_no in range(3):
            store.write_partition(
                "TaiwanStockPrice", "TW", date(2024, 1, 2),
                [{"symbol": "2330", "close": 1.0, "writer": tag * 10 + round_no}],
            )
        """
    )
    procs = [
        subprocess.Popen(  # noqa: S603 - fixed argv, our own interpreter
            [sys.executable, "-c", script, str(tmp_path), str(tag)],
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ},
        )
        for tag in range(4)
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=180)
        assert proc.returncode == 0, err

    store = ParquetStore(root=tmp_path)
    assert store.has_partition("TaiwanStockPrice", "TW", DAY)
    assert len(store.read_partition("TaiwanStockPrice", "TW", DAY)) == 1


def test_a_partition_whose_manifest_names_a_missing_version_counts_as_a_gap(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition("TaiwanStockPrice", "TW", DAY, _rows(1))
    target = _manifest_target(store)
    for p in _version_dirs(store):  # corrupt it the way the old race did
        if p.name == f"v={target}":
            for f in p.iterdir():
                f.unlink()
            p.rmdir()

    assert not store.has_partition("TaiwanStockPrice", "TW", DAY)
    store.write_partition("TaiwanStockPrice", "TW", DAY, _rows(2))  # backfill repairs it
    assert store.has_partition("TaiwanStockPrice", "TW", DAY)


def test_the_temp_sweep_leaves_a_write_that_may_still_be_running(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    fresh = tmp_path / ".tmp" / "TaiwanStockPrice__TW__2024-01-03__aaaa"
    crashed = tmp_path / ".tmp" / "TaiwanStockPrice__TW__2024-01-04__bbbb"
    for d in (fresh, crashed):
        d.mkdir(parents=True)
        (d / "part.parquet").write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(crashed, (two_hours_ago, two_hours_ago))

    assert store.sweep_orphaned_temp_dirs() == 1
    assert fresh.exists()
    assert not crashed.exists()
