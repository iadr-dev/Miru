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

from redis.asyncio import Redis

from config.settings import Settings, get_settings
from dataaccess.router import DataAccessRouter
from sources.base import SourceAdapter
from sources.caching_adapter import build_caching_adapter
from sources.etf_pcf import EtfPcfAdapter
from sources.etf_theme_tags import EtfThemeTagsAdapter
from sources.finmind import FinMindAdapter
from sources.mops import MopsAdapter
from sources.ncdr_closure import NcdrClosureAdapter
from sources.news import NewsAdapter
from sources.sitca import SitcaAdapter
from sources.taifex_openapi import TaifexOpenApiAdapter
from sources.tdcc import TdccAdapter
from sources.twse_mis import TwseMisAdapter
from sources.twse_openapi import TwseOpenApiAdapter
from sources.yfinance import YFinanceAdapter
from sources.yfinance.live_quote_adapter import YFinanceLiveQuoteAdapter
from sources.yfinance.live_quote_stream import YFinanceLiveQuoteManager

# Excluded from the Redis caching-adapter wrap below: caching a live WebSocket tick behind a TTL
# defeats the entire point (staleness), and the wrapper's read-through semantics assume an
# idempotent re-fetch, not a stateful subscribe side effect.
_UNCACHEABLE_SOURCE_IDS = frozenset({"yfinance_live"})


def build_default_adapters(
    settings: Settings | None = None,
    redis: Redis | None = None,
    live_quote_manager: YFinanceLiveQuoteManager | None = None,
) -> dict[str, SourceAdapter]:
    """Instantiate every wired source adapter keyed by its priority-config source id.

    When `market_data_cache_backend == "redis"`, every adapter is wrapped in
    `CachingSourceAdapter` (infra §3/§4's read-through hot-cache tier) before being handed to the
    router — a drop-in substitution since the wrapper implements the same `SourceAdapter`
    protocol, so `DataAccessRouter`/callers never know the difference. Defaults to `none`
    (unwrapped, real adapters only) so the existing non-network test suite's expectations of an
    uncached adapter are unaffected unless a deployment opts in via config. `live_quote_manager`
    is threaded in (rather than constructed here unconditionally) so the composition root
    (`api/composition.py`) can share one instance between this adapter and the app's shutdown
    hook — defaults to a fresh manager when not supplied (e.g. every non-API composition root:
    `mcp/`, `orchestrator/`, `scheduler/`, none of which serve `/context/quote/stream` or need
    the shutdown hook, so a manager that's simply never subscribed is harmless).
    """
    resolved = settings or get_settings()
    adapters: dict[str, SourceAdapter] = {
        "finmind": FinMindAdapter(resolved),
        "twse_openapi": TwseOpenApiAdapter(),
        "twse_mis": TwseMisAdapter(),
        "taifex_openapi": TaifexOpenApiAdapter(),
        "yfinance": YFinanceAdapter(),
        "yfinance_live": YFinanceLiveQuoteAdapter(manager=live_quote_manager),
        "news": NewsAdapter(resolved),
        "etf_pcf": EtfPcfAdapter(),
        "etf_theme_tags": EtfThemeTagsAdapter(),
        "sitca": SitcaAdapter(),
        "mops": MopsAdapter(),
        "ncdr_closure": NcdrClosureAdapter(),
        "tdcc": TdccAdapter(),
    }
    if resolved.market_data_cache_backend != "redis":
        return adapters
    client: Redis = redis if redis is not None else Redis.from_url(resolved.redis_url)
    cached: dict[str, SourceAdapter] = {}
    for source_id, adapter in adapters.items():
        cached[source_id] = (
            adapter
            if source_id in _UNCACHEABLE_SOURCE_IDS
            else build_caching_adapter(adapter, client, resolved)
        )
    return cached


def build_default_router(
    settings: Settings | None = None,
    redis: Redis | None = None,
    live_quote_manager: YFinanceLiveQuoteManager | None = None,
) -> DataAccessRouter:
    """Assemble the full router from the wired adapters + the priority config on disk."""
    return DataAccessRouter(build_default_adapters(settings, redis, live_quote_manager))


def build_live_quote_manager() -> YFinanceLiveQuoteManager:
    """A fresh `YFinanceLiveQuoteManager` — the one factory an interfaces-layer composition root
    (`api/composition.py`) calls to get an instance it can share between `build_default_router`
    (so the resolved `YFinanceLiveQuoteAdapter` uses it) and `create_app`'s shutdown hook.
    `api/` may not import `sources/` directly (layering test) — this is the sanctioned seam."""
    return YFinanceLiveQuoteManager()


__all__ = ["build_default_adapters", "build_default_router", "build_live_quote_manager"]
