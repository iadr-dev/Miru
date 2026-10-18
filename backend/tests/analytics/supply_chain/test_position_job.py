"""`job_sc_positions_refresh`: gates, a full run over invented themes, and the safety skips."""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.jobs import SC_POSITIONS_DATASET, job_sc_positions_refresh
from analytics.supply_chain.positions import overrides as overrides_module
from analytics.supply_chain.positions.models import Method, Position
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from analytics.themes.models import (
    ThemeKind,
    ThemeMember,
    ThemeSource,
    ThemeTierName,
    ThemeTierSource,
)
from analytics.themes.store import SqlAlchemyThemeStore
from config.settings import Settings
from ingest.context import IngestContext
from ingest.daemon import SCHEDULED_JOBS
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter
from tests.analytics.supply_chain.builders import ALPHA, BETA, relation

RUN_DATE = date(2026, 10, 17)
CHAIN = "chain-a"


def _ctx(tmp_path: Path, leader: object, factory: async_sessionmaker | None) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader,  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        session_factory=factory,
    )


@pytest.fixture(autouse=True)
def _no_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(overrides_module, "reference_path", (tmp_path / "ref").joinpath)
    return tmp_path / "ref"


async def _seed_chain(factory: async_sessionmaker) -> None:
    async with factory() as session:
        themes = SqlAlchemyThemeStore(session)
        theme = await themes.upsert_theme(
            key=CHAIN, name_zh="Chain A", kind=ThemeKind.INDUSTRY, source=ThemeSource.INDUSTRY_CHAIN
        )
        await themes.replace_theme_members(
            theme_key=CHAIN,
            source=ThemeSource.INDUSTRY_CHAIN,
            members=[
                ThemeMember(
                    symbol=s, market="TW", sub_industry=sub, source=ThemeSource.INDUSTRY_CHAIN
                )
                for s, sub in (("9001", "raw"), ("9002", "parts"), ("9003", "none"))
            ],
        )
        await themes.upsert_theme_tier(
            theme_id=theme.id,
            sub_industry="raw",
            tier=ThemeTierName.UPSTREAM,
            source=ThemeTierSource.CURATED,
        )
        await themes.upsert_theme_tier(
            theme_id=theme.id,
            sub_industry="parts",
            tier=ThemeTierName.MIDSTREAM,
            source=ThemeTierSource.CURATED,
        )
        await session.commit()


async def test_not_leader_is_skipped(session_factory: async_sessionmaker, tmp_path: Path) -> None:
    await _seed_chain(session_factory)
    run = await job_sc_positions_refresh(RUN_DATE, _ctx(tmp_path, NeverLeader(), session_factory))
    assert run.status is RunStatus.SKIPPED
    async with session_factory() as session:
        assert await SqlAlchemySupplyChainStore(session).get_positions_for_symbol("9001") == []


async def test_no_session_factory_is_skipped(tmp_path: Path) -> None:
    run = await job_sc_positions_refresh(RUN_DATE, _ctx(tmp_path, AlwaysLeader(), None))
    assert run.status is RunStatus.SKIPPED


async def test_no_memberships_is_skipped_and_keeps_existing_rows(
    session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    ctx = _ctx(tmp_path, AlwaysLeader(), session_factory)
    assert (await job_sc_positions_refresh(RUN_DATE, ctx)).status is RunStatus.SKIPPED
    await _seed_chain(session_factory)
    assert (await job_sc_positions_refresh(RUN_DATE, ctx)).status is RunStatus.SUCCESS
    async with session_factory() as session:  # themes disappear: rows stay, the run is skipped
        await SqlAlchemyThemeStore(session).replace_theme_members(
            theme_key=CHAIN, source=ThemeSource.INDUSTRY_CHAIN, members=[]
        )
        await session.commit()
    assert (await job_sc_positions_refresh(RUN_DATE, ctx)).status is RunStatus.SKIPPED
    async with session_factory() as session:
        assert len(await SqlAlchemySupplyChainStore(session).get_positions_for_symbol("9001")) == 1


async def test_leader_computes_and_rewrites_positions(
    session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    await _seed_chain(session_factory)
    ctx = _ctx(tmp_path, AlwaysLeader(), session_factory)
    run = await job_sc_positions_refresh(RUN_DATE, ctx)
    assert (run.dataset, run.status, run.rows_written) == (
        SC_POSITIONS_DATASET,
        RunStatus.SUCCESS,
        2,
    )
    again = await job_sc_positions_refresh(RUN_DATE, ctx)  # idempotent
    assert again.rows_written == 2
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        first = (await store.get_positions_for_symbol("9001"))[0]
        assert (first.position, first.method, first.is_primary) == (
            Position.UPSTREAM,
            Method.PRIOR,
            True,
        )
        assert first.computed_at is not None
        assert len(await store.get_positions_for_symbol("9003")) == 0  # abstained: no row


async def test_job_uses_verified_relations_and_overrides(
    session_factory: async_sessionmaker, tmp_path: Path, _no_overrides_file: Path
) -> None:
    await _seed_chain(session_factory)
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await store.upsert_relation(relation(ALPHA, BETA))
        await session.commit()
    sc_dir = _no_overrides_file / "supply_chain"
    sc_dir.mkdir(parents=True)
    (sc_dir / "position_overrides.yaml").write_text(
        f'- {{symbol: "9003", chain_key: {CHAIN}, position: downstream, reason: "invented"}}\n',
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path, AlwaysLeader(), session_factory)
    run = await job_sc_positions_refresh(RUN_DATE, ctx)
    assert run.status is RunStatus.SUCCESS and run.rows_written == 3
    async with session_factory() as session:
        row = (await SqlAlchemySupplyChainStore(session).get_positions_for_symbol("9003"))[0]
    assert (row.position, row.method) == (Position.DOWNSTREAM, Method.OVERRIDE)


async def test_invalid_overrides_file_fails_without_raising(
    session_factory: async_sessionmaker, tmp_path: Path, _no_overrides_file: Path
) -> None:
    await _seed_chain(session_factory)
    sc_dir = _no_overrides_file / "supply_chain"
    sc_dir.mkdir(parents=True)
    (sc_dir / "position_overrides.yaml").write_text("- {oops: 1}\n", encoding="utf-8")
    run = await job_sc_positions_refresh(RUN_DATE, _ctx(tmp_path, AlwaysLeader(), session_factory))
    assert run.status is RunStatus.FAILED


def test_job_runs_daily_after_the_relation_load() -> None:
    names = [job.name for job in SCHEDULED_JOBS]
    jobs = {job.name: job for job in SCHEDULED_JOBS}
    assert jobs["sc_positions_refresh"].run is job_sc_positions_refresh
    assert names.index("sc_positions_refresh") > names.index("sc_refdata_load")
    assert jobs["sc_positions_refresh"].fire_time > jobs["sc_refdata_load"].fire_time
