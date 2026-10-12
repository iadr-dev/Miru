"""One pooled `httpx.AsyncClient` per (event loop, TLS verify, timeout, headers, retry)."""

from __future__ import annotations

import asyncio
import weakref
from types import TracebackType

import httpx

from netclient.retry import RetryingTransport

# Idle keep-alive connections are dropped quickly, so a server that closes idle sockets does not
# hand the next caller a dead one.
_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=15.0)

type _Key = tuple[bool, float, tuple[tuple[str, str], ...], bool]

# httpx clients are bound to the loop that first used them, so the pool is per loop. A WeakKey
# mapping lets a finished loop (and its clients) be collected - tests create one per test.
_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[_Key, httpx.AsyncClient]] = (
    weakref.WeakKeyDictionary()
)


class SharedClientLease:
    """What `lease_client` returns. `async with` yields the shared client and leaves it open.

    The client is resolved on entry, inside the running loop, so building a lease needs no loop
    (a client is bound to the loop that first uses it)."""

    def __init__(
        self,
        *,
        verify: bool,
        timeout: float,
        headers: dict[str, str] | None,
        retry_reads: bool,
    ) -> None:
        self._verify = verify
        self._timeout = timeout
        self._headers = headers
        self._retry_reads = retry_reads

    async def __aenter__(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        key: _Key = (
            self._verify,
            self._timeout,
            tuple(sorted((self._headers or {}).items())),
            self._retry_reads,
        )
        per_loop = _clients.setdefault(loop, {})
        client = per_loop.get(key)
        if client is None or client.is_closed:
            # `retries=1` is httpx's own retry of a failed *connection attempt* only: nothing was
            # sent, so it is safe for every method.
            transport: httpx.AsyncBaseTransport = httpx.AsyncHTTPTransport(
                verify=self._verify, limits=_LIMITS, retries=1
            )
            if self._retry_reads:
                transport = RetryingTransport(transport)
            client = httpx.AsyncClient(
                timeout=self._timeout, headers=self._headers, transport=transport
            )
            per_loop[key] = client
        return client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def lease_client(
    *,
    verify: bool = True,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
    retry_reads: bool = False,
) -> SharedClientLease:
    """A lease on the pooled client for these settings, created on first use in the running loop.

    `retry_reads` adds a bounded retry of GET/HEAD on transient failures; leave it off wherever a
    repeated request costs something (a metered API) or governance forbids retrying."""
    return SharedClientLease(
        verify=verify, timeout=timeout, headers=headers, retry_reads=retry_reads
    )


async def aclose_shared_clients() -> None:
    """Close the running loop's pooled clients - call once at shutdown."""
    per_loop = _clients.pop(asyncio.get_running_loop(), {})
    for client in per_loop.values():
        await client.aclose()


__all__ = ["SharedClientLease", "aclose_shared_clients", "lease_client"]
