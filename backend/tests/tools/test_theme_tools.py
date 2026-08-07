"""Theme-lookup tools driven through the REAL registered tool + `dispatch_tool` path against a
seeded relational store — proving the full dispatch entrypoint works, not just each tool's `run()`
in isolation. No live services: the store is seeded directly over in-memory SQLite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.themes.models import (
    ThemeKind,
    ThemeMember,
    ThemeSource,
    ThemeTierName,
    ThemeTierSource,
)
from analytics.themes.store import SqlAlchemyThemeStore
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from schemas.enums import Market, Tier, TLSPolicy
from tools.base import ToolContext, dispatch_tool
from tools.registry import ToolRegistry
from tools.theme_tools import build_registration_table


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        await store.upsert_theme(
            key="半導體",
            name_zh="半導體",
            name_en="Semiconductor",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        await store.replace_theme_members(
            theme_key="半導體",
            source=ThemeSource.INDUSTRY_CHAIN,
            members=[
                ThemeMember(
                    symbol="2330",
                    market="TW",
                    sub_industry="晶圓代工",
                    source=ThemeSource.INDUSTRY_CHAIN,
                ),
                ThemeMember(
                    symbol="2454",
                    market="TW",
                    sub_industry="IC設計",
                    source=ThemeSource.INDUSTRY_CHAIN,
                ),
            ],
        )
        # A concept theme with a researched, sub-1.0-confidence member.
        await store.upsert_theme(
            key="AI伺服器",
            name_zh="AI伺服器",
            kind=ThemeKind.CONCEPT,
            source=ThemeSource.CONCEPT_RESEARCH,
        )
        await store.replace_theme_members(
            theme_key="AI伺服器",
            source=ThemeSource.CONCEPT_RESEARCH,
            members=[
                ThemeMember(
                    symbol="2330",
                    market="TW",
                    sub_industry="晶圓代工",
                    confidence=0.7,
                    source=ThemeSource.CONCEPT_RESEARCH,
                ),
            ],
        )
        theme = await store.get_theme_by_key(key="半導體")
        assert theme is not None
        await store.upsert_theme_tier(
            theme_id=theme.id,
            sub_industry="晶圓代工",
            tier=ThemeTierName.MIDSTREAM,
            source=ThemeTierSource.CURATED,
        )
        await store.upsert_theme_tier(
            theme_id=theme.id,
            sub_industry="IC設計",
            tier=ThemeTierName.UPSTREAM,
            source=ThemeTierSource.HEURISTIC,
        )
        await session.commit()


def _registry(session_factory: async_sessionmaker) -> ToolRegistry:
    registry = ToolRegistry()
    for tool, immediate in build_registration_table(session_factory):
        registry.register(tool, immediate=immediate)
    return registry


def _ctx() -> ToolContext:
    return ToolContext(user_id=None, tier=Tier.FREE, tls_policy=TLSPolicy.STRICT)


async def test_get_theme_members_via_dispatch(session_factory: async_sessionmaker) -> None:
    await _seed(session_factory)
    registry = _registry(session_factory)
    tool = registry.get_tool("get_theme_members")
    assert tool is not None

    result = await dispatch_tool(
        tool, tool.input_model.model_validate({"theme_key": "半導體"}), _ctx()
    )
    data = result.model_dump()
    assert data["found"] is True
    assert data["name_en"] == "Semiconductor"
    assert data["member_count"] == 2
    symbols = {m["symbol"] for m in data["members"]}
    assert symbols == {"2330", "2454"}
    assert all(m["source"] == "industry_chain" for m in data["members"])


async def test_get_theme_members_unknown_key_is_graceful(
    session_factory: async_sessionmaker,
) -> None:
    await _seed(session_factory)
    registry = _registry(session_factory)
    tool = registry.get_tool("get_theme_members")
    assert tool is not None

    result = await dispatch_tool(
        tool, tool.input_model.model_validate({"theme_key": "does-not-exist"}), _ctx()
    )
    data = result.model_dump()
    assert data["found"] is False
    assert data["member_count"] == 0
    assert "does-not-exist" in data["message"]


async def test_get_symbol_themes_reverse_lookup_via_dispatch(
    session_factory: async_sessionmaker,
) -> None:
    await _seed(session_factory)
    registry = _registry(session_factory)
    tool = registry.get_tool("get_symbol_themes")
    assert tool is not None

    result = await dispatch_tool(
        tool, tool.input_model.model_validate({"id": "2330", "market": Market.TW.value}), _ctx()
    )
    data = result.model_dump()
    assert data["theme_count"] == 2
    by_key = {t["theme_key"]: t for t in data["themes"]}
    assert set(by_key) == {"半導體", "AI伺服器"}
    # The concept membership discloses its researched, sub-1.0 confidence.
    assert by_key["AI伺服器"]["confidence"] == 0.7


async def test_get_theme_tiers_groups_and_discloses_source(
    session_factory: async_sessionmaker,
) -> None:
    await _seed(session_factory)
    registry = _registry(session_factory)
    tool = registry.get_tool("get_theme_tiers")
    assert tool is not None

    result = await dispatch_tool(
        tool, tool.input_model.model_validate({"theme_key": "半導體"}), _ctx()
    )
    data = result.model_dump()
    assert data["found"] is True
    assert [t["sub_industry"] for t in data["midstream"]] == ["晶圓代工"]
    assert data["midstream"][0]["source"] == "curated"
    assert [t["sub_industry"] for t in data["upstream"]] == ["IC設計"]
    assert data["upstream"][0]["source"] == "heuristic"
