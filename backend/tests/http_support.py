"""Test double for `netclient.lease_client`: an `async with` that yields a client built on a mock
transport and closes it afterwards (the real lease keeps its pooled client open)."""

from __future__ import annotations

from types import TracebackType

import httpx


class OwnedLease:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._client.aclose()
