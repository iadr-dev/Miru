"""The production scheduler-process composition root — assembles the real `SchedulerWorker` graph
from config/env, mirroring how the API app is composed.

Reuses the shared interaction-layer builders (the real DB session factory, the built-in skill
registry, the config-assembled data-access router, the role-resolved orchestrator) plus the
scheduler-only collaborators: the config-selected delivery channels, the real single-run lease
(the mechanism the ingest writer uses, with role "scheduler": a Postgres advisory lock, or a file
lock on a single-host SQLite deployment, so a second scheduler process stands by), and the durable
usage-backed quota gate that `build_scheduler_worker` defaults to.

No network I/O at construction time.
"""

from __future__ import annotations

from accounts.account_reader import build_account_reader
from accounts.hooks import EntitlementResolver
from config.settings import Settings, get_settings, reveal
from dataaccess.composition import build_default_router
from db.base import ensure_schema_for
from events.live_quote_factory import build_data_update_broker
from ingest.leader import LeaderElection, build_leader_election
from ingest.reader import ParquetMarketDataReader
from orchestrator.composition import (
    build_orchestrator,
    build_session_factory,
    build_skill_registry,
    build_tool_registry,
)
from scheduler.delivery.channels import build_default_channels
from scheduler.subscriber import UpdateSource
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
    # Scheduled agent runs act for a user, so they get the same `private` account tools the chat
    # route has (`get_my_*`); the tool gate still refuses them without a signed-in user.
    tool_registry = build_tool_registry(
        router,
        session_factory=session_factory,
        account_reader=build_account_reader(session_factory, resolved),
    )
    skill_registry = build_skill_registry()
    orchestrator = build_orchestrator(
        skill_registry=skill_registry,
        tool_registry=tool_registry,
        settings=resolved,
    )
    leader: LeaderElection = build_leader_election(
        role="scheduler",
        database_url=resolved.database_url,
        scope_root=resolved.parquet_root,
    )
    return build_scheduler_worker(
        session_factory=session_factory,
        orchestrator=orchestrator,
        brief_tool=tool_registry.get_tool("get_morning_brief"),
        public_base_url=resolved.webhook_base_url,
        link_secret=reveal(resolved.jwt_secret_key),
        settings=resolved,
        router=router,
        entitlement_resolver=EntitlementResolver(tls_policy=resolved.tls_policy),
        channels=build_default_channels(resolved, live=True),
        leader=leader,
        # Read-only Parquet view of the ingested store (never the writer's .duckdb file).
        reader=ParquetMarketDataReader(resolved.parquet_root),
    )


def build_alert_update_source(settings: Settings | None = None) -> UpdateSource | None:
    """The `data.updated` subscription the alert engine reacts to, or None when there is nothing
    to subscribe to. Only the Redis backend crosses processes — the ingest leader is a different
    process from the scheduler, so an in-memory bus could never deliver its signal here. With no
    source the engine still works: the per-pass reconciliation sweep is the correctness floor and
    the signal only lowers latency."""
    resolved = settings or get_settings()
    if resolved.event_bus_backend != "redis":
        return None
    return build_data_update_broker(resolved).reader_factory.updates


__all__ = ["build_alert_update_source", "build_scheduler_from_settings"]
