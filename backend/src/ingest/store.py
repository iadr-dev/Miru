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
from datetime import UTC, datetime, timedelta
from datetime import date as date_
from pathlib import Path

import duckdb
import pandas as pd
import structlog

from ingest.filelock import FileLock

logger = structlog.get_logger(__name__)

_MANIFEST_NAME = "_manifest.json"
_TMP_DIRNAME = ".tmp"
# Exchange-confirmed market closures, one marker file per (dataset, market, date), outside every
# `dataset=` tree so no partition scan, freshness token or reader ever sees them.
_CLOSURES_DIRNAME = "_closures"
# FinMind plan denials, one marker file per (market, capability) holding when the denial was
# learned. Outside every `dataset=` tree like the closures, and a different fact: a plan can
# change, so a marker is only believed for a bounded time (`is_plan_denied`'s `ttl`).
_PLAN_DENIALS_DIRNAME = "_plan_denials"
_WRITE_LOCK_NAME = ".write.lock"

# A reader that resolved the manifest just before a flip may still be reading the previous version
# for a while (a large range scan, a slow client). The previous version is therefore kept for this
# long after it was replaced, and only then removed. Never shorter than the longest read.
VERSION_GRACE_SECONDS = 600.0
# A temp directory younger than this may belong to a write that is still running in another
# process, so the recovery sweep leaves it alone. A crashed write's leftovers are older.
TMP_ORPHAN_MIN_AGE_SECONDS = 3600.0


_RENAME_ATTEMPTS = 5
_RENAME_PAUSE_SECONDS = 0.1


def _replace_with_retry(src: Path, dst: Path) -> None:
    """`src.replace(dst)` (the atomic manifest pointer flip), retried briefly on `PermissionError`.

    On Windows replacing a file that something has open at that instant (an antivirus scan, a
    reader in the middle of `read_text`) is refused although nothing is wrong; the window is a few
    milliseconds, so a short bounded retry rides it out. On Linux it never triggers."""
    for attempt in range(1, _RENAME_ATTEMPTS + 1):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS:
                raise
            time.sleep(_RENAME_PAUSE_SECONDS * attempt)


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


def sql_in(column: str, values: Sequence[str]) -> str:
    """`column IN ('a', 'b')` for `read_range_frame(where=...)`, with each value quoted. For values
    that are constants in code (item names), not request input."""
    quoted = ", ".join("'" + value.replace("'", "''") + "'" for value in values)
    return f"{column} IN ({quoted})"


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

        self._commit_version(partition_dir, tmp_dir, version_id)

        logger.info(
            "ingest_partition_written",
            dataset=dataset,
            market=market,
            run_date=run_date.isoformat(),
            rows_written=len(rows),
            version=version_id,
        )
        return len(rows)

    def partition_files(self, dataset: str, market: str, run_date: date_) -> list[Path]:
        """The Parquet files of the partition's current version (empty if it has none)."""
        partition_dir = PartitionKey(dataset=dataset, market=market, run_date=run_date).path(
            self.root
        )
        version = self._manifest_version(partition_dir)
        if version is None:
            return []
        return sorted((partition_dir / f"v={version}").glob("*.parquet"))

    def latest_partition_on_or_before(
        self, dataset: str, market: str, on_or_before: date_
    ) -> tuple[date_, list[Path]] | None:
        """The newest real partition dated `on_or_before` or earlier, with its files, or None."""
        dates = [d for d in self.list_partition_dates(dataset, market) if d <= on_or_before]
        if not dates:
            return None
        newest = max(dates)
        return newest, self.partition_files(dataset, market, newest)

    def write_partition_from_files(  # noqa: PLR0913 - the cell and the files and the carry-forward
        self,
        dataset: str,
        market: str,
        run_date: date_,
        files: Sequence[Path],
        *,
        carry_from: Sequence[Path] = (),
        key: Sequence[str] = ("symbol",),
    ) -> int:
        """`write_partition` for rows that already sit in Parquet files (a spool): DuckDB reads
        them and writes the partition itself, so the rows never pass through Python. A job that
        collects over hours holds a few symbols at a time and hands over the files at the end.
        The files may differ in columns (`union_by_name`). Same atomic swap as `write_partition`.

        `carry_from` is the previous partition's files, for a job that refreshes only part of its
        universe: every previous row whose `key` is not among the new rows is kept, so a symbol
        that was not refreshed this time does not vanish from the new partition. A key that IS
        among the new rows takes the new rows only. Returns the number of rows written."""
        for column in key:
            if not column.isidentifier():
                raise ValueError(f"not a column name: {column!r}")
        same_key = " AND ".join(f'new."{c}" IS NOT DISTINCT FROM prev."{c}"' for c in key)
        partition_dir = PartitionKey(dataset=dataset, market=market, run_date=run_date).path(
            self.root
        )
        partition_dir.mkdir(parents=True, exist_ok=True)
        version_id = uuid.uuid4().hex
        tmp_dir = self.root / _TMP_DIRNAME / f"{dataset}__{market}__{run_date.isoformat()}"
        tmp_dir = tmp_dir.with_name(f"{tmp_dir.name}__{version_id}")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_parquet = tmp_dir / "part.parquet"
        sources = [str(f) for f in files]
        carried = [str(f) for f in carry_from]
        # DuckDB cannot bind the COPY target while another parameter (the file list) is bound in
        # the same statement. The target is a path this method just made under the store's own
        # temp directory (a uuid), quoted as a literal.
        target = str(tmp_parquet).replace("'", "''")
        con = duckdb.connect()
        try:
            if sources and carried:
                con.execute(
                    "COPY ("  # noqa: S608 - the key columns are validated identifiers
                    "WITH new AS (SELECT * FROM read_parquet(?, union_by_name=True)), "
                    "prev AS (SELECT * FROM read_parquet(?, union_by_name=True)) "
                    "SELECT * FROM new UNION ALL BY NAME "
                    f"SELECT * FROM prev WHERE NOT EXISTS (SELECT 1 FROM new WHERE {same_key})"
                    f") TO '{target}' (FORMAT parquet)",
                    [sources, carried],
                )
            elif sources or carried:
                con.execute(
                    "COPY (SELECT * FROM read_parquet(?, union_by_name=True)) "  # noqa: S608
                    f"TO '{target}' (FORMAT parquet)",
                    [sources or carried],
                )
            else:  # nothing collected: the same one-placeholder-column empty partition
                con.register("frame", pd.DataFrame({"_empty": pd.Series([], dtype="object")}))
                con.execute("COPY (SELECT * FROM frame) TO ? (FORMAT parquet)", [str(tmp_parquet)])
            counted = con.execute("SELECT count(*) FROM read_parquet(?)", [str(tmp_parquet)])
            rows_written = int(counted.fetchone()[0])  # type: ignore[index]
        finally:
            con.close()
        self._commit_version(partition_dir, tmp_dir, version_id)
        logger.info(
            "ingest_partition_written",
            dataset=dataset,
            market=market,
            run_date=run_date.isoformat(),
            rows_written=rows_written,
            carried_from=len(carried),
            version=version_id,
        )
        return rows_written

    def _commit_version(self, partition_dir: Path, tmp_dir: Path, version_id: str) -> None:
        """Publish a finished temp directory as the partition's current version. The expensive
        part (the Parquet COPY) ran into a private temp directory; only this cheap swap runs under
        the partition lock, so two writers on the same partition take turns instead of
        interleaving "rename, flip, delete the other one's version"."""
        version_dir = partition_dir / f"v={version_id}"
        with FileLock(partition_dir / _WRITE_LOCK_NAME):
            _rename_with_retry(tmp_dir, version_dir)  # atomic within one filesystem
            self._flip_manifest(partition_dir, version_id)
            self._sweep_old_versions(partition_dir, keep_version=version_id)
        # Rewriting an existing date (an `_empty` placeholder replaced by real rows) does not
        # change the market directory's mtime that keys the date cache, so drop the entry here.
        market_dir = partition_dir.parent
        self._partition_dates_cache.pop(
            (
                market_dir.parent.name.removeprefix("dataset="),
                market_dir.name.removeprefix("market="),
            ),
            None,
        )

    def _flip_manifest(self, partition_dir: Path, version_id: str) -> None:
        manifest_path = partition_dir / _MANIFEST_NAME
        tmp_manifest = partition_dir / f".manifest.tmp.{uuid.uuid4().hex}"
        tmp_manifest.write_text(json.dumps({"version": version_id}), encoding="utf-8")
        _replace_with_retry(tmp_manifest, manifest_path)  # atomic pointer flip

    def _sweep_old_versions(
        self,
        partition_dir: Path,
        *,
        keep_version: str,
        min_age_seconds: float = VERSION_GRACE_SECONDS,
    ) -> int:
        """Remove superseded versions once they are older than the grace period. The version the
        manifest points at is never removed, whatever its age."""
        now = time.time()
        removed = 0
        for child in partition_dir.iterdir():
            if not (child.is_dir() and child.name.startswith("v=")):
                continue
            if child.name == f"v={keep_version}":
                continue
            try:
                age = now - child.stat().st_mtime
            except FileNotFoundError:
                continue  # another process removed it between iterdir and stat
            if age >= min_age_seconds:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed

    def sweep_stale_versions(self, *, min_age_seconds: float = VERSION_GRACE_SECONDS) -> int:
        """Housekeeping over the whole tree: drop superseded versions older than the grace period
        that no later write came back to clean up. Run by the leader at startup."""
        removed = 0
        for partition_dir in self.root.glob("dataset=*/market=*/date=*"):
            current = self._manifest_version(partition_dir)
            if current is None:
                continue
            with FileLock(partition_dir / _WRITE_LOCK_NAME):
                removed += self._sweep_old_versions(
                    partition_dir, keep_version=current, min_age_seconds=min_age_seconds
                )
        if removed:
            logger.info("ingest_stale_versions_swept", removed=removed)
        return removed

    def _existing_globs(self, dataset: str, market: str, run_dates: Sequence[date_]) -> list[str]:
        """The parquet glob of every date in `run_dates` that has a readable partition, in order."""
        globs: list[str] = []
        for run_date in run_dates:
            key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
            parquet_glob = self._parquet_glob(key.path(self.root))
            if parquet_glob is not None:
                globs.append(parquet_glob)
        return globs

    @classmethod
    def _parquet_glob(cls, partition_dir: Path) -> str | None:
        """The glob over the partition's current version, or `None` when there is no readable
        manifest or the version directory it names is missing (a torn partition). Readers skip
        such a partition (the gap-fill repairs it) instead of crashing the whole batch."""
        version = cls._manifest_version(partition_dir)
        if version is None:
            return None
        version_dir = partition_dir / f"v={version}"
        return str(version_dir / "*.parquet") if version_dir.is_dir() else None

    @staticmethod
    def _manifest_version(partition_dir: Path) -> str | None:
        """The version id the manifest points at, or None if there is no readable manifest."""
        try:
            manifest = json.loads((partition_dir / _MANIFEST_NAME).read_text(encoding="utf-8"))
            version = manifest["version"]
        except (OSError, ValueError, KeyError):
            return None
        return version if isinstance(version, str) else None

    def sweep_orphaned_temp_dirs(
        self, *, min_age_seconds: float = TMP_ORPHAN_MIN_AGE_SECONDS
    ) -> int:
        """Clear leftovers in `.tmp/` - the recovery path for a crash mid-write, run on leader
        startup. Only entries older than `min_age_seconds` are removed: a younger one may be a
        write that is still running in another process. Returns the number removed."""
        tmp_root = self.root / _TMP_DIRNAME
        if not tmp_root.exists():
            return 0
        now = time.time()
        removed = 0
        for child in tmp_root.iterdir():
            try:
                age = now - child.stat().st_mtime
            except FileNotFoundError:
                continue
            if age < min_age_seconds:
                continue
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
        parquet_glob = self._parquet_glob(key.path(self.root))
        if parquet_glob is None:
            return []
        con = duckdb.connect()
        try:
            # The path's `dataset=`/`market=`/`date=`/`v=` parts come back as columns (readers rely
            # on `market`, which stored price rows do not carry); `without_path_columns` strips
            # them where rows are written back.
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

    def _closure_marker(self, dataset: str, market: str, run_date: date_) -> Path:
        return self.root / _CLOSURES_DIRNAME / dataset / market / run_date.isoformat()

    def mark_closed(self, dataset: str, market: str, run_date: date_, detail: str) -> None:
        """Remember that `run_date` was confirmed a market closure for `dataset`, so a gap check
        does not fetch it again. Per dataset: a closure learned from one dataset is not assumed
        for another. Not a partition and not a trading-day fact: nothing reads it as data."""
        marker = self._closure_marker(dataset, market, run_date)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(detail, encoding="utf-8")

    def clear_closed(self, dataset: str, market: str, run_date: date_) -> None:
        """Forget a recorded closure (data for the day turned up after all)."""
        self._closure_marker(dataset, market, run_date).unlink(missing_ok=True)

    def is_closed(self, dataset: str, market: str, run_date: date_) -> bool:
        """True when an earlier run confirmed `run_date` as a market closure for `dataset`."""
        return self._closure_marker(dataset, market, run_date).is_file()

    def _plan_denial_marker(self, market: str, capability: str) -> Path:
        return self.root / _PLAN_DENIALS_DIRNAME / market / capability

    def mark_plan_denied(
        self, market: str, capability: str, *, now: datetime | None = None
    ) -> None:
        """Remember that the FinMind plan does not include `capability`, so a run does not send
        every remaining date to be refused. Written once per learned denial; never read as data."""
        marker = self._plan_denial_marker(market, capability)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text((now or datetime.now(UTC)).isoformat(), encoding="utf-8")

    def is_plan_denied(
        self, market: str, capability: str, *, ttl: timedelta, now: datetime | None = None
    ) -> bool:
        """True when a denial for `capability` was learned less than `ttl` ago. An older (or
        unreadable) marker no longer counts, so the plan is asked again and a plan upgrade is
        noticed without any code change."""
        marker = self._plan_denial_marker(market, capability)
        try:
            learned = datetime.fromisoformat(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False
        return (now or datetime.now(UTC)) - learned < ttl

    def clear_plan_denials(self) -> int:
        """Forget every recorded plan denial (the plan changed; ask FinMind again now). Returns
        how many were forgotten."""
        base = self.root / _PLAN_DENIALS_DIRNAME
        markers = [p for p in base.rglob("*") if p.is_file()] if base.is_dir() else []
        for marker in markers:
            marker.unlink(missing_ok=True)
        return len(markers)

    def has_partition(self, dataset: str, market: str, run_date: date_) -> bool:
        """True only if the manifest exists AND the version it points at is on disk, so a torn
        partition (manifest naming a missing directory) counts as a gap that backfill repairs."""
        partition_dir = PartitionKey(dataset=dataset, market=market, run_date=run_date).path(
            self.root
        )
        version = self._manifest_version(partition_dir)
        return version is not None and (partition_dir / f"v={version}").is_dir()

    def has_rows(self, dataset: str, market: str, run_date: date_) -> bool:
        """True when `run_date` has a real, non-empty partition. Unlike `has_partition`, a zero-row
        `_empty` placeholder (an upstream that answered but had nothing yet) does not count, so a
        gap check built on this retries that day instead of treating it as filled."""
        return run_date in self.list_partition_dates(dataset, market)

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
        globs = self._existing_globs(dataset, market, run_dates)
        if not globs or not symbols:
            return []

        # `placeholders` is only ever a run of `?, ?, ...` (one per `symbols` item) — never
        # interpolated user data; every actual value is still bound via `execute`'s parameter
        # list below, so this is not the injection pattern ruff's S608 flags.
        placeholders = ", ".join("?" for _ in symbols)
        con = duckdb.connect()
        try:
            relation = con.execute(
                "SELECT * FROM read_parquet(?, union_by_name => true) "  # noqa: S608
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
        where: str | None = None,
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

        `where` is an optional SQL predicate over the dataset's own columns, applied inside DuckDB
        so the rows it excludes are never turned into a DataFrame at all (a measured 5.67M-row
        price window is 1.47 GB as a frame; the same window cut to the ~10% that are real equities
        and ETFs is a fraction of that). It is a trusted fragment the caller builds from constants
        (`analytics.symbol_universe.scoreable_symbol_sql`), never from request input.
        """
        globs = self._existing_globs(dataset, market, run_dates)

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
                f"WHERE {columns[0]} IS NOT NULL" + (f" AND ({where})" if where else ""),
                [globs],
            )
            return relation.df()
        finally:
            con.close()

    def _latest_by_key_sql(  # noqa: PLR0913 - one keyword per read dimension
        self,
        dataset: str,
        market: str,
        run_dates: Sequence[date_],
        *,
        columns: Sequence[str],
        key_columns: Sequence[str],
        order_by: str | None,
    ) -> tuple[str, list[str]] | None:
        """The query behind both latest-by-key reads, and its glob parameter; `None` when no date
        in the window was ever written."""
        globs = self._existing_globs(dataset, market, run_dates)
        if not globs:
            return None
        column_list = ", ".join(columns)
        key_list = ", ".join(key_columns)
        sql = (
            f"SELECT {column_list} FROM "  # noqa: S608 - code-side literal identifiers
            "read_parquet(?, union_by_name=True, hive_partitioning=True) "
            f"WHERE {key_columns[0]} IS NOT NULL "
            f"QUALIFY row_number() OVER (PARTITION BY {key_list} ORDER BY date DESC) = 1"
        )
        if order_by:
            sql += f" ORDER BY {order_by}"
        return sql, globs

    def read_range_latest_by_key_frame(  # noqa: PLR0913 - one keyword per read dimension
        self,
        dataset: str,
        market: str,
        run_dates: Sequence[date_],
        *,
        columns: Sequence[str],
        key_columns: Sequence[str],
        order_by: str,
    ) -> pd.DataFrame:
        """`read_range_latest_by_key` as a DataFrame, in a **defined order** (`order_by`, a
        code-side SQL fragment).

        The row-dict form returns its rows in whatever order DuckDB's parallel window happens to
        produce: two runs of the same query over the same files gave different orders (measured).
        A caller that takes "the first matching line" per symbol then gets a different answer from
        one night to the next, so a caller that cares says how to order them. As a frame the
        ~4M-row US statement window is also not boxed into 4M dicts first."""
        query = self._latest_by_key_sql(
            dataset,
            market,
            run_dates,
            columns=columns,
            key_columns=key_columns,
            order_by=order_by,
        )
        if query is None:
            return pd.DataFrame({column: pd.Series([], dtype="object") for column in columns})
        sql, globs = query
        con = duckdb.connect()
        try:
            return con.execute(sql, [globs]).df()
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
        query = self._latest_by_key_sql(
            dataset,
            market,
            run_dates,
            columns=columns,
            key_columns=key_columns,
            order_by=None,
        )
        if query is None:
            return []
        sql, globs = query
        con = duckdb.connect()
        try:
            relation = con.execute(sql, [globs])
            names = [d[0] for d in relation.description or []]
            return [dict(zip(names, row, strict=True)) for row in relation.fetchall()]
        finally:
            con.close()


__all__ = ["ParquetStore", "PartitionKey"]
