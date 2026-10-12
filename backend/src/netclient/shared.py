"""Pooled connections, with no state shared between callers.

What is worth sharing is the transport: its keep-alive connections and TLS sessions, so a new
request does not pay a fresh TCP + TLS handshake. What must not be shared is anything an upstream
can put *state* in. An `httpx.AsyncClient` carries a cookie jar, so one client reused by everyone
would send a cookie set in answer to one caller's request on the next caller's, which is a
cross-tenant leak when callers are different users. So the pool holds transports only; every
`async with lease_client(...)` gets a fresh client with its own empty cookie jar, its own headers
and its own timeout, on top of the shared transport."""

from __future__ import annotations

import asyncio
import weakref
from types import TracebackType

import httpx

from netclient.retry import RetryingTransport

# Idle keep-alive connections are dropped quickly, so a server that closes idle sockets does not
# hand the next caller a dead one.
_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=15.0)

type _Key = tuple[bool, bool]  # (TLS verify, retry reads)

# A transport's connections are bound to the event loop that opened them, so the pool is per loop.
# A WeakKey mapping lets a finished loop (and its connections) be collected: tests create one per
# test.
_transports: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[_Key, httpx.AsyncBaseTransport]
] = weakref.WeakKeyDictionary()


def _shared_transport(*, verify: bool, retry_reads: bool) -> httpx.AsyncBaseTransport:
    per_loop = _transports.setdefault(asyncio.get_running_loop(), {})
    key: _Key = (verify, retry_reads)
    transport = per_loop.get(key)
    if transport is None:
        # `retries=1` is httpx's own retry of a failed *connection attempt* only: nothing was
        # sent, so it is safe for every method.
        transport = httpx.AsyncHTTPTransport(verify=verify, limits=_LIMITS, retries=1)
        if retry_reads:
            transport = RetryingTransport(transport)
        per_loop[key] = transport
    return transport


class SharedClientLease:
    """What `lease_client` returns. `async with` yields a client that uses the shared connection
    pool but has its own cookie jar; leaving the block leaves the pool open.

    The client is built on entry, inside the running loop, so making a lease needs no loop."""

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
        transport = _shared_transport(verify=self._verify, retry_reads=self._retry_reads)
        # Not closed on exit: `AsyncClient.aclose()` would close the shared transport with it.
        return httpx.AsyncClient(transport=transport, timeout=self._timeout, headers=self._headers)

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
    """A lease on the shared connection pool, with a private client (cookies, headers, timeout).

    `retry_reads` adds a bounded retry of GET/HEAD on transient failures; leave it off wherever a
    repeated request costs something (a metered API) or governance forbids retrying."""
    return SharedClientLease(
        verify=verify, timeout=timeout, headers=headers, retry_reads=retry_reads
    )


async def aclose_shared_clients() -> None:
    """Close the running loop's pooled connections - call once at shutdown."""
    per_loop = _transports.pop(asyncio.get_running_loop(), {})
    for transport in per_loop.values():
        await transport.aclose()


__all__ = ["SharedClientLease", "aclose_shared_clients", "lease_client"]
