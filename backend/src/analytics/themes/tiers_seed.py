"""Layer-2 tier seed — assigns every theme's sub-industries an upstream/midstream/downstream
position, running after the Layer-1 membership seed in the same monthly refresh.

Two producers, curated-wins: a small editorial map (`config/theme_tiers.yaml`) names the tiers a
human curated for the major themes, written with `source='curated'`; then the keyword heuristic
(`classify_tier`) backfills every OTHER `(theme, sub_industry)` pair actually present in the
memberships, written with `source='heuristic'`. Curated rows are never overwritten by the heuristic
pass — that is enforced twice over: the backfill only visits pairs the curated pass did not cover,
and `upsert_theme_tier` itself refuses a heuristic write against an already-curated row, so a re-run
in any order stays curated-wins.

Pure derived-compute over already-stored data plus one YAML file: it needs no FinMind call and no
new I/O source, only the relational session the daemon supplies via `IngestContext.session_factory`.
Idempotent — every write is an upsert keyed on the `(theme_id, sub_industry)` unique pair, so a
re-run refreshes rather than duplicates.
"""

from __future__ import annotations

from datetime import date as date_
from pathlib import Path
from typing import Final, cast

import structlog
import yaml

from analytics.themes.models import ThemeTierName, ThemeTierSource
from analytics.themes.store import SqlAlchemyThemeStore
from analytics.themes.tiers import classify_tier
from ingest.context import IngestContext
from ingest.journal import JobRun, RunStatus, journal_run

logger = structlog.get_logger(__name__)

THEME_TIER_SEED_DATASET = "theme_tier_seed"

_CURATED_CONFIG_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "config" / "theme_tiers.yaml"
)


def _load_curated(path: Path | None = None) -> dict[str, dict[str, list[str]]]:
    """Load the `theme_key -> {tier -> [sub_industry, ...]}` editorial map. Data, not code —
    matches the router's `source_priority.yaml` loading idiom."""
    config_path = path or _CURATED_CONFIG_PATH
    with config_path.open(encoding="utf-8") as fh:
        raw: object = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"theme_tiers.yaml at {config_path} did not parse to a mapping")
    return cast("dict[str, dict[str, list[str]]]", raw)


async def seed_theme_tiers(run_date: date_, ctx: IngestContext) -> JobRun:
    """Refresh the theme tiers from the curated map + heuristic backfill. Leader-gated (only the
    leader writes). Runs after `seed_industry_themes` in the same monthly pass — it reads the
    sub-industries that seed just wrote, so it must not run before memberships exist. Returns a
    journaled `JobRun` describing the outcome."""
    if not ctx.leader.is_leader():
        return journal_run(
            THEME_TIER_SEED_DATASET,
            run_date,
            rows_written=0,
            status=RunStatus.SKIPPED,
            detail="not the leader — write skipped",
        )
    if ctx.session_factory is None:
        raise RuntimeError("theme tier seed requires IngestContext.session_factory to be wired")

    curated = _load_curated()
    written = 0
    async with ctx.session_factory() as session:
        store = SqlAlchemyThemeStore(session)

        # Step 1 — curated editorial map. A theme_key naming a theme that was never seeded, or a
        # sub_industry with no membership, simply produces no read later; the write is harmless.
        covered: set[tuple[str, str]] = set()
        for theme_key, by_tier in curated.items():
            theme = await store.get_theme_by_key(key=theme_key)
            if theme is None:
                continue
            for tier_name, sub_industries in by_tier.items():
                tier = ThemeTierName(tier_name)
                for sub_industry in sub_industries:
                    await store.upsert_theme_tier(
                        theme_id=theme.id,
                        sub_industry=sub_industry,
                        tier=tier,
                        source=ThemeTierSource.CURATED,
                    )
                    covered.add((theme.id, sub_industry))
                    written += 1

        # Step 2 — heuristic backfill over every membership pair the curated map did not cover.
        for theme_id, sub_industry in await store.list_uncurated_sub_industries(covered=covered):
            await store.upsert_theme_tier(
                theme_id=theme_id,
                sub_industry=sub_industry,
                tier=ThemeTierName(classify_tier(sub_industry)),
                source=ThemeTierSource.HEURISTIC,
            )
            written += 1

        await session.commit()

    return journal_run(
        THEME_TIER_SEED_DATASET,
        run_date,
        rows_written=written,
        status=RunStatus.SUCCESS,
        detail=f"{written} theme tiers refreshed (curated + heuristic backfill)",
    )


__all__ = ["THEME_TIER_SEED_DATASET", "seed_theme_tiers"]
