"""FinMind adapter package — the history/backfill engine for FinMind's 100+ datasets.

Re-exports `FinMindAdapter`, the public seam every caller (`dataaccess/`, `ingest/`) imports as
`from sources.finmind import FinMindAdapter`. Also re-exports `build_async_client` so tests can
patch the HTTP-client factory the adapter calls through, exactly as they could when this was a
single module.
"""

from __future__ import annotations

from sources.tls import build_async_client

from ._adapter import FinMindAdapter

__all__ = ["FinMindAdapter", "build_async_client"]
