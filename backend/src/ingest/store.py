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
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date as date_
from pathlib import Path

import duckdb
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

_MANIFEST_NAME = "_manifest.json"
_TMP_DIRNAME = ".tmp"


_RENAME_ATTEMPTS = 5
_RENAME_PAUSE_SECONDS = 0.1


def _rename_with_retry(src: Path, dst: Path) -> None:
    """`src.rename(dst)`, retried briefly on `PermissionError`.

    On Windows a just-written directory can be held for a moment by the writer's own file handles
    or by a virus scanner/indexer, and the rename fails with 'access denied' although nothing is
    wrong. A short bounded retry rides that out; on Linux it never triggers. The rename itself is
    still the single atomic step, so the swap stays all-or-nothing."""
    for attempt in range(1, _RENAME_ATTEMPTS + 1):
        try:
            src.rename(dst)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS:
                raise
            time.sleep(_RENAME_PAUSE_SECONDS * attempt)


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
        # `list_partition_dates` cache, keyed by (dataset, market) -> (dir_mtime_ns, dates).
        # Invalidated by directory mtime rather than a blind TTL: `write_partition`'s `mkdir`
        # for a new `date=` child bumps its parent `market=` dir's mtime immediately, so a
        # freshly-ingested partition is visible on the very next call in this process — no
        # staleness window to tune, unlike a fixed-TTL cache. This matters because the api and
        # ingest containers are separate processes; each api worker's own cache self-invalidates
        # from the filesystem, it never needs a cross-process signal from ingest.
        self._partition_dates_cache: dict[tuple[str, str], tuple[float, tuple[date_, ...]]] = {}

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
        _rename_with_retry(tmp_dir, version_dir)  # atomic within one filesystem
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

    def freshness_token(self, market: str) -> tuple[tuple[str, int], ...]:
        """A cheap fingerprint of "has ingest written anything new for this market": each
        dataset directory's mtime (bumped when a new `date=` partition dir is created). A caller
        caching a whole-market derivation keys it on this plus a TTL, so the derivation recomputes
        after an ingest run instead of serving yesterday's ranking."""
        token: list[tuple[str, int]] = []
        for dataset_dir in sorted(self.root.glob("dataset=*")):
            base = dataset_dir / f"market={market}"
            if base.is_dir():
                token.append((dataset_dir.name, base.stat().st_mtime_ns))
        return tuple(token)

    def has_partition(self, dataset: str, market: str, run_date: date_) -> bool:
        key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
        return (key.path(self.root) / _MANIFEST_NAME).exists()

    def list_partition_dates(self, dataset: str, market: str) -> tuple[date_, ...]:
        """Every date this `(dataset, market)` has a real, non-empty, fully-written partition
        for — ground truth for "was this a trading day", since a whole-market EOD job only
        writes actual rows on a real trading day (`ingest/jobs.py`'s calendar gate) — see
        `calendar_from_known_dates` for the intended consumer.

        A manifest existing is not sufficient on its own: a job that ran under a stale/naive
        calendar gate on a real holiday still writes a *valid*, manifest-pointed partition
        whose frame is the zero-row `_empty`-placeholder `write_partition` uses for a
        genuinely empty write (its own docstring: DuckDB's `COPY` rejects a zero-column
        frame, so an empty day still needs one placeholder column). Counting that placeholder
        partition as a trading day is exactly the bug this method exists to avoid — confirmed
        empirically: `TaiwanStockPrice`'s 2025-10-10 (Double Ten) partition has a valid manifest
        but zero real rows. Only a partition whose Parquet file has a column other than
        `_empty` counts."""
        base = self.root / f"dataset={dataset}" / f"market={market}"
        if not base.is_dir():
            return ()

        # A new `date=` partition's `mkdir` (`write_partition`) bumps `base`'s own mtime, so
        # comparing it against the cached mtime is enough to detect "ingest wrote something new
        # since we last scanned" without re-opening every partition's Parquet file again. This
        # is the fix for a real measured cost: scanning ~750+ date partitions (each its own
        # DuckDB `connect()` + Parquet-metadata read) took ~11s per
        # call once the store held a few years of backfilled history — and this method is called
        # on every `_real_calendar()`/price-history/ETF-flow tool invocation, so that cost was
        # paid on every single API request, not once.
        cache_key = (dataset, market)
        current_mtime = base.stat().st_mtime_ns
        cached = self._partition_dates_cache.get(cache_key)
        if cached is not None and cached[0] == current_mtime:
            return cached[1]

        # One DuckDB connection and ONE `parquet_schema` query over every partition's footer,
        # instead of a `connect()` + `read_parquet` per partition: measured ~0.6s vs ~9s for the
        # ~830 TaiwanStockPrice partitions, and this scan runs on the request path (it blocks the
        # event loop) after every API restart and every ingest write of a new date.
        version_dirs: dict[str, date_] = {}
        for child in base.iterdir():
            if not child.name.startswith("date="):
                continue
            manifest_path = child / _MANIFEST_NAME
            if not manifest_path.exists():
                continue
            try:
                run_date = date_.fromisoformat(child.name.removeprefix("date="))
            except ValueError:  # pragma: no cover — malformed dirname would be an ingest bug
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            version_dirs[(child / f"v={manifest['version']}").as_posix()] = run_date
        empty_dirs = self._placeholder_only_dirs(version_dirs)
        dates = [d for version_dir, d in version_dirs.items() if version_dir not in empty_dirs]
        result = tuple(sorted(dates))
        self._partition_dates_cache[cache_key] = (current_mtime, result)
        return result

    @staticmethod
    def _placeholder_only_dirs(version_dirs: Iterable[str]) -> set[str]:
        """The subset of these version dirs whose Parquet file is the zero-row `_empty`
        placeholder `write_partition` writes for an empty day (its own doc: DuckDB's `COPY`
        rejects a zero-column frame). Only the file footer's column list is read, so this is
        cheap even across hundreds of partitions. `read_parquet` would also surface the
        Hive-partition path columns, which is why the check is "has an `_empty` column", not an
        exact column-list match."""
        globs = [f"{version_dir}/*.parquet" for version_dir in version_dirs]
        if not globs:
            return set()
        con = duckdb.connect()
        try:
            rows = con.execute(
                "SELECT DISTINCT file_name FROM parquet_schema(?) WHERE name = '_empty'", [globs]
            ).fetchall()
        finally:
            con.close()
        return {Path(str(row[0])).parent.as_posix() for row in rows}

    def read_range(
        self,
        dataset: str,
        market: str,
        run_dates: Sequence[date_],
        *,
        symbols: Sequence[str] | None = None,
    ) -> list[dict[str, object]]:
        """Read and concatenate several date partitions, in the given order, skipping any
        date that has never been written (a gap is silently omitted here — callers that need
        to distinguish "no data" from "not yet ingested" should check `has_partition`
        themselves; this helper is for downstream compute that only needs whatever rows
        already exist).

        `symbols`, when given, filters at the DuckDB SQL layer (`WHERE symbol IN (...)`)
        rather than in Python after every row is read+boxed — a caller asking for 1-20 symbols
        (e.g. `get_price_history`'s ≤20-symbol batch contract) would otherwise pull and box
        every symbol in the whole market for each date just to discard nearly all of it: a
        measured 20-day/one-market window is ~445K rows market-wide vs. ~20-260 rows for a
        typical ≤20-symbol request. Falls back to the unfiltered `read_partition`-per-date path
        when `symbols` is `None`, since not every caller (e.g. a whole-market screener batch)
        wants a symbol filter at all.
        """
        if symbols is None:
            rows: list[dict[str, object]] = []
            for run_date in run_dates:
                rows.extend(self.read_partition(dataset, market, run_date))
            return rows
        return self._read_range_filtered(dataset, market, run_dates, symbols=symbols)

    def _read_range_filtered(
        self,
        dataset: str,
        market: str,
        run_dates: Sequence[date_],
        *,
        symbols: Sequence[str],
    ) -> list[dict[str, object]]:
        """One batched multi-glob DuckDB query (mirrors `read_range_frame`'s own batching, see
        its docstring) with the symbol filter pushed into SQL via a bound parameter list —
        never string-interpolated, even though these are trusted internal capability names, to
        keep exactly one query-construction pattern across this module."""
        globs: list[str] = []
        for run_date in run_dates:
            key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
            partition_dir = key.path(self.root)
            manifest_path = partition_dir / _MANIFEST_NAME
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            version_dir = partition_dir / f"v={manifest['version']}"
            globs.append(str(version_dir / "*.parquet"))
        if not globs or not symbols:
            return []

        # `placeholders` is only ever a run of `?, ?, ...` (one per `symbols` item) — never
        # interpolated user data; every actual value is still bound via `execute`'s parameter
        # list below, so this is not the injection pattern ruff's S608 flags.
        placeholders = ", ".join("?" for _ in symbols)
        con = duckdb.connect()
        try:
            relation = con.execute(
                f"SELECT * FROM read_parquet(?, union_by_name => true) "  # noqa: S608
                f"WHERE symbol IN ({placeholders})",
                [globs, *symbols],
            )
            columns = [d[0] for d in relation.description or []]
            return [dict(zip(columns, row, strict=True)) for row in relation.fetchall()]
        finally:
            con.close()

    def read_range_frame(
        self,
        dataset: str,
        market: str,
        run_dates: Sequence[date_],
        *,
        columns: Sequence[str],
    ) -> pd.DataFrame:
        """The columnar counterpart to `read_range`, for a caller that reads a wide multi-day
        window and would otherwise box every cell into a Python `dict` per row for no reason
        (`job_scoring_batch`'s own 260-trading-day price read: ~5.19M rows materialized as
        `list[dict]` peaks near 5GB resident; the identical rows as one batched, column-
        projected DataFrame read peak near 1.25GB — a ~4x reduction confirmed by measurement,
        not a guess). Two changes from `read_range`, both required to get that reduction:

        1. **One DuckDB query over every partition's glob**, not `read_partition` called once
           per date — `read_parquet` accepts a list of globs directly, so this is still exactly
           one Parquet-reading pass per date, just inside a single relation instead of N
           separate `fetchall()` + Python `dict` round-trips.
        2. **`.df()` instead of `.fetchall()`+`dict(zip(...))`** — the result stays in DuckDB's
           columnar/Arrow representation through the handoff to pandas, so cell values are
           stored in typed columnar buffers rather than individually boxed as Python objects.

        `union_by_name=True` because a genuinely empty trading day is written with a single
        placeholder `_empty` column (`write_partition`'s own doc: DuckDB's `COPY` rejects a
        zero-column frame) — without it, `read_parquet` raises a schema-mismatch error the
        moment one queried date happens to be such a day. `columns` is caller-specified rather
        than `SELECT *`: this method exists specifically to avoid materializing columns nothing
        downstream reads (the Hive-partitioning columns DuckDB injects — `market`/`dataset`/
        `date`/`v` — included), so a caller must say exactly what it needs.
        """
        globs: list[str] = []
        for run_date in run_dates:
            key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
            partition_dir = key.path(self.root)
            manifest_path = partition_dir / _MANIFEST_NAME
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            version_dir = partition_dir / f"v={manifest['version']}"
            globs.append(str(version_dir / "*.parquet"))

        if not globs:
            return pd.DataFrame({column: pd.Series([], dtype="object") for column in columns})

        column_list = ", ".join(columns)
        con = duckdb.connect()
        try:
            relation = con.execute(
                f"SELECT {column_list} FROM read_parquet(?, union_by_name=True) "  # noqa: S608
                # `columns` is a fixed, code-side list of known-safe identifiers (every call
                # site passes a literal tuple, never user/LLM input) — never string-built from
                # a request; the actual glob path list is still a bound parameter below.
                f"WHERE {columns[0]} IS NOT NULL",
                [globs],
            )
            return relation.df()
        finally:
            con.close()

    def read_range_latest_by_key(
        self,
        dataset: str,
        market: str,
        run_dates: Sequence[date_],
        *,
        columns: Sequence[str],
        key_columns: Sequence[str],
    ) -> list[dict[str, object]]:
        """Across the given date partitions, one row per `key_columns` combination — the copy
        from the newest partition. For a dataset whose every partition is a full re-pull of the
        same logical rows (the weekly US financial statements: ~1M rows per snapshot, each one
        restating the last), `read_range` boxes every snapshot's copy of every row as a Python
        `dict` — measured at ~5.8GB resident for a 400-day window, enough to OOM a 7.6GB host
        once the universe doubled. Deduplicating inside DuckDB means only one snapshot's worth
        of rows ever reaches Python, and a restated value wins over the older figure it
        replaced instead of both being returned.

        `columns`/`key_columns` are code-side literal identifier tuples (never request input),
        same as `read_range_frame`; the glob list is still a bound parameter. A date never
        written is skipped; no written partitions at all returns `[]`."""
        globs: list[str] = []
        for run_date in run_dates:
            key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
            partition_dir = key.path(self.root)
            manifest_path = partition_dir / _MANIFEST_NAME
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            globs.append(str(partition_dir / f"v={manifest['version']}" / "*.parquet"))
        if not globs:
            return []

        column_list = ", ".join(columns)
        key_list = ", ".join(key_columns)
        con = duckdb.connect()
        try:
            relation = con.execute(
                f"SELECT {column_list} FROM "  # noqa: S608
                "read_parquet(?, union_by_name=True, hive_partitioning=True) "
                f"WHERE {key_columns[0]} IS NOT NULL "
                f"QUALIFY row_number() OVER (PARTITION BY {key_list} ORDER BY date DESC) = 1",
                [globs],
            )
            names = [d[0] for d in relation.description or []]
            return [dict(zip(names, row, strict=True)) for row in relation.fetchall()]
        finally:
            con.close()


__all__ = ["ParquetStore", "PartitionKey"]
