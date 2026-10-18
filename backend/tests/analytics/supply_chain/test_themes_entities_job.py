"""The `sc_themes_entities_load` ingest job and the CLI's `load` / `stats`. Synthetic data."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain import __main__ as cli
from analytics.supply_chain.entities_io import loader as entities_loader
from analytics.supply_chain.jobs import SC_THEMES_ENTITIES_DATASET, job_sc_themes_entities_load
from analytics.supply_chain.refdata_io import loader as relations_loader
from analytics.supply_chain.store.concept_store import SqlAlchemyConceptStore
from analytics.supply_chain.themes_io import loader as themes_loader
from analytics.themes.models import ThemeKind
from analytics.themes.store import SqlAlchemyThemeStore
from config.settings import Settings
from ingest.context import IngestContext
from ingest.daemon import SCHEDULED_JOBS
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter

RUN_DATE = date(2026, 10, 17)


def _ctx(tmp_path: Path, leader: object, factory: async_sessionmaker | None) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader,  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        session_factory=factory,
    )


@pytest.fixture
def reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty reference dir both loaders read instead of the shared synthetic one."""
    root = tmp_path / "ref"
    root.mkdir()
    monkeypatch.setattr(themes_loader, "reference_path", root.joinpath)
    monkeypatch.setattr(entities_loader, "reference_path", root.joinpath)
    monkeypatch.setattr(relations_loader, "reference_path", root.joinpath)
    return root


def _write_entities(reference: Path) -> None:
    directory = reference / "entities"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "catalog.yaml").write_text("- {id: Thing, type: product}\n", encoding="utf-8")
    (directory / "mentions.jsonl").write_text(
        '{"symbol": "9001", "entity": "Thing", "section": "chain", "role": null, "count": 2}\n',
        encoding="utf-8",
    )


def _write_theme(reference: Path, text: str) -> None:
    directory = reference / "themes" / "concepts"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "t.yaml").write_text(text, encoding="utf-8")


GOOD_THEME = "key: t\nname_zh: T\nmembers: [{symbol: '9001', position: upstream}]\n"


async def test_not_leader_and_no_factory_are_skipped(
    tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    run = await job_sc_themes_entities_load(
        RUN_DATE, _ctx(tmp_path, NeverLeader(), session_factory)
    )
    assert run.status is RunStatus.SKIPPED
    run = await job_sc_themes_entities_load(RUN_DATE, _ctx(tmp_path, AlwaysLeader(), None))
    assert run.status is RunStatus.SKIPPED
    async with session_factory() as session:
        assert (await SqlAlchemyConceptStore(session).counts()).concept_themes == 0


async def test_leader_loads_the_shared_synthetic_fixture(
    tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    run = await job_sc_themes_entities_load(
        RUN_DATE, _ctx(tmp_path, AlwaysLeader(), session_factory)
    )
    assert (run.dataset, run.status) == (SC_THEMES_ENTITIES_DATASET, RunStatus.SUCCESS)
    async with session_factory() as session:
        counts = await SqlAlchemyConceptStore(session).counts()
        concept = await SqlAlchemyThemeStore(session).list_themes(kind=ThemeKind.CONCEPT)
    assert (counts.concept_themes, counts.concept_members) == (2, 5)
    assert (counts.entities, counts.entity_mentions) == (5, 6)
    assert [t.key for t in concept] == ["test-alpha", "test-beta"]
    assert run.rows_written == 5 + 5 + 6


async def test_rerun_is_idempotent(tmp_path: Path, session_factory: async_sessionmaker) -> None:
    ctx = _ctx(tmp_path, AlwaysLeader(), session_factory)
    first = await job_sc_themes_entities_load(RUN_DATE, ctx)
    second = await job_sc_themes_entities_load(RUN_DATE, ctx)
    assert first.rows_written == second.rows_written
    async with session_factory() as session:
        assert (await SqlAlchemyConceptStore(session).counts()).entity_mentions == 6


async def test_no_files_is_skipped(
    reference: Path, tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    run = await job_sc_themes_entities_load(
        RUN_DATE, _ctx(tmp_path, AlwaysLeader(), session_factory)
    )
    assert run.status is RunStatus.SKIPPED


async def test_a_bad_theme_file_fails_the_run_but_not_the_entity_load(
    reference: Path, tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    _write_theme(reference, "key: t\nname_zh: T\nmembers: [{symbol: '9001', position: nope}]\n")
    _write_entities(reference)
    run = await job_sc_themes_entities_load(
        RUN_DATE, _ctx(tmp_path, AlwaysLeader(), session_factory)
    )
    assert run.status is RunStatus.FAILED
    async with session_factory() as session:
        counts = await SqlAlchemyConceptStore(session).counts()
    assert (counts.concept_themes, counts.entities, counts.entity_mentions) == (0, 1, 1)


async def test_a_bad_entity_file_fails_the_run_but_not_the_theme_load(
    reference: Path, tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    _write_theme(reference, GOOD_THEME)
    _write_entities(reference)
    (reference / "entities" / "mentions.jsonl").write_text("{oops\n", encoding="utf-8")
    run = await job_sc_themes_entities_load(
        RUN_DATE, _ctx(tmp_path, AlwaysLeader(), session_factory)
    )
    assert run.status is RunStatus.FAILED
    async with session_factory() as session:
        counts = await SqlAlchemyConceptStore(session).counts()
    assert (counts.concept_themes, counts.entities) == (1, 0)


def test_job_runs_after_the_relation_load_and_before_the_positions_refresh() -> None:
    names = [job.name for job in SCHEDULED_JOBS]
    jobs = {job.name: job for job in SCHEDULED_JOBS}
    assert jobs["sc_themes_entities_load"].run is job_sc_themes_entities_load
    assert (
        jobs["sc_refdata_load"].fire_time
        < jobs["sc_themes_entities_load"].fire_time
        < jobs["sc_positions_refresh"].fire_time
    )
    assert names.count("sc_themes_entities_load") == 1


async def test_cli_load_all_and_stats(
    reference: Path,
    session_factory: async_sessionmaker,
) -> None:
    _write_theme(reference, GOOD_THEME)
    _write_entities(reference)
    async with session_factory() as session:
        code, text = await cli._load_all(session)
        assert code == 0, text
        assert '"concept_themes"' in text
        assert '"entities"' in text
        assert (await SqlAlchemyConceptStore(session).counts()).entities == 1
    (reference / "themes" / "concepts" / "t.yaml").write_text("key: [", encoding="utf-8")
    async with session_factory() as session:
        code, text = await cli._load_all(session)
        assert code == 1
        assert text.startswith("invalid file:")
