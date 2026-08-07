"""End-to-end theme-seed tests.

The failure-mode guard: these do not test `seed_industry_themes` in isolation only — they drive
the REAL `IngestDaemon.run_due_jobs` pass with a stubbed `fetch_whole_market` and then query the
REAL `ThemeStore.get_theme_members`, proving the seed is actually wired to the daemon's common
call path and lands queryable membership rows. A second test proves a later monthly fire replaces
rather than unions/duplicates the memberships. No live network / no live DB (in-memory SQLite +
monkeypatched HTTP client, matching the ingest job tests' recorded-fixture convention).
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from collections.abc import AsyncIterator
from datetime import date as date_
from datetime import datetime
from datetime import time as time_
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import sources.finmind as finmind_module
from analytics.themes.seed import seed_industry_themes
from analytics.themes.store import SqlAlchemyThemeStore
from config.settings import Settings
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from ingest.context import IngestContext
from ingest.daemon import Cadence, IngestDaemon, ScheduledIngestJob
from ingest.leader import AlwaysLeader, NeverLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter
from tests.sources.conftest import make_json_client_factory

_TAIPEI = ZoneInfo("Asia/Taipei")


def _classification_body(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"msg": "success", "status": 200, "data": rows}


_FIRST_SNAPSHOT = _classification_body(
    [
        {
            "stock_id": "2330",
            "industry": "半導體",
            "sub_industry": "晶圓代工",
            "date": "2026-01-01",
        },
        {"stock_id": "2454", "industry": "半導體", "sub_industry": "IC設計", "date": "2026-01-01"},
        {
            "stock_id": "2317",
            "industry": "電子零組件",
            "sub_industry": "連接器",
            "date": "2026-01-01",
        },
    ]
)

# A later month: 2454 left 半導體, 3034 joined it; 電子零組件 unchanged.
_SECOND_SNAPSHOT = _classification_body(
    [
        {
            "stock_id": "2330",
            "industry": "半導體",
            "sub_industry": "晶圓代工",
            "date": "2026-02-01",
        },
        {"stock_id": "3034", "industry": "半導體", "sub_industry": "IC設計", "date": "2026-02-01"},
        {
            "stock_id": "2317",
            "industry": "電子零組件",
            "sub_industry": "連接器",
            "date": "2026-02-01",
        },
    ]
)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _ctx(
    tmp_path: object,
    session_factory: async_sessionmaker,
    *,
    leader: object,
) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=leader,  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        session_factory=session_factory,
    )


def _taipei(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_TAIPEI)


def _theme_job() -> tuple[ScheduledIngestJob, ...]:
    return (
        ScheduledIngestJob(
            "theme_industry_chain", time_(0, 30), seed_industry_themes, cadence=Cadence.MONTHLY
        ),
    )


async def test_daemon_pass_drives_seed_to_queryable_members(
    tmp_path: object,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        finmind_module, "build_async_client", make_json_client_factory(_FIRST_SNAPSHOT)
    )
    ctx = _ctx(tmp_path, session_factory, leader=AlwaysLeader())
    daemon = IngestDaemon(ctx, jobs=_theme_job())

    ran = await daemon.run_due_jobs(now=_taipei(2026, 1, 5, 1, 0))

    assert ran == ["theme_industry_chain"]
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        assert await store.get_theme_members(theme_key="半導體") == ["2330", "2454"]
        assert await store.get_theme_members(theme_key="電子零組件") == ["2317"]


async def test_second_monthly_fire_replaces_not_duplicates(
    tmp_path: object,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(tmp_path, session_factory, leader=AlwaysLeader())
    daemon = IngestDaemon(ctx, jobs=_theme_job())

    monkeypatch.setattr(
        finmind_module, "build_async_client", make_json_client_factory(_FIRST_SNAPSHOT)
    )
    assert await daemon.run_due_jobs(now=_taipei(2026, 1, 5, 1, 0)) == ["theme_industry_chain"]

    # A pass a few days later is NOT due yet (monthly rolling gap) — no re-fire, no change.
    assert await daemon.run_due_jobs(now=_taipei(2026, 1, 20, 1, 0)) == []

    # Next month: the second snapshot fires and must REPLACE, not union, the membership.
    monkeypatch.setattr(
        finmind_module, "build_async_client", make_json_client_factory(_SECOND_SNAPSHOT)
    )
    assert await daemon.run_due_jobs(now=_taipei(2026, 2, 5, 1, 0)) == ["theme_industry_chain"]

    async with session_factory() as session:
        members = await SqlAlchemyThemeStore(session).get_theme_members(theme_key="半導體")
    # 2454 dropped, 3034 added, 2330 present exactly once — a replace, not an ever-growing union.
    assert members == ["2330", "3034"]


async def test_non_leader_pass_seeds_nothing(
    tmp_path: object,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs: object) -> object:
        raise AssertionError("a non-leader must not fetch")

    monkeypatch.setattr(finmind_module, "build_async_client", _boom)
    ctx = _ctx(tmp_path, session_factory, leader=NeverLeader())
    daemon = IngestDaemon(ctx, jobs=_theme_job())

    assert await daemon.run_due_jobs(now=_taipei(2026, 1, 5, 1, 0)) == []
    async with session_factory() as session:
        assert await SqlAlchemyThemeStore(session).list_themes() == []


async def test_empty_roster_is_graceful_skip_not_error(
    tmp_path: object,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Free-tier token yields no rows for this Sponsor dataset — a graceful skip that leaves any
    existing snapshot untouched, never an error."""
    monkeypatch.setattr(
        finmind_module, "build_async_client", make_json_client_factory(_classification_body([]))
    )
    ctx = _ctx(tmp_path, session_factory, leader=AlwaysLeader())

    run = await seed_industry_themes(date_(2026, 1, 5), ctx)

    assert run.rows_written == 0
    async with session_factory() as session:
        assert await SqlAlchemyThemeStore(session).list_themes() == []
