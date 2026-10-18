"""The operator-triggered concept-theme research run — `python -m
analytics.themes`.

Deliberately NOT a daemon and NOT wired into any scheduled ingest pass. This layer is the only one
that spends on language-model calls and does live web research, so it carries an unattended-drift
and unattended-spend risk the deterministic industry/tier seeds do not. It is run by an operator,
by hand, when they want to refresh the concept themes — a one-shot run-and-exit script.

One invocation, under one correlated `run_id`: research every concept theme in
`concept_themes.yaml` (two researchers + a verifier, with the fabrication safeguard filtering every
proposed symbol against the real stored universe).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.themes.research import (
    ConceptThemeSpec,
    load_concept_themes,
    run_concept_research,
)
from analytics.themes.store import SqlAlchemyThemeStore
from config.settings import get_settings
from dataaccess.web_search import WebSearchGateway
from db import (
    models,  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
    rls,
)
from db.base import make_engine, make_session_factory
from llm.base import LLMClient
from llm.registry import ModelRole, resolve_role

logger = structlog.get_logger(__name__)


async def run_operator_pipeline(  # noqa: PLR0913 — injected collaborators, one per seam
    *,
    session_factory: async_sessionmaker[AsyncSession],
    web_search: WebSearchGateway,
    researcher_a: LLMClient,
    researcher_b: LLMClient,
    verifier: LLMClient,
    specs: list[ConceptThemeSpec],
    run_id: str,
) -> None:
    """The orchestration itself, with every collaborator injected. `main` wires the real ones; the
    end-to-end test wires stubs — so the exact operator call path runs in the default suite without
    a live provider or the network. Each stage commits its own transaction so a later-stage failure
    never rolls back completed earlier work."""
    logger.info("concept_research_start", run_id=run_id, themes=len(specs))

    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)

        summary = await run_concept_research(
            specs,
            store=store,
            web_search=web_search,
            researcher_a=researcher_a,
            researcher_b=researcher_b,
            verifier=verifier,
            run_id=run_id,
        )
        await session.commit()
        logger.info(
            "concept_research_complete",
            run_id=run_id,
            themes_processed=summary.themes_processed,
            total_members=summary.total_members,
            total_dropped_fabricated=summary.total_dropped_fabricated,
        )

    logger.info("operator_run_finished", run_id=run_id)


async def main() -> None:
    settings = get_settings()
    rls.install(enabled=settings.row_level_security)
    rls.enter_system_mode()  # platform-wide reference data, not one user
    run_id = str(uuid.uuid4())

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    # Role-based routing — two different roles give the two researchers model independence; the
    # verifier reconciles disputed picks; No provider or
    # model name appears here — the roles resolve through config/env.
    try:
        await run_operator_pipeline(
            session_factory=session_factory,
            web_search=WebSearchGateway(),
            researcher_a=resolve_role(ModelRole.SUBAGENT, settings),
            researcher_b=resolve_role(ModelRole.AGENT, settings),
            verifier=resolve_role(ModelRole.SYNTHESIS, settings),
            specs=load_concept_themes(),
            run_id=run_id,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
