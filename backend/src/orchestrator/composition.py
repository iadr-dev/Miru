"""Shared, layer-safe builders for the real collaborator graph, used by every process role.

Both the API app (`api/composition.py`) and the standalone scheduler process
(`scheduler/composition.py`) need the same fully-wired session factory, skill registry, tool
registry, and orchestrator built from `Settings`. Those builders live here, in the interaction
layer, so both an interfaces-layer and a product-layer composition root can import them downward
without either process reaching across into the other's package (which the one-way dependency rule
forbids).

Everything is config-selected and network-free at construction time: the LLM clients resolve their
provider/model per role at call time, and the data-access router is assembled from the on-disk
priority config.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import skills
from config.settings import Settings, get_settings
from dataaccess.composition import build_default_router
from dataaccess.router import DataAccessRouter
from db.base import make_engine, make_session_factory
from ingest import build_market_data_reader
from orchestrator.orchestrator import Orchestrator
from personality.registry import PersonalityRegistry
from skills.registry import SkillRegistry
from tools import build_default_tool_registry
from tools.registry import ToolRegistry


def build_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """The real DB session factory built from `settings.database_url` (dialect-agnostic).

    Does NOT ensure the schema exists — this function is sync (called from sync composition
    roots) and schema creation needs a running event loop. Each process's own async startup
    path calls `db.base.ensure_schema_for(session_factory)` once, separately, to do that."""
    return make_session_factory(make_engine(settings.database_url))


def _built_in_skills_dir() -> Path:
    """The bundled built-in skill definitions ship inside the `skills` package."""
    return Path(skills.__file__).resolve().parent / "built_ins"


def build_skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.load([_built_in_skills_dir()])
    return registry


def build_tool_registry(
    router: DataAccessRouter | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ToolRegistry:
    """The fully-wired tool registry. `session_factory` backs the relational theme-lookup tools
    (which read the ingested theme store rather than a live source); the real composition roots
    thread the same factory they build for the request path, so a theme tool reads the one DB the
    app is pointed at. The derived-store reader backs the tag screener, which reads the ingest-
    owned Parquet flag store the nightly batch job writes — built from the same `parquet_root`
    config the rest of the app reads."""
    return build_default_tool_registry(
        router or build_default_router(),
        session_factory=session_factory,
        market_data_reader=build_market_data_reader(),
    )


def build_orchestrator(
    *,
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
    settings: Settings | None = None,
    personality_registry: PersonalityRegistry | None = None,
) -> Orchestrator:
    """The real orchestrator: role-resolved LLM clients (per call, from config), the built-in
    skill registry, and the tool lookup drawn from the fully-wired registry. No client is
    injected, so each role resolves through `llm/registry.py` at use time.

    `personality_registry` defaults to a freshly-loaded one when omitted; a caller that already
    built one (the API app builds exactly one and stashes it on `AppState` for `/accounts` to
    read the known-id list) passes it here too, so the app has a single loaded registry instance
    rather than parsing the preset YAML twice."""
    return Orchestrator(
        registry=skill_registry,
        tool_lookup={tool.name: tool for tool in tool_registry.all_tools()},
        settings=settings or get_settings(),
        personality_registry=personality_registry,
    )


__all__ = [
    "build_orchestrator",
    "build_session_factory",
    "build_skill_registry",
    "build_tool_registry",
]
