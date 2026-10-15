"""`RowSpool`: rows collected over hours survive a kill, a resume never counts a symbol twice, and
the store builds the partition from the spool files."""

from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

import pytest

from ingest.spool import PROGRESS_FILE, SPOOL_DIRNAME, RowSpool, sweep_stale_spools
from ingest.store import ParquetStore

RUN_DATE = date(2024, 1, 2)


def _rows(symbol: str, n: int = 2) -> list[dict[str, object]]:
    return [{"symbol": symbol, "value": i} for i in range(n)]


def _progress(root: Path) -> Path:
    return root / SPOOL_DIRNAME / f"job__{RUN_DATE.isoformat()}" / PROGRESS_FILE


def test_a_checkpointed_run_is_picked_up_by_a_new_spool(tmp_path: Path) -> None:
    first = RowSpool(tmp_path, "job", RUN_DATE)
    first.resume()
    first.add("ds", _rows("A"))
    first.add("ds", _rows("B"))
    first.checkpoint(["A", "B"], failures=1)

    second = RowSpool(tmp_path, "job", RUN_DATE)  # the process was killed; this is the next run
    second.resume()

    assert second.done == {"A", "B"}
    assert second.failures == 1
    assert len(second.files("ds")) == 1


def test_rows_added_after_the_last_checkpoint_are_lost_not_half_kept(tmp_path: Path) -> None:
    first = RowSpool(tmp_path, "job", RUN_DATE)
    first.resume()
    first.add("ds", _rows("A"))
    first.checkpoint(["A"], failures=0)
    first.add("ds", _rows("B"))  # never checkpointed

    second = RowSpool(tmp_path, "job", RUN_DATE)
    second.resume()

    assert second.done == {"A"}
    assert len(second.files("ds")) == 1


def test_a_chunk_without_its_progress_line_is_dropped_on_resume(tmp_path: Path) -> None:
    """Killed between writing the chunk files and the progress line: the symbols are fetched again,
    so keeping the chunk would count them twice."""
    first = RowSpool(tmp_path, "job", RUN_DATE)
    first.resume()
    first.add("ds", _rows("A"))
    first.checkpoint(["A"], failures=0)
    first.add("ds", _rows("B"))
    first.checkpoint(["B"], failures=0)
    lines = _progress(tmp_path).read_text(encoding="utf-8").splitlines()
    _progress(tmp_path).write_text(lines[0] + "\n", encoding="utf-8")  # line 2 never hit disk

    second = RowSpool(tmp_path, "job", RUN_DATE)
    second.resume()

    assert second.done == {"A"}
    assert len(second.files("ds")) == 1


def test_a_torn_progress_line_is_ignored(tmp_path: Path) -> None:
    first = RowSpool(tmp_path, "job", RUN_DATE)
    first.resume()
    first.add("ds", _rows("A"))
    first.checkpoint(["A"], failures=0)
    with _progress(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"n": 2, "symb')

    second = RowSpool(tmp_path, "job", RUN_DATE)
    second.resume()

    assert second.done == {"A"}


def test_a_different_date_does_not_resume_another_spool(tmp_path: Path) -> None:
    first = RowSpool(tmp_path, "job", RUN_DATE)
    first.resume()
    first.add("ds", _rows("A"))
    first.checkpoint(["A"], failures=0)

    other = RowSpool(tmp_path, "job", date(2024, 1, 3))
    other.resume()

    assert other.done == frozenset()


def test_discard_removes_everything(tmp_path: Path) -> None:
    spool = RowSpool(tmp_path, "job", RUN_DATE)
    spool.resume()
    spool.add("ds", _rows("A"))
    spool.checkpoint(["A"], failures=0)
    spool.discard()

    assert not _progress(tmp_path).parent.exists()


def test_the_store_builds_a_partition_from_the_spool_files(tmp_path: Path) -> None:
    spool = RowSpool(tmp_path, "job", RUN_DATE)
    spool.resume()
    for symbol in ("A", "B", "C"):
        spool.add("ds", _rows(symbol, 3))
        spool.checkpoint([symbol], failures=0)
    # a later chunk may carry a column the first ones did not
    spool.add("ds", [{"symbol": "D", "value": 0, "extra": "x"}])
    spool.checkpoint(["D"], failures=0)
    store = ParquetStore(root=tmp_path)

    written = store.write_partition_from_files("ds", "US", RUN_DATE, spool.files("ds"))

    assert written == 10
    rows = store.read_partition("ds", "US", RUN_DATE)
    assert len(rows) == 10
    assert {r["symbol"] for r in rows} == {"A", "B", "C", "D"}


def test_the_store_writes_an_empty_partition_when_there_are_no_files(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)

    assert store.write_partition_from_files("ds", "US", RUN_DATE, []) == 0
    assert store.has_partition("ds", "US", RUN_DATE)


def test_sweep_removes_only_abandoned_spools(tmp_path: Path) -> None:
    old = RowSpool(tmp_path, "job", date(2024, 1, 1))
    old.resume()
    fresh = RowSpool(tmp_path, "job", RUN_DATE)
    fresh.resume()
    old_dir = tmp_path / SPOOL_DIRNAME / "job__2024-01-01"
    week_ago = time.time() - 7 * 24 * 3600
    os.utime(old_dir, (week_ago, week_ago))

    assert sweep_stale_spools(tmp_path) == 1
    assert not old_dir.exists()
    assert _progress(tmp_path).parent.exists()


def test_sweep_with_no_spool_directory_is_a_no_op(tmp_path: Path) -> None:
    assert sweep_stale_spools(tmp_path) == 0


def test_a_refreshed_partition_keeps_the_symbols_it_did_not_refresh(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "ds",
        "US",
        date(2024, 1, 1),
        [{"symbol": "A", "value": 1}, {"symbol": "B", "value": 1}, {"symbol": "B", "value": 2}],
    )
    previous = store.partition_files("ds", "US", date(2024, 1, 1))
    spool = RowSpool(tmp_path, "job", RUN_DATE)
    spool.resume()
    # B is refreshed (one row now, a different type and an extra column), C is new, A is untouched.
    spool.add("ds", [{"symbol": "B", "value": 9.5, "note": "new"}, {"symbol": "C", "value": 3.0}])
    spool.checkpoint(["B", "C"], failures=0)

    written = store.write_partition_from_files(
        "ds", "US", RUN_DATE, spool.files("ds"), carry_from=previous
    )

    rows = store.read_partition("ds", "US", RUN_DATE)
    assert written == 3
    assert sorted((r["symbol"], r["value"]) for r in rows) == [("A", 1.0), ("B", 9.5), ("C", 3.0)]


def test_a_composite_key_replaces_only_the_matching_pairs(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "state",
        "US",
        date(2024, 1, 1),
        [
            {"symbol": "A", "capability": "x", "fetched_on": "2024-01-01"},
            {"symbol": "A", "capability": "y", "fetched_on": "2024-01-01"},
        ],
    )
    spool = RowSpool(tmp_path, "job", RUN_DATE)
    spool.resume()
    spool.add("state", [{"symbol": "A", "capability": "x", "fetched_on": "2024-01-02"}])
    spool.checkpoint(["A"], failures=0)

    store.write_partition_from_files(
        "state",
        "US",
        RUN_DATE,
        spool.files("state"),
        carry_from=store.partition_files("state", "US", date(2024, 1, 1)),
        key=("symbol", "capability"),
    )

    rows = store.read_partition("state", "US", RUN_DATE)
    assert sorted((r["capability"], r["fetched_on"]) for r in rows) == [
        ("x", "2024-01-02"),
        ("y", "2024-01-01"),
    ]


def test_a_key_that_is_not_a_column_name_is_refused(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    with pytest.raises(ValueError, match="not a column name"):
        store.write_partition_from_files("ds", "US", RUN_DATE, [], key=("symbol; DROP TABLE x",))
