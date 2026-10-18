"""MCP callers get the same server-side Pro preview as the app: a Free/Premium token receives the
limited, `locked` result with a note the model can relay; a Pro token receives the full data.
The tier is resolved per call from the credential, through the real principal + dispatch path."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from mcp._dispatch import dispatch_and_render
from mcp.principal import resolve_principal
from schemas.enums import Tier
from tests.tools.test_theme_previews import _seed
from tools.base import ToolContext
from tools.registry import ToolRegistry
from tools.theme_tools import build_registration_table


class _Bearer:
    """Maps a bearer token to (user, tier), the shape `AccountsPrincipalResolver` returns."""

    def __init__(self, tiers: dict[str, Tier]) -> None:
        self._tiers = tiers

    async def resolve_bearer(self, token: str) -> tuple[str, Tier] | None:
        tier = self._tiers.get(token)
        return (f"user-{token}", tier) if tier is not None else None


@pytest.fixture
async def registry() -> AsyncIterator[ToolRegistry]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(factory)
    built = ToolRegistry()
    for tool, immediate in build_registration_table(factory):
        built.register(tool, immediate=immediate)
    yield built
    await engine.dispose()


async def _ctx(token: str | None) -> ToolContext:
    headers = {"authorization": f"Bearer {token}"} if token else {}
    bearer = _Bearer({"free": Tier.FREE, "premium": Tier.PREMIUM, "pro": Tier.PRO})
    return await resolve_principal(headers, bearer_resolver=bearer)


async def _run(registry: ToolRegistry, name: str, args: dict[str, object], token: str | None):
    tool = registry.get_tool(name)
    assert tool is not None
    result = await dispatch_and_render(tool, args, await _ctx(token))
    assert not result.is_error  # a preview is a normal result, never a tool error
    assert result.structured_content is not None
    return result.structured_content


@pytest.mark.parametrize("token", [None, "free", "premium"])
async def test_non_pro_token_gets_the_preview_with_a_relayable_note(
    registry: ToolRegistry, token: str | None
) -> None:
    edges = await _run(registry, "get_supply_chain_edges", {"id": "9001", "market": "TW"}, token)
    assert edges["locked"] is True
    assert edges["sources_locked"] is True
    assert edges["edge_count"] == 9
    assert "Pro plan" in edges["upgrade_note"]
    assert all(e["sources"] == [] for e in edges["edges"])

    graph = await _run(registry, "get_entity_graph", {"id": "9001", "market": "TW"}, token)
    assert graph["locked"] is True

    positions = await _run(registry, "get_chain_position", {"id": "9001", "market": "TW"}, token)
    assert len(positions["positions"]) == 1
    assert positions["locked"] is True

    theme = await _run(registry, "get_theme_members", {"theme_key": "wave"}, token)
    assert theme["locked"] is True
    assert len(theme["members"]) == 7

    listing = await _run(registry, "get_themes", {}, token)
    assert listing["concept_locked"] is True


async def test_pro_token_gets_the_full_data(registry: ToolRegistry) -> None:
    edges = await _run(registry, "get_supply_chain_edges", {"id": "9001", "market": "TW"}, "pro")
    assert edges["locked"] is False
    assert edges["edge_count"] == 13
    assert any(e["sources"] for e in edges["edges"])

    theme = await _run(registry, "get_theme_members", {"theme_key": "wave"}, "pro")
    assert (theme["locked"], len(theme["members"])) == (False, 9)

    listing = await _run(registry, "get_themes", {}, "pro")
    assert listing["concept_locked"] is False


async def test_a_token_is_resolved_to_its_own_tier_each_call(registry: ToolRegistry) -> None:
    """Nothing is cached between calls: Pro then Free then Pro on one registry."""
    counts = []
    for token in ("pro", "free", "pro"):
        edges = await _run(
            registry, "get_supply_chain_edges", {"id": "9001", "market": "TW"}, token
        )
        counts.append(edges["edge_count"])
    assert counts == [13, 9, 13]
