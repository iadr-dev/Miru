"""The derived-store writer: Hive-partitioned Parquet with an atomic temp-dir -> rename ->
manifest-pointer-flip swap, so a reader globbing at any instant only ever sees a fully-written
partition, never a half-written one.

**Ingest-local version of the eventual production write path** — the leader ingest process is
the sole writer; API workers (a future phase) will read the same tree read-only via
`read_parquet(glob, hive_partitioning=true)` over a per-worker `:memory:` DuckDB connection,
never this module's writer connection and never a shared `.duckdb` file.

**Write path: DataFrame -> DuckDB zero-copy -> bulk `COPY ... TO parquet`.**
No row `INSERT`, no CSV staging — `pandas.DataFrame` (what the FinMind/yfinance SDKs already
hand back) is registered in an in-memory DuckDB connection and bulk-written with `COPY (SELECT
* FROM <df>) TO '<path>' (FORMAT parquet)`. `pyarrow` is not required for this: DuckDB's
own replacement scan over a `pandas.DataFrame` is idiomatic and already zero-copy (Arrow
under the hood), and `COPY` is DuckDB's own bulk writer — no reason to add a second stack.

**Partition layout.** Each `(dataset, market, date)` cell is its own tiny Hive-partitioned
tree:

    <parquet_root>/dataset=<dataset>/market=<market>/date=<date>/
        v=<version-uuid>/part.parquet     # the actual data — one immutable version per write
        _manifest.json                    # {"version": "<uuid>"} — readers resolve this first

A write:
1. Writes to a **temp dir** under `<parquet_root>/.tmp/<uuid>/part.parquet` (never directly
   into the partition tree — a crash here leaves nothing in the partition at all).
2. `os.rename`s the temp dir into `v=<uuid>` under the partition (atomic within one
   filesystem; the target name is always fresh, so this never collides with an existing dir).
3. Flips the manifest: write a new manifest to a temp file, then `os.replace` it onto
   `_manifest.json` (atomic on POSIX and Windows for files) — a reader globbing at any instant
   sees either the old, fully-written version or the new one, never a half-written partition.
4. Best-effort deletes now-orphaned older `v=*` directories under this partition — a write
   always rewrites the whole date partition (replace-by-partition, not row-level MERGE), so
   old versions are garbage once the pointer has moved.

`sweep_orphaned_temp_dirs()` clears `.tmp/*` on leader startup — the recovery for a crash that
happened *before* step 2.
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_
from pathlib import Path

import duckdb
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

_MANIFEST_NAME = "_manifest.json"
_TMP_DIRNAME = ".tmp"


@dataclass(frozen=True)
class PartitionKey:
    dataset: str
    market: str
    run_date: date_

    def path(self, root: Path) -> Path:
        return (
            root
            / f"dataset={self.dataset}"
            / f"market={self.market}"
            / f"date={self.run_date.isoformat()}"
        )


class ParquetStore:
    """The leader-only writer + a read-only helper over one `parquet_root` tree.

    Note: nothing in this class checks leadership itself — callers (the job functions in
    `ingest/jobs.py`) must consult `ingest.leader.LeaderElection.is_leader()` before calling
    `write_partition`. Keeping that check at the call site (not buried here) makes it visible
    in every job's code, which is the point of the architectural contract.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # -- write path -----------------------------------------------------------------

    def write_partition(
        self,
        dataset: str,
        market: str,
        run_date: date_,
        rows: list[dict[str, object]],
    ) -> int:
        """Atomically (re)write one date partition from `rows`. Idempotent: re-running with
        the same `rows` for the same `(dataset, market, run_date)` produces one version, not
        an accumulation — the natural idempotency key is the partition itself.

        Returns the number of rows written (0 is valid — an empty trading day is still a
        journaled, successful run, not an error).
        """
        key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
        partition_dir = key.path(self.root)
        partition_dir.mkdir(parents=True, exist_ok=True)

        # `pd.DataFrame([])` has zero columns (not just zero rows) — DuckDB's `COPY` rejects a
        # frame with no columns at all ("Need a DataFrame with at least one column"), even
        # though the Parquet format itself has no trouble with zero *rows*. A genuinely empty
        # `rows` list still needs one placeholder column so the write succeeds; the reader
        # side (`read_partition`) already handles a column-only, row-empty frame correctly —
        # it just yields no rows, exactly like any other zero-row read.
        if rows:
            frame = pd.DataFrame(rows)
        else:
            frame = pd.DataFrame({"_empty": pd.Series([], dtype="object")})
        version_id = uuid.uuid4().hex
        tmp_dir = self.root / _TMP_DIRNAME / f"{dataset}__{market}__{run_date.isoformat()}"
        tmp_dir = tmp_dir.with_name(f"{tmp_dir.name}__{version_id}")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_parquet = tmp_dir / "part.parquet"

        con = duckdb.connect()
        try:
            # Explicit `register` (rather than relying on DuckDB's implicit local-variable
            # replacement scan) — zero-copy over the pandas DataFrame, bulk `COPY`, never a
            # row-by-row INSERT. The destination path is a bound parameter, not an
            # interpolated string.
            con.register("frame", frame)
            con.execute(
                "COPY (SELECT * FROM frame) TO ? (FORMAT parquet)",
                [str(tmp_parquet)],
            )
        finally:
            con.close()

        version_dir = partition_dir / f"v={version_id}"
        tmp_dir.rename(version_dir)  # atomic within one filesystem
        self._flip_manifest(partition_dir, version_id)
        self._sweep_old_versions(partition_dir, keep_version=version_id)

        logger.info(
            "ingest_partition_written",
            dataset=dataset,
            market=market,
            run_date=run_date.isoformat(),
            rows_written=len(rows),
            version=version_id,
        )
        return len(rows)

    def _flip_manifest(self, partition_dir: Path, version_id: str) -> None:
        manifest_path = partition_dir / _MANIFEST_NAME
        tmp_manifest = partition_dir / f".manifest.tmp.{uuid.uuid4().hex}"
        tmp_manifest.write_text(json.dumps({"version": version_id}), encoding="utf-8")
        tmp_manifest.replace(manifest_path)  # atomic pointer flip

    def _sweep_old_versions(self, partition_dir: Path, *, keep_version: str) -> None:
        for child in partition_dir.iterdir():
            if child.is_dir() and child.name.startswith("v=") and child.name != f"v={keep_version}":
                shutil.rmtree(child, ignore_errors=True)

    def sweep_orphaned_temp_dirs(self) -> int:
        """Clear `.tmp/*` — the recovery path for a crash mid-write, run once on leader
        startup. Returns the number of orphaned dirs removed."""
        tmp_root = self.root / _TMP_DIRNAME
        if not tmp_root.exists():
            return 0
        removed = 0
        for child in tmp_root.iterdir():
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
        logger.info("ingest_tmp_swept", removed=removed)
        return removed

    # -- read path (ingest-local helper; the real reader fan-out is a future phase) ----

    def read_partition(self, dataset: str, market: str, run_date: date_) -> list[dict[str, object]]:
        """Resolve the manifest, then read the pointed-to version — never a mid-write glob.

        Returns `[]` if the partition has never been written (not an error — a gap the
        gap-fill/backfill path detects and fills).
        """
        key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
        partition_dir = key.path(self.root)
        manifest_path = partition_dir / _MANIFEST_NAME
        if not manifest_path.exists():
            return []
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version_id = manifest["version"]
        version_dir = partition_dir / f"v={version_id}"
        parquet_glob = str(version_dir / "*.parquet")
        con = duckdb.connect()
        try:
            relation = con.execute("SELECT * FROM read_parquet(?)", [parquet_glob])
            columns = [d[0] for d in relation.description or []]
            return [dict(zip(columns, row, strict=True)) for row in relation.fetchall()]
        finally:
            con.close()

    def has_partition(self, dataset: str, market: str, run_date: date_) -> bool:
        key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
        return (key.path(self.root) / _MANIFEST_NAME).exists()

    def read_range(
        self,
        dataset: str,
        market: str,
        run_dates: Sequence[date_],
    ) -> list[dict[str, object]]:
        """Read and concatenate several date partitions, in the given order, skipping any
        date that has never been written (a gap is silently omitted here — callers that need
        to distinguish "no data" from "not yet ingested" should check `has_partition`
        themselves; this helper is for downstream compute that only needs whatever rows
        already exist).

        Every partition is still resolved through its own manifest (never a mid-write glob
        across the whole tree), so this is just `read_partition` applied per date, not a new
        read mechanism — kept here rather than in a caller so there is exactly one Parquet-
        reading code path for the whole derived store.
        """
        rows: list[dict[str, object]] = []
        for run_date in run_dates:
            rows.extend(self.read_partition(dataset, market, run_date))
        return rows


__all__ = ["ParquetStore", "PartitionKey"]
