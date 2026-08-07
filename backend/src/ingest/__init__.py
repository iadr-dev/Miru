from __future__ import annotations

from config import get_settings
from ingest.reader import ParquetMarketDataReader
from ingest.store import ParquetStore, PartitionKey


def build_market_data_reader() -> ParquetMarketDataReader:
    """Construct the read-only derived-store reader from config — the API-worker read path over
    the leader's Parquet tree. One per worker; reuse it (DuckDB is fastest reusing a connection)."""
    return ParquetMarketDataReader(root=get_settings().parquet_root)


__all__ = [
    "ParquetMarketDataReader",
    "ParquetStore",
    "PartitionKey",
    "build_market_data_reader",
]
