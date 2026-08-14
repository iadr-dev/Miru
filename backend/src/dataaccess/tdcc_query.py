"""The `dataaccess/` seam for `get_tdcc_raw_dataset` — the one legitimate place `tools/` can
reach `sources.tdcc.raw_query` from.

A raw TDCC dataset id isn't `(capability, symbol)`-shaped (see `tools/tdcc_tools.py`'s
`GetTdccRawDatasetTool` docstring for the full rationale), so it cannot go through
`DataAccessRouter.fetch()`. Mirrors `dataaccess/web_search.py`'s exact shape: a thin wrapper
whose only job is to satisfy "`tools/` never imports `sources/` directly" for this one
non-router-shaped read.
"""

from __future__ import annotations

from schemas.enums import TLSPolicy
from schemas.source import SourceResult
from sources.tdcc.raw_query import raw_query


class TdccQueryGateway:
    """Thin `dataaccess/`-layer wrapper around `sources.tdcc.raw_query.raw_query`."""

    async def query(
        self,
        dataset_id: str,
        *,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
        symbol_column: str | None = None,
        symbol_value: str | None = None,
    ) -> SourceResult:
        return await raw_query(
            dataset_id,
            tls_policy=tls_policy,
            symbol_column=symbol_column,
            symbol_value=symbol_value,
        )


__all__ = ["TdccQueryGateway"]
