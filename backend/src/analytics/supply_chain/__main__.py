"""Operator CLI: `python -m analytics.supply_chain COMMAND`, one of
load|stats|positions|eval|discover|verify|promote.

`load` rebuilds the index tables from the private relation, concept-theme and entity files;
`stats` prints row counts (numbers only, never file content). `positions` recomputes and writes
the chain positions and prints counts per position and method only.
`eval [--golden PATH] [--json]` computes the positions in memory from the current database and
scores them against the golden file (exit 1 when the gate fails or the golden file is unusable).
`discover [--limit N] [--source S]` lists the next claims to research; `verify [--limit N]
[--source S] [--dry-run]` researches them (see `verification/cli.py`). Both print counts and
claim ids only.
`discover --themes [--min-companies N] [--limit N] [--dry-run]` instead mines new concept-theme
candidates from the entity mentions (no search, no model, no key needed) and writes the private
candidates file unless `--dry-run`; it prints rank, label, type, company count, trend and up to five
stock symbols, never a company name (see `theme_discovery/`). When a database is reachable
it also counts industry-chain overlap; otherwise it says so in one line and uses concept
themes only. `promote FILE... [--dry-run] [--min-confidence medium|high]` turns reviewed
proposal files into live concept-theme files (see `themes_io/promote.py`); an explicit
operator step, never scheduled.
`research-themes [--limit N] [--source candidates|existing|all] [--dry-run]` (Step B) and
`verify-themes [--limit N] [--covered-threshold X] [--dry-run]` (Step C) turn web research into
proposal files for `promote` to read after a person has reviewed them (see `theme_research/`);
counts and ids only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from analytics.supply_chain.entities_io.loader import EntityFileError, load_entities
from analytics.supply_chain.evaluation.golden import GoldenFileError, load_golden
from analytics.supply_chain.evaluation.metrics import check_gate, evaluate, format_report
from analytics.supply_chain.positions.overrides import OverrideFileError, load_overrides
from analytics.supply_chain.positions.service import compute_positions, summarize
from analytics.supply_chain.refdata_io.loader import RelationFileError, load_reference_relations
from analytics.supply_chain.store.concept_protocol import ConceptCollisionError
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from analytics.supply_chain.theme_discovery.industry import (
    load_industry_groups,
    load_industry_roster,
)
from analytics.supply_chain.theme_discovery.job import run_themes_command
from analytics.supply_chain.theme_research.job import run_theme_command
from analytics.supply_chain.theme_research.verify import COVERED_THRESHOLD
from analytics.supply_chain.themes_io.loader import ConceptFileError, load_concept_themes
from analytics.supply_chain.themes_io.promote import (
    IndustryRoster,
    PromoteWriteError,
    ProposalFileError,
    run_promote,
)
from analytics.supply_chain.verification.cli import run_research_command
from analytics.themes.store import SqlAlchemyThemeStore
from config.private_data import reference_dir
from config.settings import get_settings
from db import (
    models,  # noqa: F401  # pyright: ignore[reportUnusedImport] - registers ORM models
    rls,
)
from db.base import ensure_schema, make_engine, make_session_factory

NO_MEMBERSHIPS = "no industry memberships to classify\n"


async def _positions(session: AsyncSession) -> tuple[int, str]:
    store = SqlAlchemySupplyChainStore(session)
    positions, memberships = await compute_positions(
        SqlAlchemyThemeStore(session), store, load_overrides()
    )
    if memberships == 0:
        return 1, NO_MEMBERSHIPS
    await store.replace_positions(positions, computed_at=datetime.now(UTC))
    await session.commit()
    summary = {"memberships": memberships, "written": len(positions), **summarize(positions)}
    return 0, json.dumps(summary) + "\n"


async def _eval(
    session: AsyncSession, golden_path: Path | None, *, as_json: bool
) -> tuple[int, str]:
    positions, memberships = await compute_positions(
        SqlAlchemyThemeStore(session), SqlAlchemySupplyChainStore(session), load_overrides()
    )
    if memberships == 0:
        return 1, NO_MEMBERSHIPS
    report = evaluate(load_golden(golden_path), positions)
    failures = check_gate(report)
    code = 1 if failures else 0
    if as_json:
        body = report.model_dump(mode="json") | {"gate_failures": failures}
        return code, json.dumps(body, ensure_ascii=False, indent=2) + "\n"
    verdict = "FAIL - " + "; ".join(failures) if failures else "pass"
    return code, f"{format_report(report)}\n\ngate: {verdict}\n"


async def _load_all(session: AsyncSession) -> tuple[int, str]:
    """Relations, then concept themes, then entities. Each load validates before it writes, so a
    bad file stops the run with nothing from that file applied (earlier loads stay committed)."""
    store = SqlAlchemySupplyChainStore(session)
    concepts = SqlAlchemyConceptStore(session)
    report: dict[str, object] = {}
    try:
        relations = await load_reference_relations(store, today=datetime.now(UTC).date())
        await session.commit()
        report["relations"] = relations.__dict__
        themes = await load_concept_themes(concepts)
        await session.commit()
        report["concept_themes"] = themes.__dict__
        entities = await load_entities(concepts)
        await session.commit()
        report["entities"] = entities.__dict__
    except (RelationFileError, ConceptFileError, ConceptCollisionError, EntityFileError) as exc:
        await session.rollback()
        return 1, f"invalid file: {exc}\n"
    return 0, json.dumps(report) + "\n"


async def _run(command: str, golden: Path | None, *, as_json: bool) -> int:
    settings = get_settings()
    rls.install(enabled=settings.row_level_security)
    rls.enter_system_mode()  # platform-wide reference data, not one user
    engine = make_engine(settings.database_url)
    try:
        await ensure_schema(engine)
        factory = make_session_factory(engine)
        async with factory() as session:
            store = SqlAlchemySupplyChainStore(session)
            if command == "load":
                code, text = await _load_all(session)
                (sys.stdout if code == 0 else sys.stderr).write(text)
                return code
            elif command in ("positions", "eval"):
                try:
                    if command == "positions":
                        code, text = await _positions(session)
                    else:
                        code, text = await _eval(session, golden, as_json=as_json)
                except (OverrideFileError, GoldenFileError) as exc:
                    sys.stderr.write(f"{exc}\n")
                    return 1
                sys.stdout.write(text)
                return code
            else:
                counts = (await store.counts()).model_dump(mode="json")
                concept = (await SqlAlchemyConceptStore(session).counts()).model_dump(mode="json")
                sys.stdout.write(json.dumps(counts | {"concepts": concept}, indent=2) + "\n")
    finally:
        await engine.dispose()
    return 0


DB_TIMEOUT_SECONDS = 10.0


async def _with_session[T](fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    settings = get_settings()
    rls.install(enabled=settings.row_level_security)
    rls.enter_system_mode()
    engine = make_engine(settings.database_url)
    try:
        async with make_session_factory(engine)() as session:
            return await fn(session)
    finally:
        await engine.dispose()


def _try_database[T](fn: Callable[[AsyncSession], Awaitable[T]]) -> T | None:
    """Run `fn` against the app database, or None when it is not configured or unreachable.
    Any failure means "unavailable": the callers degrade, they never fail on the database."""
    try:
        return asyncio.run(asyncio.wait_for(_with_session(fn), DB_TIMEOUT_SECONDS))
    except Exception:
        return None


def _fetch_industry_groups() -> dict[str, frozenset[str]] | None:
    return _try_database(lambda s: load_industry_groups(SqlAlchemyThemeStore(s)))


def _fetch_industry_roster() -> IndustryRoster | None:
    return _try_database(lambda s: load_industry_roster(SqlAlchemyThemeStore(s)))


def _promote(files: list[Path], *, dry_run: bool, min_confidence: str) -> int:
    if not files:
        sys.stderr.write("promote needs at least one proposal file\n")
        return 1
    try:
        report = run_promote(
            reference_dir(),
            files,
            dry_run=dry_run,
            min_confidence=min_confidence,
            industry=_fetch_industry_roster(),
        )
    except (ProposalFileError, PromoteWriteError) as exc:
        sys.stderr.write(f"invalid file: {exc}\n")
        return 1
    sys.stdout.write(report.format(dry_run=dry_run))
    if not dry_run:
        sys.stdout.write(
            "next: run `python -m analytics.supply_chain load` (or wait for the 06:25 job) "
            "to index it\n"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analytics.supply_chain")
    parser.add_argument(
        "command",
        choices=[
            "load",
            "stats",
            "positions",
            "eval",
            "discover",
            "verify",
            "promote",
            "research-themes",
            "verify-themes",
        ],
    )
    parser.add_argument("files", nargs="*", type=Path, help="promote: proposal YAML files")
    parser.add_argument(
        "--min-confidence",
        choices=["medium", "high"],
        default="medium",
        help="promote: lowest evidence confidence accepted (default medium: both)",
    )
    parser.add_argument("--golden", type=Path, default=None, help="eval: golden file path")
    parser.add_argument("--json", action="store_true", help="eval: print the report as JSON")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="discover/verify: max claims; --themes: max candidates",
    )
    parser.add_argument(
        "--themes",
        action="store_true",
        help="discover: only mine new concept-theme candidates from entity mentions "
        "(no search, no LLM, no key; writes the private candidates file unless --dry-run)",
    )
    parser.add_argument(
        "--min-companies",
        type=int,
        default=None,
        help="discover --themes: min distinct listed companies per entity (default 5)",
    )
    parser.add_argument(
        "--max-share",
        type=float,
        default=None,
        help="discover --themes: drop entities mentioned by more than this share of all listed "
        "companies in the catalog (default 0.04)",
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=None,
        help="discover --themes: drop entities whose companies are at least this share inside "
        "one existing concept theme (default 0.6)",
    )
    parser.add_argument(
        "--covered-threshold",
        type=float,
        default=COVERED_THRESHOLD,
        help="verify-themes: a new-theme candidate whose verified members are at least this "
        "share inside one existing concept theme is not proposed as a theme (default 0.8)",
    )
    parser.add_argument(
        "--source",
        choices=["candidates", "gaps", "stale", "existing", "all"],
        default="all",
        help="discover/verify: which claims to take (candidates|gaps|stale|all); "
        "research-themes: candidates|existing|all",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="verify/research-themes/verify-themes: search and judge, write nothing; "
        "discover --themes: print, write nothing",
    )
    args = parser.parse_args(argv)
    if args.files and args.command != "promote":
        parser.error("proposal files apply to the promote command only")
    if args.command == "promote":
        return _promote(args.files, dry_run=args.dry_run, min_confidence=args.min_confidence)
    if args.command in ("research-themes", "verify-themes"):
        if args.command == "research-themes" and args.source in ("gaps", "stale"):
            parser.error("research-themes --source is one of candidates|existing|all")
        if not 0 < args.covered_threshold <= 1:
            parser.error("--covered-threshold must be above 0 and at most 1")
        return asyncio.run(
            run_theme_command(
                args.command,
                limit=args.limit,
                dry_run=args.dry_run,
                source=args.source,
                covered_threshold=args.covered_threshold,
            )
        )
    if args.source == "existing":
        parser.error("--source existing applies to research-themes only")
    if args.themes:
        if args.command != "discover":
            parser.error("--themes applies to the discover command only")
        groups = _fetch_industry_groups()
        if groups is None:
            sys.stdout.write("industry overlap skipped: database unavailable\n")
        return run_themes_command(
            industry_groups=groups,
            min_companies=args.min_companies,
            limit=args.limit,
            dry_run=args.dry_run,
            max_share=args.max_share,
            overlap_threshold=args.overlap,
        )
    if args.command in ("discover", "verify"):
        return asyncio.run(
            run_research_command(
                args.command, limit=args.limit, dry_run=args.dry_run, source=args.source
            )
        )
    return asyncio.run(_run(args.command, args.golden, as_json=args.json))


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
