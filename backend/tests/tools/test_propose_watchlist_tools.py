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


def test_each_action_needs_exactly_its_own_fields() -> None:
    # add/remove/move act on symbols; the group actions act on a group.
    with pytest.raises(ValidationError):
        change(action="move", group_name=None)  # a move needs a target group
    assert change(action="move", group_name="半導體").group_name == "半導體"
    with pytest.raises(ValidationError):
        change(action="rename_group", symbols=[], group_name="面板")  # missing new_name
    renamed = change(action="rename_group", symbols=[], group_name="面板", new_name="顯示器")
    assert renamed.new_name == "顯示器"
    with pytest.raises(ValidationError):
        change(action="rename_group", symbols=["2330"], group_name="a", new_name="b")
    with pytest.raises(ValidationError):
        change(action="delete_group", symbols=[], group_name=None)
    assert change(action="delete_group", symbols=[], group_name="記憶體").group_name == "記憶體"
    with pytest.raises(ValidationError):
        change(action="reorder_group", symbols=[], group_name="半導體")  # missing position
    ordered = change(action="reorder_group", symbols=[], group_name="半導體", position="first")
    assert ordered.position == "first"
    with pytest.raises(ValidationError):
        change(action="add", position="first")  # position is for reorder_group only
    with pytest.raises(ValidationError):
        change(action="add", new_name="x")  # new_name is for rename_group only


def test_group_names_are_cleaned_for_every_field() -> None:
    with pytest.raises(ValidationError):
        change(action="rename_group", symbols=[], group_name="面板", new_name="<b>x</b>")


async def test_a_group_action_echoes_back_for_the_card() -> None:
    args = change(action="reorder_group", symbols=[], group_name="半導體", position="last")
    dumped = (await ProposeWatchlistChangeTool().run(args, ToolContext())).model_dump(mode="json")
    assert dumped["action"] == "reorder_group"
    assert dumped["position"] == "last"
    assert dumped["symbols"] == []


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
        "把 2330 移到半導體群組",
        "把「面板」群組改名成顯示器",
        "刪除記憶體群組",
        "把半導體群組排到最前面",
    ],
)
def test_watchlist_phrasing_routes_to_the_watchlist_skill(message: str) -> None:
    registry = build_skill_registry()
    assert _keyword_fallback(message, registry).skills == ("watchlist-proposal",)


def test_the_name_lookup_is_internal_so_its_card_is_hidden_in_this_flow() -> None:
    # The proposal card already shows each resolved name; a raw "Symbol search" metadata card
    # beside it is noise.
    skill = build_skill_registry().get("watchlist-proposal")
    assert "get_symbol_directory_search" in skill.frontmatter.hide_cards
