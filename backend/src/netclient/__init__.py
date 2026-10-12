"""Shared outbound HTTP clients: pooled connections and a bounded retry for safe reads.

Opening a new `httpx.AsyncClient` per call meant a fresh TCP + TLS handshake for every upstream
request. `lease_client` hands out one long-lived pooled client per (event loop, settings) and the
`async with` that callers already write no longer closes it. `RetryingTransport` retries a safe
read (GET/HEAD) a bounded number of times on a transient failure, with backoff and jitter.
"""

from netclient.retry import RetryingTransport
from netclient.shared import SharedClientLease, aclose_shared_clients, lease_client

__all__ = ["RetryingTransport", "SharedClientLease", "aclose_shared_clients", "lease_client"]
