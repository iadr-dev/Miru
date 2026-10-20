from __future__ import annotations

import json
from calendar.trading_days import TradingDayCalendar
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.refdata_io import loader
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from analytics.supply_chain.verification import job as job_module
from analytics.supply_chain.verification.directory import build_directory
from analytics.supply_chain.verification.job import (
    SC_RESEARCH_DATASET,
    job_sc_research,
    run_research_job,
)
from config.settings import Settings
from ingest.context import IngestContext
from ingest.daemon import SCHEDULED_JOBS, Cadence
from ingest.journal import RunStatus
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from llm.registry import ProviderNotConfiguredError
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter
from tests.analytics.supply_chain.verification.support import (
    PAGE_TEXT,
    TODAY,
    StubSearch,
    hit,
    judgement_json,
    llm,
)

URL = "https://news.example.com/p"


def make_ctx(
    tmp_path: Path, leader: object, factory: async_sessionmaker | None = None
) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path / "parquet"),  # type: ignore[arg-type]
        leader=leader,  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        session_factory=factory,
    )


def settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "supply_chain_research_enabled": True,
        "brave_search_api_key": "k",
        "finmind_api_key": "t",
    }
    return Settings(**(base | kw))  # type: ignore[arg-type]


@pytest.fixture
def reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ref"
    (root / "supply_chain").mkdir(parents=True)
    (root / "supply_chain" / "publisher_trust.yaml").write_text(
        "news.example.com: secondary\n", encoding="utf-8"
    )
    candidates = {
        "supplier": "9001",
        "customer": "9002",
        "counterparty_name": "貝塔裝置",
        "counterparty_resolved": True,
        "kind": "supplies",
    }
    (root / "supply_chain" / "candidates.jsonl").write_text(
        json.dumps(candidates) + "\n", encoding="utf-8"
    )
    directory = build_directory(
        roster={"9001": "阿爾法零件", "9002": "貝塔裝置"},
        market_values={"9001": 1.0, "9002": 2.0},
    )
    monkeypatch.setattr(job_module, "load_directory", lambda *_a, **_k: directory)
    monkeypatch.setattr(loader, "reference_path", root.joinpath)
    return root


def stubs() -> tuple[StubSearch, object]:
    return StubSearch([hit(URL)], {URL: PAGE_TEXT}), llm(judgement_json())


async def test_disabled_is_skipped_without_any_work(tmp_path: Path, reference: Path) -> None:
    search, client = stubs()
    run = await run_research_job(
        TODAY,
        make_ctx(tmp_path, AlwaysLeader()),
        settings=settings(supply_chain_research_enabled=False),
        search=search,
        judge_client=client,  # type: ignore[arg-type]
        root=reference,
    )
    assert run.status is RunStatus.SKIPPED and "ENABLED" in run.detail
    assert search.queries == []


async def test_not_the_leader_is_skipped(tmp_path: Path, reference: Path) -> None:
    search, client = stubs()
    run = await run_research_job(
        TODAY,
        make_ctx(tmp_path, NeverLeader()),
        settings=settings(),
        search=search,
        judge_client=client,  # type: ignore[arg-type]
        root=reference,
    )
    assert run.status is RunStatus.SKIPPED and "leader" in run.detail
    assert search.queries == [] and not (reference / "supply_chain" / "relations").exists()


async def test_missing_search_key_is_skipped(tmp_path: Path, reference: Path) -> None:
    run = await run_research_job(
        TODAY,
        make_ctx(tmp_path, AlwaysLeader()),
        settings=settings(brave_search_api_key=None),
        root=reference,
    )
    assert run.status is RunStatus.SKIPPED and "search key" in run.detail


async def test_missing_llm_role_is_skipped(
    tmp_path: Path, reference: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_provider(*_a: object, **_k: object) -> object:
        raise ProviderNotConfiguredError("nope")

    monkeypatch.setattr(job_module, "resolve_role_with_override", no_provider)
    run = await run_research_job(
        TODAY, make_ctx(tmp_path, AlwaysLeader()), settings=settings(), root=reference
    )
    assert run.status is RunStatus.SKIPPED and "LLM" in run.detail


async def test_the_scheduled_entry_point_is_gated_by_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        job_module, "get_settings", lambda: settings(supply_chain_research_enabled=False)
    )
    run = await job_sc_research(TODAY, make_ctx(tmp_path, AlwaysLeader()))
    assert run.dataset == SC_RESEARCH_DATASET and run.status is RunStatus.SKIPPED


async def test_a_run_writes_the_file_and_reindexes_the_database(
    tmp_path: Path, reference: Path, session_factory: async_sessionmaker
) -> None:
    search, client = stubs()
    run = await run_research_job(
        TODAY,
        make_ctx(tmp_path, AlwaysLeader(), session_factory),
        settings=settings(),
        search=search,
        judge_client=client,  # type: ignore[arg-type]
        root=reference,
        source="candidates",
    )
    assert run.status is RunStatus.SUCCESS and run.rows_written == 1
    assert "verified=1" in run.detail and "reindex=success" in run.detail
    assert PAGE_TEXT not in run.detail and "阿爾法" not in run.detail
    [row] = yaml.safe_load(
        (reference / "supply_chain" / "relations" / "TW-9001.yaml").read_text(encoding="utf-8")
    )
    assert row["origin"] == "research" and row["status"] == "verified"
    async with session_factory() as session:
        assert (await SqlAlchemySupplyChainStore(session).counts()).relations == 1


async def test_dry_run_writes_nothing_and_does_not_reindex(
    tmp_path: Path, reference: Path, session_factory: async_sessionmaker
) -> None:
    search, client = stubs()
    run = await run_research_job(
        TODAY,
        make_ctx(tmp_path, AlwaysLeader(), session_factory),
        settings=settings(),
        search=search,
        judge_client=client,  # type: ignore[arg-type]
        root=reference,
        dry_run=True,
    )
    assert run.status is RunStatus.SUCCESS and "would_write=1" in run.detail
    assert not (reference / "supply_chain" / "relations").exists()
    assert not (reference / "supply_chain" / "rejected.jsonl").exists()
    async with session_factory() as session:
        assert (await SqlAlchemySupplyChainStore(session).counts()).relations == 0


async def test_a_broken_relation_file_fails_the_run_without_writing(
    tmp_path: Path, reference: Path
) -> None:
    relations = reference / "supply_chain" / "relations"
    relations.mkdir()
    (relations / "bad.yaml").write_text("- supplier: 1\n", encoding="utf-8")
    search, client = stubs()
    run = await run_research_job(
        TODAY,
        make_ctx(tmp_path, AlwaysLeader()),
        settings=settings(),
        search=search,
        judge_client=client,  # type: ignore[arg-type]
        root=reference,
    )
    assert run.status is RunStatus.FAILED
    assert search.queries == []


async def test_caps_from_settings_end_the_run_and_are_journaled(
    tmp_path: Path, reference: Path
) -> None:
    search, client = stubs()
    run = await run_research_job(
        TODAY,
        make_ctx(tmp_path, AlwaysLeader()),
        settings=settings(supply_chain_research_max_searches=0),
        search=search,
        judge_client=client,  # type: ignore[arg-type]
        root=reference,
    )
    assert run.status is RunStatus.SUCCESS and "stopped_by=searches" in run.detail
    assert run.rows_written == 0


def test_the_job_is_registered_weekly_and_off_by_default() -> None:
    [entry] = [j for j in SCHEDULED_JOBS if j.name == "sc_research"]
    assert entry.cadence is Cadence.WEEKLY and entry.run is job_sc_research
    assert Settings(finmind_api_key="t").supply_chain_research_enabled is False


async def test_the_backfill_step_runs_only_when_the_setting_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingest import backfill_cli  # noqa: PLC0415

    calls: list[object] = []

    async def fake_job(run_date: object, ctx: object) -> object:
        calls.append(run_date)
        return object()

    monkeypatch.setattr(backfill_cli, "job_sc_research", fake_job)
    ctx = make_ctx(tmp_path, AlwaysLeader())
    off = settings(supply_chain_research_enabled=False)
    assert await backfill_cli._optional_sc_research(off, ctx) == ()
    assert len(await backfill_cli._optional_sc_research(settings(), ctx)) == 1
    assert len(calls) == 1


def test_the_research_model_setting_picks_the_judge_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []

    def fake(_role: object, override: str | None, _settings: object = None) -> object:
        seen.append(override)
        return object()

    monkeypatch.setattr(job_module, "resolve_role_with_override", fake)
    assert job_module.missing_prerequisite(settings(supply_chain_research_model="ollama/x")) is None
    assert job_module.missing_prerequisite(settings()) is None
    assert seen == ["ollama/x", None]


def test_the_second_opinion_model_setting_picks_its_own_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[object, str | None]] = []

    def fake(role: object, override: str | None, _settings: object = None) -> object:
        seen.append((role, override))
        return object()

    monkeypatch.setattr(job_module, "resolve_role_with_override", fake)
    assert job_module.missing_prerequisite(settings(), need_second=True) is None
    assert (
        job_module.missing_prerequisite(
            settings(supply_chain_research_second_model="  "), need_second=True
        )
        is None
    )
    second = [override for role, override in seen if getattr(role, "value", "") == "synthesis"]
    assert second == ["openai/gpt-5.6-sol", None]


def test_a_blank_research_model_counts_as_unset() -> None:
    assert job_module.research_model(settings(supply_chain_research_model="")) is None
    assert job_module.research_model(settings(supply_chain_research_model="  ")) is None
    assert job_module.research_model(settings(supply_chain_research_model="x/y")) == "x/y"
