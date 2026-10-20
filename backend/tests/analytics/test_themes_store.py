"""`SqlAlchemyThemeStore` tests over an in-memory SQLite schema — upsert, snapshot-replace,
the screener hot-path read, and the reverse symbol->themes lookup. No live services."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.themes.models import ThemeKind, ThemeMember, ThemeSource, ThemeStatus
from analytics.themes.store import SqlAlchemyThemeStore, ThemeNotFoundError
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _member(symbol: str, sub: str = "") -> ThemeMember:
    return ThemeMember(
        symbol=symbol, market="TW", sub_industry=sub, source=ThemeSource.INDUSTRY_CHAIN
    )


async def test_upsert_theme_is_idempotent_on_key(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        first = await store.upsert_theme(
            key="半導體",
            name_zh="半導體",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        again = await store.upsert_theme(
            key="半導體",
            name_zh="半導體業",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        await session.commit()

    assert first.id == again.id  # same row, not a duplicate
    assert again.name_zh == "半導體業"
    async with session_factory() as session:
        themes = await SqlAlchemyThemeStore(session).list_themes()
    assert len(themes) == 1
    assert themes[0].status is ThemeStatus.ACTIVE


async def test_replace_theme_members_and_hot_path_read(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        await store.upsert_theme(
            key="半導體",
            name_zh="半導體",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        written = await store.replace_theme_members(
            theme_key="半導體",
            source=ThemeSource.INDUSTRY_CHAIN,
            members=[_member("2330", "IC設計"), _member("2454")],
        )
        await session.commit()

    assert written == 2
    async with session_factory() as session:
        members = await SqlAlchemyThemeStore(session).get_theme_members(theme_key="半導體")
    assert members == ["2330", "2454"]


async def test_count_theme_members(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        await store.upsert_theme(
            key="半導體",
            name_zh="半導體",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        await store.replace_theme_members(
            theme_key="半導體",
            source=ThemeSource.INDUSTRY_CHAIN,
            members=[_member("2330"), _member("2454")],
        )
        await session.commit()

    async with session_factory() as session:
        count = await SqlAlchemyThemeStore(session).count_theme_members(theme_key="半導體")
    assert count == 2


async def test_count_theme_members_unknown_key_is_zero(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        count = await SqlAlchemyThemeStore(session).count_theme_members(theme_key="does-not-exist")
    assert count == 0


async def test_replace_theme_members_replaces_not_unions(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        await store.upsert_theme(
            key="半導體",
            name_zh="半導體",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        await store.replace_theme_members(
            theme_key="半導體",
            source=ThemeSource.INDUSTRY_CHAIN,
            members=[_member("2330"), _member("2454")],
        )
        await session.commit()

    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        # A fresh snapshot with a different membership set — the old rows must be gone, not unioned.
        await store.replace_theme_members(
            theme_key="半導體",
            source=ThemeSource.INDUSTRY_CHAIN,
            members=[_member("2330"), _member("3711")],
        )
        await session.commit()

    async with session_factory() as session:
        members = await SqlAlchemyThemeStore(session).get_theme_members(theme_key="半導體")
    assert members == ["2330", "3711"]  # 2454 dropped, 3711 added, 2330 not doubled


async def test_get_symbol_themes_reverse_lookup(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        for key in ("半導體", "IC設計"):
            await store.upsert_theme(
                key=key, name_zh=key, kind=ThemeKind.INDUSTRY, source=ThemeSource.INDUSTRY_CHAIN
            )
            await store.replace_theme_members(
                theme_key=key,
                source=ThemeSource.INDUSTRY_CHAIN,
                members=[_member("2330", sub=key)],
            )
        await session.commit()

    async with session_factory() as session:
        themes = await SqlAlchemyThemeStore(session).get_symbol_themes(symbol="2330", market="TW")
    assert {t.theme_key for t in themes} == {"半導體", "IC設計"}


async def test_get_theme_member_details_carries_provenance(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        await store.upsert_theme(
            key="半導體",
            name_zh="半導體",
            kind=ThemeKind.INDUSTRY,
            source=ThemeSource.INDUSTRY_CHAIN,
        )
        await store.replace_theme_members(
            theme_key="半導體",
            source=ThemeSource.INDUSTRY_CHAIN,
            members=[_member("2330", "晶圓代工"), _member("2454", "IC設計")],
        )
        await session.commit()

    async with session_factory() as session:
        members = await SqlAlchemyThemeStore(session).get_theme_member_details(theme_key="半導體")
    assert [m.symbol for m in members] == ["2330", "2454"]
    assert members[0].sub_industry == "晶圓代工"
    assert all(m.source is ThemeSource.INDUSTRY_CHAIN for m in members)


async def test_get_theme_member_details_unknown_key_is_empty(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        members = await SqlAlchemyThemeStore(session).get_theme_member_details(theme_key="nope")
    assert members == []


async def test_replace_members_on_missing_theme_raises(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        store = SqlAlchemyThemeStore(session)
        members = [_member("2330")]
        with pytest.raises(ThemeNotFoundError):
            await store.replace_theme_members(
                theme_key="does-not-exist",
                source=ThemeSource.INDUSTRY_CHAIN,
                members=members,
            )


async def test_get_theme_members_unknown_key_is_empty(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        members = await SqlAlchemyThemeStore(session).get_theme_members(theme_key="nope")
    assert members == []
