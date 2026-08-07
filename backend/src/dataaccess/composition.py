"""The production data-access composition root — the single place the real source adapters are
assembled into a `DataAccessRouter`.

`dataaccess/` is one of the only two layers permitted to import `sources/` (the other is
`ingest/`), so adapter instantiation belongs here rather than at an interface-layer wiring site.
The dict keys are the source ids used in `config/source_priority.yaml`; the router resolves a
`(market, capability)` request against those keys, so a key that isn't wired here is simply skipped
(logged) rather than crashing — a partially-configured deployment still serves what it can.

Adapters that only need env config take `Settings`; the rest are zero-arg. No network I/O happens
at construction time — building the router never opens a socket.
"""

from __future__ import annotations

from config.settings import Settings, get_settings
from dataaccess.router import DataAccessRouter
from sources.base import SourceAdapter
from sources.etf_pcf import EtfPcfAdapter
from sources.finmind import FinMindAdapter
from sources.mops import MopsAdapter
from sources.news import NewsAdapter
from sources.taifex_openapi import TaifexOpenApiAdapter
from sources.twse_mis import TwseMisAdapter
from sources.twse_openapi import TwseOpenApiAdapter
from sources.yfinance import YFinanceAdapter


def build_default_adapters(settings: Settings | None = None) -> dict[str, SourceAdapter]:
    """Instantiate every wired source adapter keyed by its priority-config source id."""
    resolved = settings or get_settings()
    return {
        "finmind": FinMindAdapter(resolved),
        "twse_openapi": TwseOpenApiAdapter(),
        "twse_mis": TwseMisAdapter(),
        "taifex_openapi": TaifexOpenApiAdapter(),
        "yfinance": YFinanceAdapter(),
        "news": NewsAdapter(resolved),
        "etf_pcf": EtfPcfAdapter(),
        "mops": MopsAdapter(),
    }


def build_default_router(settings: Settings | None = None) -> DataAccessRouter:
    """Assemble the full router from the wired adapters + the priority config on disk."""
    return DataAccessRouter(build_default_adapters(settings))


__all__ = ["build_default_adapters", "build_default_router"]
