"""The theme REST routes driven through the REAL FastAPI route via `TestClient` against a seeded
store — proving the full HTTP entrypoint works end-to-end, not just the tool in isolation. The
tool registry is built with the app's own session factory so a route reads the one DB the app is
pointed at; the store is then seeded over that same factory. No live services."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.themes.models import (
    ThemeKind,
    ThemeMember,
    ThemeSource,
    ThemeTierName,
    ThemeTierSource,
)
from analytics.themes.store import SqlAlchemyThemeStore
from config.settings import Settings
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.api.conftest import build_test_client
from tests.tools.conftest import make_router
from tools import build_default_tool_registry


def _seed_sync(session_factory: async_sessionmaker) -> None:
    async def _run() -> None:
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
            theme = await store.get_theme_by_key(key="半導體")
            assert theme is not None
            await store.upsert_theme_tier(
                theme_id=theme.id,
                sub_industry="晶圓代工",
                tier=ThemeTierName.MIDSTREAM,
                source=ThemeTierSource.CURATED,
            )
            await session.commit()

    asyncio.run(_run())


def _stub_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub caveat",
        payload={},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _client_with_seeded_themes(settings: Settings, agent_answer: str) -> TestClient:
    """A real `TestClient` whose tool registry carries the theme tools wired to the app's own
    session factory, with the theme store seeded over that same factory — so a theme route reads
    the one DB the app is pointed at."""
    client = build_test_client(settings, agent_answer=agent_answer)
    state = client.app.state.app_state
    sym = Symbol(id="2330", market=Market.TW)
    stub_router, _ = make_router(sym, dict.fromkeys(Capability, _stub_result()))
    registry = build_default_tool_registry(stub_router, session_factory=state.session_factory)
    # Swap the registry in place so the theme routes dispatch through the theme tools.
    object.__setattr__(state, "tool_registry", registry)
    _seed_sync(state.session_factory)
    return client


def test_list_themes_route(settings: Settings, agent_answer: str) -> None:
    client = _client_with_seeded_themes(settings, agent_answer)
    resp = client.get("/api/v1/themes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["theme_count"] == 1
    assert body["themes"][0]["key"] == "半導體"
    assert body["themes"][0]["member_count"] == 2


def test_list_themes_route_filters_by_kind(settings: Settings, agent_answer: str) -> None:
    client = _client_with_seeded_themes(settings, agent_answer)
    resp = client.get("/api/v1/themes", params={"kind": "concept"})
    assert resp.status_code == 200
    assert resp.json()["theme_count"] == 0


def test_theme_detail_route(settings: Settings, agent_answer: str) -> None:
    client = _client_with_seeded_themes(settings, agent_answer)
    resp = client.get("/api/v1/themes/半導體")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["name_en"] == "Semiconductor"
    assert {m["symbol"] for m in body["members"]} == {"2330", "2454"}


def test_theme_detail_unknown_key_is_graceful(settings: Settings, agent_answer: str) -> None:
    client = _client_with_seeded_themes(settings, agent_answer)
    resp = client.get("/api/v1/themes/does-not-exist")
    assert resp.status_code == 200
    assert resp.json()["found"] is False


def test_theme_tiers_route(settings: Settings, agent_answer: str) -> None:
    client = _client_with_seeded_themes(settings, agent_answer)
    resp = client.get("/api/v1/themes/半導體/tiers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["midstream"][0]["sub_industry"] == "晶圓代工"
    assert body["midstream"][0]["source"] == "curated"


def test_symbol_themes_reverse_route(settings: Settings, agent_answer: str) -> None:
    client = _client_with_seeded_themes(settings, agent_answer)
    resp = client.get("/api/v1/symbols/2330/themes", params={"market": Market.TW.value})
    assert resp.status_code == 200
    body = resp.json()
    assert body["theme_count"] == 1
    assert body["themes"][0]["theme_key"] == "半導體"
