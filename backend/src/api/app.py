"""The FastAPI application factory.

`create_app(...)` assembles the router set, the shared `AppState`, the standardized error
handlers, and CORS, then returns a ready `FastAPI`. It performs **no startup network calls** —
the app boots clean (the leader scheduler owns ingest in a later phase). Every long-lived
collaborator (tool registry, skill registry, orchestrator) is injected so a test can pass stubs
and a real deployment can pass the fully-wired objects; the DB session factory is likewise
injected (built from `settings.database_url` by the caller) so tests can point at an in-memory
SQLite engine.

Layering: this is the interfaces layer — it may import product/interaction/runtime collaborators
below it (accounts, orchestrator, skills, tools), never the reverse.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from accounts.hooks import EntitlementResolver
from api.auth_expired import AUTH_EXPIRED_HEADER, AuthExpiredHeaderMiddleware
from api.dependencies import AppState
from api.errors import register_exception_handlers
from api.etag import ConditionalGetMiddleware
from api.quote_stream import CalendarMarketOpenGate, LiveQuoteManagerLike, ToolQuoteFetcher
from api.request_context import REQUEST_ID_HEADER, RequestContextMiddleware
from api.routers import (
    accounts_extra,
    agent,
    analytics,
    auth,
    billing,
    context,
    fund_data,
    market,
    market_rest,
    memory,
    oauth,
    pets,
    probes,
    scheduler,
    themes,
    threads,
    threads_actions,
    voice,
)
from api.screener_warm import keep_screener_warm
from api.turn_registry import TurnRegistry
from config.settings import Settings, get_settings
from db import rls
from db.base import dispose_engine_for, ensure_schema_for
from events.factory import EventBusFactory, build_event_bus_factory
from events.live_quote import QuoteFeed
from events.live_quote_factory import DataUpdateBroker, build_data_update_broker, build_quote_feed
from events.rate_limit import AsyncRateLimiter, build_rate_limiter
from events.turn_control import TurnControl, build_turn_control
from ingest.store import ParquetStore
from netclient import aclose_shared_clients
from orchestrator.orchestrator import Orchestrator
from personality.registry import PersonalityRegistry, default_registry
from skills.registry import SkillRegistry
from tools.registry import ToolRegistry
from usage.store import SqlAlchemyUsageStore, UsageStore


def _start_screener_warm(settings: Settings, broker: DataUpdateBroker) -> asyncio.Task[None] | None:
    """Keep the screener's whole-market reads ready after each ingest. Only with the Redis
    backend: that is the hosted topology where the ingest signal reaches the API from another
    process (the in-memory broker only carries a signal published inside this process)."""
    if settings.event_bus_backend != "redis":
        return None
    store = ParquetStore(root=settings.parquet_root)
    return asyncio.create_task(keep_screener_warm(broker.reader_factory.updates, store))


def create_app(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    tool_registry: ToolRegistry,
    skill_registry: SkillRegistry,
    orchestrator: Orchestrator,
    settings: Settings | None = None,
    entitlement_resolver: EntitlementResolver | None = None,
    personality_registry: PersonalityRegistry | None = None,
    event_bus_factory: EventBusFactory | None = None,
    rate_limiter: AsyncRateLimiter | None = None,
    usage_store: UsageStore | None = None,
    quote_feed: QuoteFeed | None = None,
    data_update_broker: DataUpdateBroker | None = None,
    live_quote_manager: LiveQuoteManagerLike | None = None,
    turn_control: TurnControl | None = None,
    allowed_origins: list[str] | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    rls.install(enabled=resolved_settings.row_level_security)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # `create_all()` is idempotent — safe even if the caller (a test fixture) already
        # created the schema itself, and safe under multiple replicas racing to call it.
        await ensure_schema_for(session_factory)
        app.state.app_state.shutdown.install()
        warm_task = _start_screener_warm(resolved_settings, resolved_data_update_broker)
        try:
            yield
        finally:
            if warm_task is not None:
                warm_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await warm_task
            # `live_quote_manager` is the SAME instance the composition root threaded into
            # `YFinanceLiveQuoteAdapter` (see `api/composition.py`) — closing it here on shutdown
            # cancels its background listen/sweep tasks and closes the WebSocket connection.
            # `quote_feed`'s own poller has no equivalent shutdown hook today (a pre-existing
            # gap this doesn't fix) — not repeating that gap for the new manager.
            if live_quote_manager is not None:
                await live_quote_manager.aclose()
            await dispose_engine_for(session_factory)
            await aclose_shared_clients()

    app = FastAPI(title="Miru API", version="0.1.0", lifespan=_lifespan)

    resolved_quote_feed = quote_feed or build_quote_feed(
        resolved_settings,
        fetcher=ToolQuoteFetcher(tool_registry, tls_policy=resolved_settings.tls_policy),
        market_gate=CalendarMarketOpenGate(),
    )
    resolved_data_update_broker = data_update_broker or build_data_update_broker(resolved_settings)

    app.state.app_state = AppState(
        settings=resolved_settings,
        session_factory=session_factory,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        orchestrator=orchestrator,
        entitlement_resolver=entitlement_resolver
        or EntitlementResolver(tls_policy=resolved_settings.tls_policy),
        personality_registry=personality_registry or default_registry(),
        turn_registry=TurnRegistry(),
        event_bus_factory=event_bus_factory
        or build_event_bus_factory(resolved_settings, session_factory),
        rate_limiter=rate_limiter or build_rate_limiter(resolved_settings),
        usage_store=usage_store or SqlAlchemyUsageStore(session_factory),
        quote_feed=resolved_quote_feed,
        data_update_broker=resolved_data_update_broker,
        turn_control=turn_control or build_turn_control(resolved_settings),
    )

    # Added before CORS so CORS wraps it (Starlette applies the last-added middleware outermost);
    # the header must also be exposed or a cross-origin client can't read it.
    # Innermost of the three, so a 304 still gets the auth-expired header and CORS headers.
    app.add_middleware(ConditionalGetMiddleware)
    app.add_middleware(AuthExpiredHeaderMiddleware)
    # Outside the other two so it times the whole request and its id is on every response.
    app.add_middleware(RequestContextMiddleware)
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[AUTH_EXPIRED_HEADER, REQUEST_ID_HEADER, "X-Thread-Id", "X-Turn-Id"],
        )

    register_exception_handlers(app)

    api = "/api/v1"
    app.include_router(market.router, prefix=api)
    app.include_router(market_rest.router, prefix=api)
    app.include_router(context.router, prefix=api)
    app.include_router(context.stream_router, prefix=api)
    app.include_router(auth.router, prefix=api)
    app.include_router(oauth.web_router, prefix=api)
    app.include_router(accounts_extra.router, prefix=api)
    app.include_router(billing.router, prefix=api)
    # threads_actions first: its static `/threads/search` must be matched before threads.py's
    # `/threads/{thread_id}` dynamic route (FastAPI matches in registration order).
    app.include_router(threads_actions.router, prefix=api)
    app.include_router(threads.router, prefix=api)
    app.include_router(agent.router, prefix=api)
    app.include_router(scheduler.router, prefix=api)
    app.include_router(voice.router, prefix=api)
    app.include_router(pets.router, prefix=api)
    app.include_router(memory.router, prefix=api)
    app.include_router(themes.router, prefix=api)
    app.include_router(analytics.router, prefix=api)
    app.include_router(fund_data.router, prefix=api)

    # Infra probes (`/livez`, `/readyz`, `/startupz`) are mounted at the root, not under the API
    # version prefix — they're for the load balancer / orchestrator, not an API consumer.
    app.include_router(probes.router)
    # The MCP sign-in protocol endpoints live at the host root: issuer metadata (RFC 8414) is
    # discovered at a fixed well-known path, and it names the rest.
    app.include_router(oauth.router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction] — registered via decorator
        return {"status": "ok"}

    return app


def __getattr__(name: str) -> object:
    """Lazily expose a module-level `app` ASGI instance for `uvicorn api.app:app`.

    Built on first access from real env config via the composition root, so importing this
    module for its `create_app` factory (tests, other wiring) stays cheap and side-effect-free —
    the full collaborator graph is assembled only when a process actually asks for the runnable
    app. Imported inside the accessor to avoid a construction-time import cycle with the
    composition module (which imports this one).
    """
    if name == "app":
        from api.composition import build_app  # noqa: PLC0415 — deferred to break the import cycle

        built = build_app()
        globals()["app"] = built
        return built
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["create_app"]  # `app` is a lazily-built module attribute (see __getattr__)
