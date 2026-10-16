"""Row helpers for data read back from the Parquet store."""

from __future__ import annotations

from pydantic import BaseModel


def rows_from_store[T: BaseModel](model: type[T], raw_rows: list[dict[str, object]]) -> list[T]:
    """`ParquetStore.read_partition`/`read_range` hand back DuckDB's own read-back of a
    Hive-partitioned glob, which resurfaces the partition-key columns (`dataset`/`date`/`v`)
    alongside the written row data. Those are storage bookkeeping, not part of any
    `schemas.capabilities` contract, so they are dropped before validating against an
    `extra="forbid"` model rather than relaxing that model's strictness."""
    field_names = set(model.model_fields)
    return [
        model.model_validate({k: v for k, v in row.items() if k in field_names}) for row in raw_rows
    ]


_PATH_COLUMNS = frozenset({"dataset", "market", "date", "v"})


def without_path_columns(
    raw_rows: list[dict[str, object]], *, keep: frozenset[str] = frozenset()
) -> list[dict[str, object]]:
    """Rows read back from the store minus the partition-path columns (`dataset`/`market`/`date`/
    `v`) the Hive read adds. A job that rewrites a partition from rows it just read must not write
    those back as data columns; `keep` names the ones that are real data columns of the dataset
    (e.g. `market` on 注意股 rows)."""
    drop = _PATH_COLUMNS - keep
    return [{k: v for k, v in row.items() if k not in drop} for row in raw_rows]


__all__ = ["rows_from_store", "without_path_columns"]
