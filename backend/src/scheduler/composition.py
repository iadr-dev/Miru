"""The production scheduler-process composition root — assembles the real `SchedulerWorker` graph
from config/env, mirroring how the API app is composed.

Reuses the shared interaction-layer builders (the real DB session factory, the built-in skill
registry, the config-assembled data-access router, the role-resolved orchestrator) plus the
scheduler-only collaborators: the config-selected delivery channels, the shared leader-election
seam (the same `LeaderElection` the ingest write path uses, so a real cross-process election swaps
in at this one site), and the durable usage-backed quota gate that `build_scheduler_worker`
defaults to.

No network I/O at construction time.
"""

from __future__ import annotations

from accounts.hooks import EntitlementResolver
from config.settings import Settings, get_settings
from dataaccess.composition import build_default_router
from db.base import ensure_schema_for
from ingest.leader import LeaderElection, get_default_leader_election
from orchestrator.composition import (
    build_orchestrator,
    build_session_factory,
    build_skill_registry,
    build_tool_registry,
)
from scheduler.delivery.channels import build_default_channels
from scheduler.worker import SchedulerWorker, build_scheduler_worker


async def build_scheduler_from_settings(settings: Settings | None = None) -> SchedulerWorker:
    """Assemble the real worker from config — the composition root the scheduler process invokes.

    `async def` (not sync) so it can `await ensure_schema_for(...)` on the caller's own already-
    running startup loop — this project's sole schema-provisioning mechanism, no Alembic
    migration chain."""
    resolved = settings or get_settings()
    session_factory = build_session_factory(resolved)
    await ensure_schema_for(session_factory)
    router = build_default_router(resolved)
    tool_registry = build_tool_registry(router, session_factory=session_factory)
    skill_registry = build_skill_registry()
    orchestrator = build_orchestrator(
        skill_registry=skill_registry,
        tool_registry=tool_registry,
        settings=resolved,
    )
    leader: LeaderElection = get_default_leader_election()
    return build_scheduler_worker(
        session_factory=session_factory,
        orchestrator=orchestrator,
        router=router,
        entitlement_resolver=EntitlementResolver(tls_policy=resolved.tls_policy),
        channels=build_default_channels(resolved),
        leader=leader,
    )


__all__ = ["build_scheduler_from_settings"]
