"""A disk spool for a job that collects rows slowly and writes them once at the end.

`job_us_fundamentals` makes about 10,000 paced calls (9 capabilities for each of ~1,100 symbols,
0.5 s apart: nearly two hours) and used to hold every row in memory until the end, 1-1.5 GB, and
lose all of it if the process was killed at any point (an out-of-memory kill, a restart, a lost
host) and then repeat the two hours.

The spool keeps a few symbols in memory, writes them to small Parquet files at each checkpoint, and
records which symbols are done. A run that is started again for the same date picks up where the
last checkpoint left off. The final partition is built by DuckDB from the files
(`ParquetStore.write_partition_from_files`), so the rows never come back into Python.

On disk, under `<store root>/.spool/<name>__<date>/`:

    <dataset>.<n>.parquet   rows collected up to checkpoint n
    progress.jsonl          one line per checkpoint: {"n": 3, "symbols": [...], "failures": 2}

A checkpoint writes its chunk files first (each atomically, via a temp name) and the progress line
last, so a crash between the two leaves chunk files that no progress line vouches for; they are
deleted on resume and their symbols are fetched again, never counted twice."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections import defaultdict
from datetime import date as date_
from pathlib import Path

import duckdb
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

SPOOL_DIRNAME = ".spool"
PROGRESS_FILE = "progress.jsonl"
# A spool older than this was abandoned (the job was never run again for that date).
STALE_SPOOL_SECONDS = 3 * 24 * 3600.0


class RowSpool:
    def __init__(self, store_root: Path, name: str, run_date: date_) -> None:
        self._dir = store_root / SPOOL_DIRNAME / f"{name}__{run_date.isoformat()}"
        self._buffers: dict[str, list[dict[str, object]]] = defaultdict(list)
        self._checkpoint = 0
        self._done: set[str] = set()
        self._failures = 0

    @property
    def done(self) -> frozenset[str]:
        return frozenset(self._done)

    @property
    def failures(self) -> int:
        return self._failures

    def resume(self) -> None:
        """Pick up an earlier, unfinished run for this date, if one is on disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        progress = self._dir / PROGRESS_FILE
        if progress.exists():
            for line in progress.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    break  # a torn last line from a crash: that checkpoint did not happen
                self._checkpoint = int(entry["n"])
                self._done.update(entry["symbols"])
                self._failures += int(entry["failures"])
        for chunk in self._dir.glob("*.parquet"):
            if _chunk_number(chunk) > self._checkpoint:
                chunk.unlink()  # written, but its checkpoint never was
        for leftover in self._dir.glob("*.tmp"):
            leftover.unlink()
        if self._done:
            logger.info(
                "ingest_spool_resumed",
                spool=self._dir.name,
                symbols_done=len(self._done),
                failures=self._failures,
            )

    def add(self, dataset: str, rows: list[dict[str, object]]) -> None:
        self._buffers[dataset].extend(rows)

    def checkpoint(self, symbols: list[str], failures: int) -> None:
        """Make everything added so far durable and record `symbols` as done."""
        self._checkpoint += 1
        for dataset, rows in self._buffers.items():
            if rows:
                _write_chunk(self._dir / f"{dataset}.{self._checkpoint}.parquet", rows)
        self._buffers.clear()
        line = json.dumps({"n": self._checkpoint, "symbols": symbols, "failures": failures})
        with (self._dir / PROGRESS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._done.update(symbols)
        self._failures += failures

    def files(self, dataset: str) -> list[Path]:
        """The chunk files of `dataset`, in the order they were collected."""
        return sorted(self._dir.glob(f"{dataset}.*.parquet"), key=_chunk_number)

    def discard(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)


def _chunk_number(path: Path) -> int:
    return int(path.stem.rsplit(".", 1)[1])


def _write_chunk(path: Path, rows: list[dict[str, object]]) -> None:
    tmp = path.with_suffix(".tmp")
    con = duckdb.connect()
    try:
        con.register("frame", pd.DataFrame(rows))
        con.execute("COPY (SELECT * FROM frame) TO ? (FORMAT parquet)", [str(tmp)])
    finally:
        con.close()
    tmp.replace(path)  # a chunk file is whole or absent


def sweep_stale_spools(store_root: Path, *, max_age_seconds: float = STALE_SPOOL_SECONDS) -> int:
    """Remove spools nobody resumed (the job was not run again for that date). Returns how many."""
    root = store_root / SPOOL_DIRNAME
    if not root.exists():
        return 0
    now = time.time()
    removed = 0
    for child in root.iterdir():
        try:
            if now - child.stat().st_mtime < max_age_seconds:
                continue
        except FileNotFoundError:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    return removed


__all__ = ["RowSpool", "sweep_stale_spools"]
