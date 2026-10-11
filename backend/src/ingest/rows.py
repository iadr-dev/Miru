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


__all__ = ["rows_from_store"]
