"""The read-only side of the derived store: an API-worker reader over the same Hive-partitioned
Parquet tree the leader ingest process writes.

This is the counterpart to `store.py`'s writer, kept strictly separate per DuckDB's documented
one-writer-OR-many-readers rule: a single `.duckdb` file supports either one read-write process
or many read-only processes, never both. Our topology is one leader *writer* plus many API-worker
*reader* processes, so a reader must **never** open the writer's file. This reader instead opens
the Parquet directly through a per-worker in-memory DuckDB connection
(`read_parquet(glob, hive_partitioning=true)`), which takes no write lock — any number of reader
processes read the same tree concurrently while the leader writes elsewhere.

Each partition is resolved through its own `_manifest.json` (the writer's atomic pointer), so a
glob never straddles an in-progress write and never reads an orphaned older version. A partition
that was never ingested degrades to an empty result, not an error — the same honest-degradation
pattern the writer's own `read_partition` uses and that the whole codebase relies on.

The reader lives here beside the writer rather than in `context/` because the architecture keeps
market-data I/O out of the request-path context layer: `context/` names a capability and lets a
lower layer touch the store. This module is that lower layer's read implementation.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date as date_
from pathlib import Path

import duckdb
import structlog

from ingest.store import PartitionKey

logger = structlog.get_logger(__name__)

_MANIFEST_NAME = "_manifest.json"


class ParquetMarketDataReader:
    """A read-only reader over one `parquet_root` tree, backed by a reused per-instance in-memory
    DuckDB connection. Construct one per worker and reuse it — DuckDB is fastest reusing a
    connection, and this one is read-only over Parquet, holding no write lock and never touching
    the writer's `.duckdb` file.

    Not for concurrent use from multiple asyncio tasks on one instance: a DuckDB connection is not
    safe to share across concurrent queries. The request path treats a read as blocking work to
    run in a threadpool; give each such worker its own reader (or serialize through one)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        # `:memory:` — no file, no lock. Purely a query engine over external Parquet files.
        self._con = duckdb.connect(":memory:")

    def close(self) -> None:
        self._con.close()

    def _resolve_version_dir(self, partition_dir: Path) -> Path | None:
        """Read the manifest pointer and return the current version dir, or `None` if the
        partition has no manifest (never ingested) or points at a missing version (an
        interrupted/half-cleaned state — treated as "no data", never a stale read)."""
        manifest_path = partition_dir / _MANIFEST_NAME
        if not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            version_id = manifest["version"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None
        version_dir = partition_dir / f"v={version_id}"
        return version_dir if version_dir.exists() else None

    def read_partition(self, dataset: str, market: str, run_date: date_) -> list[dict[str, object]]:
        """Read one `(dataset, market, date)` cell read-only, resolving its manifest first.

        Returns `[]` for a partition that was never ingested — the caller decides whether that is
        a gap to backfill or simply "no data for this cell"; it is never an exception here."""
        key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
        version_dir = self._resolve_version_dir(key.path(self.root))
        if version_dir is None:
            return []
        parquet_glob = str(version_dir / "*.parquet")
        # `union_by_name`: a genuinely-empty trading day writes a placeholder `_empty` column
        # (`ParquetStore.write_partition`'s empty-rows case), a different schema than a real-data
        # write for the same dataset — `read_range` globs across dates that can mix both, and
        # DuckDB's default glob read rejects any schema mismatch outright.
        relation = self._con.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning => true, union_by_name => true)",
            [parquet_glob],
        )
        columns = [d[0] for d in relation.description or []]
        return [dict(zip(columns, row, strict=True)) for row in relation.fetchall()]

    def read_range(
        self,
        dataset: str,
        market: str,
        run_dates: Sequence[date_],
        *,
        symbols: Sequence[str] | None = None,
    ) -> list[dict[str, object]]:
        """Read several date partitions in order, silently skipping any never-ingested date. Each
        partition is still resolved through its own manifest, so this is `read_partition` applied
        per date — one Parquet-reading path, never a mid-write glob across the whole tree."""
        rows: list[dict[str, object]] = []
        for run_date in run_dates:
            rows.extend(self.read_partition(dataset, market, run_date))
        if symbols is None:
            return rows
        wanted = set(symbols)
        return [row for row in rows if row.get("symbol") in wanted]

    def has_partition(self, dataset: str, market: str, run_date: date_) -> bool:
        """Whether a resolvable (manifest-pointed, version-present) partition exists — lets a
        caller distinguish "no data" from "not yet ingested" without reading the rows."""
        key = PartitionKey(dataset=dataset, market=market, run_date=run_date)
        return self._resolve_version_dir(key.path(self.root)) is not None


__all__ = ["ParquetMarketDataReader"]
