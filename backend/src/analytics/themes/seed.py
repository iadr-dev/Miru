"""Layer-1 theme seed — deterministically materializes industry themes and their memberships
from FinMind's whole-market industry-chain classification roster.

For each classification row: the stock's `industry` becomes an `industry`-kind theme (keyed by the
industry name itself — that name is unique within the roster, so it doubles as the natural slug),
and the stock is collected as a member of that theme. Memberships are written one snapshot per
theme via `replace_theme_members`, so a monthly re-run is a fresh replacement, never a growing
union.

Reads through `IngestContext.finmind` (the ingest layer's sole whole-market caller) and writes
through the relational `ThemeStore`, so it needs a DB session — the daemon supplies one via
`IngestContext.session_factory`. A genuinely-empty roster (e.g. a Free-tier token with no Sponsor
access to this dataset) is handled as a graceful skip, not an error, and never wipes an existing
snapshot.
"""

from __future__ import annotations

from datetime import date as date_
from typing import cast

import structlog

from analytics.themes.models import ThemeKind, ThemeMember, ThemeSource
from analytics.themes.store import SqlAlchemyThemeStore
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run
from schemas.capabilities import IndustryChainClassificationRow
from schemas.enums import Capability
from schemas.source import HealthStatus

logger = structlog.get_logger(__name__)

THEME_SEED_DATASET = "theme_industry_chain_seed"


def _parse_rows(payload: dict[str, object]) -> list[IndustryChainClassificationRow]:
    row_key = next(iter(payload), None)
    raw: object = payload.get(row_key) if row_key else None
    if not isinstance(raw, list):
        return []
    return [
        IndustryChainClassificationRow.model_validate(item)
        for item in cast("list[dict[str, object]]", raw)
    ]


async def seed_industry_themes(run_date: date_, ctx: IngestContext) -> JobRun:
    """Refresh the industry-chain-sourced themes and memberships. Leader-gated (only the leader
    writes); the FinMind read is skipped entirely on a non-leader pass to avoid pointless quota
    spend. Returns a journaled `JobRun` describing the outcome."""
    if not ctx.leader.is_leader():
        return journal_run(
            THEME_SEED_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )
    if ctx.session_factory is None:
        # A daemon that never wired a session factory cannot persist themes; surface it rather
        # than silently no-op, since this is a wiring defect, not a data condition.
        raise RuntimeError("theme seed requires IngestContext.session_factory to be wired")

    result = await ctx.finmind.fetch_whole_market(
        Capability.INDUSTRY_CHAIN_CLASSIFICATION, tls_policy=ctx.tls_policy
    )
    if result.health.status in (HealthStatus.UNREACHABLE, HealthStatus.UNAVAILABLE_TIER):
        # A Free-tier token with no Sponsor access to this dataset, or a transport failure, is a
        # graceful skip — never an error, and the existing snapshot is left untouched.
        logger.info(
            "theme_seed_unavailable",
            run_date=run_date.isoformat(),
            status=result.health.status.value,
            detail=result.health.detail,
        )
        return journal_run(
            THEME_SEED_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail=f"industry-chain classification unavailable: {result.health.detail}",
        )

    rows = _parse_rows(result.payload)
    if not rows:
        # A legitimately-empty roster leaves the current snapshot in place rather than wiping it
        # to zero on a transient empty response.
        return journal_run(
            THEME_SEED_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="no industry-chain classification rows returned",
        )

    # Group members by industry so each theme is written as one snapshot, not one write per row.
    members_by_industry: dict[str, list[ThemeMember]] = {}
    for row in rows:
        industry = row.industry
        if not industry:
            continue
        members_by_industry.setdefault(industry, []).append(
            ThemeMember(
                symbol=row.symbol,
                market=row.market.value,
                sub_industry=row.sub_industry,
                confidence=1.0,
                source=ThemeSource.INDUSTRY_CHAIN,
            )
        )

    written = 0
    async with ctx.session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        for industry, members in members_by_industry.items():
            await store.upsert_theme(
                key=industry,
                name_zh=industry,
                kind=ThemeKind.INDUSTRY,
                source=ThemeSource.INDUSTRY_CHAIN,
            )
            written += await store.replace_theme_members(
                theme_key=industry,
                source=ThemeSource.INDUSTRY_CHAIN,
                members=members,
            )
        await session.commit()

    return journal_run(
        THEME_SEED_DATASET,
        run_date,
        rows_written=written,
        status=RunStatus.SUCCESS,
        detail=f"{len(members_by_industry)} industry themes refreshed",
    )


__all__ = ["THEME_SEED_DATASET", "seed_industry_themes"]
