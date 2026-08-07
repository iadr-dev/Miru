"""End-to-end Layer-2 tier-seed tests.

The failure-mode guard: these do not test `classify_tier` in isolation only (that has its own unit
test) nor assert only that the YAML loads. They drive the REAL `IngestDaemon.run_due_jobs` monthly
pass with BOTH the membership seed and the tier seed wired, then query the REAL
`ThemeStore.get_theme_tiers`, proving the tier data is genuinely queryable via the store after the
common daemon call path — for a curated sub-industry (`source='curated'`) AND an uncurated one only
the heuristic would tier (`source='heuristic'`). A second test proves curated-wins-on-re-run: a
second identical pass never flips a curated row to heuristic and never duplicates it. No live
network / no live DB (in-memory SQLite + monkeypatched HTTP client).
"""

from __future__ import annotations

from calendar.trading_days import TradingDayCalendar
from collections.abc import AsyncIterator
from datetime import datetime
from datetime import time as time_
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import sources.finmind as finmind_module
from analytics.themes.models import ThemeTierName, ThemeTierSource
from analytics.themes.seed import seed_industry_themes
from analytics.themes.store import SqlAlchemyThemeStore
from analytics.themes.tiers_seed import seed_theme_tiers
from config.settings import Settings
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from ingest.context import IngestContext
from ingest.daemon import Cadence, IngestDaemon, ScheduledIngestJob
from ingest.leader import AlwaysLeader
from ingest.store import ParquetStore
from schemas.enums import TLSPolicy
from sources.finmind import FinMindAdapter
from tests.sources.conftest import make_json_client_factory

_TAIPEI = ZoneInfo("Asia/Taipei")


def _classification_body(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"msg": "success", "status": 200, "data": rows}


# 晶圓代工 is curated (upstream) under 半導體; 電子零件測試 is NOT in the curated map, so only the
# heuristic classifier would tier it (電子零件測試 matches no keyword -> midstream default).
_SNAPSHOT = _classification_body(
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
            "sub_industry": "電子零件測試",
            "date": "2026-01-01",
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


def _ctx(tmp_path: object, session_factory: async_sessionmaker) -> IngestContext:
    return IngestContext(
        finmind=FinMindAdapter(settings=Settings(finmind_api_key="test-token")),
        store=ParquetStore(root=tmp_path),  # type: ignore[arg-type]
        leader=AlwaysLeader(),  # type: ignore[arg-type]
        calendar=TradingDayCalendar(),
        tls_policy=TLSPolicy.STRICT,
        session_factory=session_factory,
    )


def _taipei(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_TAIPEI)


def _theme_and_tier_jobs() -> tuple[ScheduledIngestJob, ...]:
    # Same order and fire time as the real SCHEDULED_JOBS tuple: membership seed, then tier seed.
    return (
        ScheduledIngestJob(
            "theme_industry_chain", time_(0, 30), seed_industry_themes, cadence=Cadence.MONTHLY
        ),
        ScheduledIngestJob("theme_tiers", time_(0, 30), seed_theme_tiers, cadence=Cadence.MONTHLY),
    )


async def test_daemon_pass_drives_tier_seed_to_queryable_rows(
    tmp_path: object,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(_SNAPSHOT))
    daemon = IngestDaemon(_ctx(tmp_path, session_factory), jobs=_theme_and_tier_jobs())

    ran = await daemon.run_due_jobs(now=_taipei(2026, 1, 5, 1, 0))

    # Both fire, in order: memberships first so their sub-industries exist to tier.
    assert ran == ["theme_industry_chain", "theme_tiers"]

    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)

        semi = {t.sub_industry: t for t in await store.get_theme_tiers(theme_key="半導體")}
        # Curated: 晶圓代工 is editor-placed upstream.
        assert semi["晶圓代工"].tier is ThemeTierName.UPSTREAM
        assert semi["晶圓代工"].source is ThemeTierSource.CURATED

        # Uncurated sub-industry only the heuristic would tier: 電子零件測試 in 電子零組件 (a
        # theme the curated map does not cover at all), defaulting to midstream.
        elec = {t.sub_industry: t for t in await store.get_theme_tiers(theme_key="電子零組件")}
        assert elec["電子零件測試"].tier is ThemeTierName.MIDSTREAM
        assert elec["電子零件測試"].source is ThemeTierSource.HEURISTIC


async def test_curated_wins_on_reseed(
    tmp_path: object,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finmind_module, "build_async_client", make_json_client_factory(_SNAPSHOT))
    ctx = _ctx(tmp_path, session_factory)

    # First full pass: memberships + tiers.
    daemon = IngestDaemon(ctx, jobs=_theme_and_tier_jobs())
    assert await daemon.run_due_jobs(now=_taipei(2026, 1, 5, 1, 0)) == [
        "theme_industry_chain",
        "theme_tiers",
    ]

    # A fresh daemon fires the next monthly window with the same data — a re-seed.
    daemon2 = IngestDaemon(ctx, jobs=_theme_and_tier_jobs())
    assert await daemon2.run_due_jobs(now=_taipei(2026, 2, 5, 1, 0)) == [
        "theme_industry_chain",
        "theme_tiers",
    ]

    async with session_factory() as session:
        tiers = await SqlAlchemyThemeStore(session).get_theme_tiers(theme_key="半導體")

    # 晶圓代工 appears exactly once and is still curated — never duplicated, never flipped.
    curated_hits = [t for t in tiers if t.sub_industry == "晶圓代工"]
    assert len(curated_hits) == 1
    assert curated_hits[0].source is ThemeTierSource.CURATED
    assert curated_hits[0].tier is ThemeTierName.UPSTREAM
