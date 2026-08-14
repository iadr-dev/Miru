"""TDCC (台灣集中保管結算所, Taiwan Depository & Clearing Corporation) OpenData adapter package.

Re-exports `TdccAdapter`, the public seam every caller (`dataaccess/`) imports as
`from sources.tdcc import TdccAdapter`. Also re-exports `build_async_client` so tests can patch
the HTTP-client factory the adapter calls through, mirroring `sources/finmind/__init__.py`'s
same re-export shape.
"""

from __future__ import annotations

from sources.tls import build_async_client

from ._adapter import TdccAdapter
from .raw_query import raw_query

__all__ = ["TdccAdapter", "build_async_client", "raw_query"]
