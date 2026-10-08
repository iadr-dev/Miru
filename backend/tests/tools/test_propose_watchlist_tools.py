"""`propose_watchlist_add` is propose-only: it validates and echoes a symbol for a confirmation
card and adds nothing. The confirm gate is the card (the frontend calls the watchlist routes)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.composition import build_skill_registry
from orchestrator.orchestrator import _tools_for, _warm_tools
from schemas.enums import Market
from tests.tools.test_registry_and_specs import _router
from tools import build_default_tool_registry
from tools.base import ToolContext
from tools.propose_watchlist_tools import (
    ProposeWatchlistAddInput,
    ProposeWatchlistAddTool,
    build_registration_table,
)


async def test_echoes_the_symbol_and_requires_confirmation() -> None:
    args = ProposeWatchlistAddInput(symbol=" 2330 ", market=Market.TW)
    output = await ProposeWatchlistAddTool().run(args, ToolContext(user_id=None))
    dumped = output.model_dump(mode="json")
    assert dumped["symbol"] == "2330"
    assert dumped["market"] == "TW"
    assert dumped["requires_user_confirmation"] is True


def test_us_tickers_are_uppercased() -> None:
    assert ProposeWatchlistAddInput(symbol="aapl", market=Market.US).symbol == "AAPL"
    assert ProposeWatchlistAddInput(symbol="brk.b", market=Market.US).symbol == "BRK.B"


@pytest.mark.parametrize("bad", ["", "2330 台積電", "<script>", "../etc", "x" * 17])
def test_a_non_ticker_is_refused(bad: str) -> None:
    with pytest.raises(ValidationError):
        ProposeWatchlistAddInput(symbol=bad, market=Market.TW)


def test_only_tw_and_us_are_supported() -> None:
    with pytest.raises(ValidationError):
        ProposeWatchlistAddInput(symbol="7203", market=Market.JP)


def test_it_declares_the_card_the_frontend_renders() -> None:
    (tool, _), *_ = build_registration_table()
    assert getattr(tool, "card_type", None) == "watchlist_proposal"


def _lookup():
    return {t.name: t for t in build_default_tool_registry(_router()).all_tools()}


def test_it_is_reachable_from_a_normal_turn_and_from_its_skill() -> None:
    assert "propose_watchlist_add" in {t.name for t in _warm_tools(_lookup())}
    skill = build_skill_registry().get("watchlist-proposal")
    assert {t.name for t in _tools_for(skill, _lookup())} == {"propose_watchlist_add", "ask_user"}
    assert {"加入自選", "add to my watchlist"} <= set(skill.frontmatter.triggers)


def test_watchlist_phrasing_routes_to_the_watchlist_skill() -> None:
    from orchestrator.classifier import _keyword_fallback  # noqa: PLC0415

    registry = build_skill_registry()

    def routed(message: str) -> tuple[str, ...]:
        return _keyword_fallback(message, registry).skills

    assert routed("把 2330 加到自選股") == ("watchlist-proposal",)
    assert routed("請幫我加入自選 0050") == ("watchlist-proposal",)
    assert routed("add AAPL to my watchlist") == ("watchlist-proposal",)
