"""`propose_watchlist_change` is propose-only: it validates and echoes an add/remove for a
confirmation card and changes nothing. The confirm gate is the card (the frontend calls the
watchlist routes)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.classifier import _keyword_fallback
from orchestrator.composition import build_skill_registry
from orchestrator.orchestrator import _tools_for, _warm_tools
from schemas.enums import Market
from tests.tools.test_registry_and_specs import _router
from tools import build_default_tool_registry
from tools.base import ToolContext
from tools.propose_watchlist_tools import (
    MAX_SYMBOLS,
    ProposeWatchlistChangeInput,
    ProposeWatchlistChangeTool,
    build_registration_table,
)


def change(**overrides: object) -> ProposeWatchlistChangeInput:
    base: dict[str, object] = {"action": "add", "market": Market.TW, "symbols": ["2409", "3481"]}
    return ProposeWatchlistChangeInput.model_validate({**base, **overrides})


async def test_echoes_the_change_and_requires_confirmation() -> None:
    output = await ProposeWatchlistChangeTool().run(change(group_name="面板"), ToolContext())
    dumped = output.model_dump(mode="json")
    assert dumped["action"] == "add"
    assert dumped["symbols"] == ["2409", "3481"]
    assert dumped["group_name"] == "面板"
    assert dumped["requires_user_confirmation"] is True


def test_symbols_are_trimmed_uppercased_and_deduplicated() -> None:
    assert change(symbols=[" aapl ", "AAPL", "brk.b"], market=Market.US).symbols == [
        "AAPL",
        "BRK.B",
    ]


@pytest.mark.parametrize("bad", ["", "2330 台積電", "<script>", "../etc", "x" * 17])
def test_a_non_ticker_is_refused(bad: str) -> None:
    with pytest.raises(ValidationError):
        change(symbols=[bad])


def test_the_batch_is_bounded() -> None:
    with pytest.raises(ValidationError):
        change(symbols=[])
    with pytest.raises(ValidationError):
        change(symbols=[str(1000 + i) for i in range(MAX_SYMBOLS + 1)])


def test_only_tw_and_us_are_supported() -> None:
    with pytest.raises(ValidationError):
        change(market=Market.JP)


def test_a_group_name_is_cleaned_and_only_for_add() -> None:
    assert change(group_name="  面板   族群 ").group_name == "面板 族群"
    assert change(group_name="   ").group_name is None
    with pytest.raises(ValidationError):
        change(group_name="<b>x</b>")
    with pytest.raises(ValidationError):
        change(group_name="x" * 31)
    with pytest.raises(ValidationError):
        change(action="remove", group_name="面板")


def test_it_declares_the_card_the_frontend_renders() -> None:
    (tool, _), *_ = build_registration_table()
    assert getattr(tool, "card_type", None) == "watchlist_proposal"


def _lookup():
    return {t.name: t for t in build_default_tool_registry(_router()).all_tools()}


def test_it_is_reachable_from_a_normal_turn_and_from_its_skill() -> None:
    assert "propose_watchlist_change" in {t.name for t in _warm_tools(_lookup())}
    skill = build_skill_registry().get("watchlist-proposal")
    # `get_symbol_directory_search` needs the ingested roster, which this test registry lacks, so
    # only the tools it does build can appear - but the skill must ask for it.
    assert "get_symbol_directory_search" in skill.frontmatter.tools
    assert {t.name for t in _tools_for(skill, _lookup())} >= {
        "propose_watchlist_change",
        "ask_user",
    }


@pytest.mark.parametrize(
    "message",
    [
        "把 2330 加到自選股",
        "請幫我加入自選 0050",
        "把友達和群創加進自選股",
        "add AAPL to my watchlist",
        "把 2330 從自選股移除",
        "幫我移除自選股裡的 2317",
        "remove 2330 from my watchlist",
    ],
)
def test_watchlist_phrasing_routes_to_the_watchlist_skill(message: str) -> None:
    registry = build_skill_registry()
    assert _keyword_fallback(message, registry).skills == ("watchlist-proposal",)
