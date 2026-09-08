"""The `dataaccess/` seam for `get_ptt_board` — the one legitimate place `tools/` can reach
`sources.ptt.PttAdapter` from.

A PTT board name is a required per-call parameter, not a `(capability, market)` router entry, so
this tool cannot go through `DataAccessRouter.fetch()`. The architecture layering rule is still
"`tools/` never imports `sources/` directly" — this thin wrapper is the seam that satisfies that
rule, the same shape `dataaccess/web_search.py`'s `WebSearchGateway` already establishes for
`WEB`.
"""

from __future__ import annotations

from schemas.enums import TLSPolicy
from schemas.source import SourceResult
from sources.ptt import PttAdapter


class PttGateway:
    """Thin `dataaccess/`-layer wrapper around `PttAdapter` for `get_ptt_board`."""

    def __init__(self, adapter: PttAdapter | None = None) -> None:
        self._adapter = adapter or PttAdapter()

    async def fetch_board(
        self, *, board: str, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        return await self._adapter.fetch_board(board=board, tls_policy=tls_policy)


__all__ = ["PttGateway"]
