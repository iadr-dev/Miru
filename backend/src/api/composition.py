"""The production application-composition root — assembles the real, fully-wired FastAPI app from
config/env and exposes the module-level ASGI `app` that `uvicorn api.app:app`-style entrypoints
import directly.

This is deliberately the interfaces layer: it may import the shared interaction-layer builders
(`orchestrator.composition`) that assemble the collaborator graph, and it is the one place the
concrete app is stitched together from `Settings`. Everything is config-selected, never hardcoded —
the LLM clients resolve their provider/model per role at call time (so the app boots with no key
set and degrades at the point a specific provider is actually needed, never at import), and the
data-access router is assembled from the on-disk priority config.

No network I/O happens here: `create_app` performs no startup fetch and the collaborator graph is
network-free at construction.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.app import create_app
from config.logging_config import configure_logging
from config.settings import Settings, get_settings
from dataaccess.composition import build_default_router, build_live_quote_manager
from orchestrator.composition import (
    build_orchestrator,
    build_session_factory,
    build_skill_registry,
    build_tool_registry,
)
from personality.registry import default_registry as default_personality_registry


def build_app(settings: Settings | None = None) -> FastAPI:
    """Assemble the fully-wired FastAPI app from real config — the composition root a container
    entrypoint invokes."""
    resolved = settings or get_settings()
    configure_logging(resolved)
    session_factory = build_session_factory(resolved)
    # One process-lifetime manager, shared between the `YFinanceLiveQuoteAdapter` the router
    # resolves `Capability.LIVE_PRICE_QUOTE` to and `create_app`'s own shutdown hook — otherwise
    # the hook would close a WebSocket connection nobody is actually serving traffic through.
    # Built via `dataaccess.composition` (not imported from `sources/` directly) — `api/` may
    # not import `sources/` (layering test), so this stays a downward `dataaccess` call.
    live_quote_manager = build_live_quote_manager()
    router = build_default_router(resolved, live_quote_manager=live_quote_manager)
    tool_registry = build_tool_registry(router, session_factory=session_factory)
    skill_registry = build_skill_registry()
    # Built once and shared: the orchestrator resolves the turn's personality fragment from it,
    # `AppState.personality_registry` is the same instance so `/accounts` validates a chosen
    # preset id against exactly the set the orchestrator can actually resolve.
    personality_registry = default_personality_registry()
    orchestrator = build_orchestrator(
        skill_registry=skill_registry,
        tool_registry=tool_registry,
        settings=resolved,
        personality_registry=personality_registry,
    )
    return create_app(
        session_factory=session_factory,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        orchestrator=orchestrator,
        settings=resolved,
        personality_registry=personality_registry,
        live_quote_manager=live_quote_manager,
        allowed_origins=resolved.allowed_origins() or None,
    )


__all__ = ["build_app"]
