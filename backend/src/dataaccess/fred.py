"""The `dataaccess/` seam for the US macro statistics source - the one place the ingest layer
reaches `sources.fred.FredAdapter` from.

There is no `(capability, market)` router entry for it (a series query has no symbol to route),
the same non-router carve-out `dataaccess/financecalendar.py` and `dataaccess/news.py` establish.
Only the ingest job calls it: the request-path tools read the Parquet store the job writes and
never fetch from the upstream.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from schemas.enums import TLSPolicy
from schemas.source import SourceResult
from sources.fred import FredAdapter


class FredGateway:
    """Thin `dataaccess/`-layer wrapper around `FredAdapter`."""

    def __init__(self, adapter: FredAdapter | None = None) -> None:
        self._adapter = adapter or FredAdapter()

    @property
    def is_configured(self) -> bool:
        return self._adapter.is_configured

    async def series_metas(
        self,
        series_ids: Sequence[str],
        *,
        concurrency: int = 6,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> dict[str, SourceResult]:
        return await self._adapter.series_metas(
            series_ids, concurrency=concurrency, tls_policy=tls_policy
        )

    async def recent_series_updates(
        self, *, pages: int = 3, tls_policy: TLSPolicy = TLSPolicy.STRICT
    ) -> SourceResult:
        return await self._adapter.recent_series_updates(pages=pages, tls_policy=tls_policy)

    async def fetch_all_observations(  # noqa: PLR0913 - mirrors the adapter
        self,
        series_id: str,
        *,
        units: str | None = None,
        observation_start: date | None = None,
        observation_end: date | None = None,
        realtime_start: date | None = None,
        realtime_end: date | None = None,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        return await self._adapter.fetch_all_observations(
            series_id,
            units=units,
            observation_start=observation_start,
            observation_end=observation_end,
            realtime_start=realtime_start,
            realtime_end=realtime_end,
            tls_policy=tls_policy,
        )

    async def release_dates(
        self,
        *,
        from_date: date,
        to_date: date,
        include_future: bool = True,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        return await self._adapter.release_dates(
            from_date=from_date,
            to_date=to_date,
            include_future=include_future,
            tls_policy=tls_policy,
        )

    async def vintage_dates(
        self,
        series_id: str,
        *,
        sort_order: str = "asc",
        limit: int = 10_000,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        return await self._adapter.vintage_dates(
            series_id, sort_order=sort_order, limit=limit, tls_policy=tls_policy
        )

    async def bulk_release_observations(
        self,
        release_id: int,
        *,
        cursor: str | None = None,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """UNVERIFIED v2 bulk method - see `FredAdapter.bulk_release_observations`."""
        return await self._adapter.bulk_release_observations(
            release_id, cursor=cursor, tls_policy=tls_policy
        )


__all__ = ["FredGateway"]
