from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.jobs import SC_REFDATA_DATASET, job_sc_refdata_load
from analytics.supply_chain.refdata_io import loader
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from config.settings import Settings
from ingest.context import IngestContext
from ingest.daemon import SCHEDULED_JOBS
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter

RUN_DATE = date(2026, 10, 17)
ROW = """
- supplier: {symbol: "9001", market: TW, name: Alpha Parts}
  customer: {symbol: "9002", market: TW, name: Beta Devices}
  origin: seeded
  grade: low
"""


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
    monkeypatch.setattr(loader, "reference_path", tmp_path.joinpath)
    return tmp_path


def _write_rows(reference: Path, text: str) -> None:
    rel_dir = reference / "supply_chain" / "relations"
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / "a.yaml").write_text(text, encoding="utf-8")


async def test_not_leader_is_skipped(
    reference: Path, tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    _write_rows(reference, ROW)
    run = await job_sc_refdata_load(RUN_DATE, _ctx(tmp_path, NeverLeader(), session_factory))
    assert run.status is RunStatus.SKIPPED
    async with session_factory() as session:
        assert (await SqlAlchemySupplyChainStore(session).counts()).relations == 0


async def test_no_session_factory_is_skipped(reference: Path, tmp_path: Path) -> None:
    run = await job_sc_refdata_load(RUN_DATE, _ctx(tmp_path, AlwaysLeader(), None))
    assert run.status is RunStatus.SKIPPED


async def test_leader_loads_rows(
    reference: Path, tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    _write_rows(reference, ROW)
    run = await job_sc_refdata_load(RUN_DATE, _ctx(tmp_path, AlwaysLeader(), session_factory))
    assert (run.dataset, run.status, run.rows_written) == (SC_REFDATA_DATASET, RunStatus.SUCCESS, 1)
    async with session_factory() as session:
        assert (await SqlAlchemySupplyChainStore(session).counts()).relations == 1


async def test_missing_files_are_skipped_and_bad_rows_fail_without_raising(
    reference: Path, tmp_path: Path, session_factory: async_sessionmaker
) -> None:
    ctx = _ctx(tmp_path, AlwaysLeader(), session_factory)
    assert (await job_sc_refdata_load(RUN_DATE, ctx)).status is RunStatus.SKIPPED
    _write_rows(reference, "- {oops: 1}\n")
    assert (await job_sc_refdata_load(RUN_DATE, ctx)).status is RunStatus.FAILED


def test_job_is_scheduled_daily() -> None:
    jobs = {job.name: job for job in SCHEDULED_JOBS}
    assert jobs["sc_refdata_load"].run is job_sc_refdata_load
