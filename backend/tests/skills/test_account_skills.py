"""`account-overview` and `watchlist-review`: the skills over the user's own data. Pins their tool
allowlists, that they stay descriptive, and that their triggers do not steal requests from the
propose-only skills (a "提醒我 ..." request must still reach `alert-proposal`)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orchestrator.classifier import _keyword_fallback
from orchestrator.composition import build_skill_registry
from schemas.enums import DataClass
from tests.skills.test_tool_exposure import _registered
from tests.tools.test_registry_and_specs import _router
from tools import build_default_tool_registry
from tools.account_tools import build_account_tools
from tools.registry import ToolRegistry

ACCOUNT_TOOLS = ("get_my_watchlists", "get_my_automations", "get_my_usage", "get_my_notifications")
WEEKLY_REVIEW_TOOL = "get_watchlist_weekly_review"


def _skill(name: str):
    return build_skill_registry().get(name)


def test_account_overview_allowlist_is_exactly_the_four_account_tools() -> None:
    assert _skill("account-overview").frontmatter.tools == ACCOUNT_TOOLS


def test_watchlist_review_allowlist() -> None:
    assert _skill("watchlist-review").frontmatter.tools == (
        "get_watchlist_weekly_review",
        "get_my_watchlists",
        "get_symbol_quotes",
        "get_price_history",
        "get_dividend_calendar",
        "get_calendar_events",
        "get_stock_score",
    )


def test_only_the_two_skills_expose_the_private_tools_and_they_exist() -> None:
    private = {t.name for t in build_account_tools(object(), ToolRegistry())}  # type: ignore[arg-type]
    assert private == set(ACCOUNT_TOOLS)
    private |= {WEEKLY_REVIEW_TOOL}  # the weekly review is a private tool of its own module
    assert private <= _registered()
    users = {
        s.name for s in build_skill_registry().all_skills() if private & set(s.frontmatter.tools)
    }
    assert users == {"account-overview", "watchlist-review"}


def test_neither_skill_can_write() -> None:
    for name in ("account-overview", "watchlist-review"):
        assert not [t for t in _skill(name).frontmatter.tools if t.startswith("propose_")]


@pytest.mark.parametrize("name", ["account-overview", "watchlist-review"])
def test_boundaries_keep_it_descriptive_and_signed_in_only(name: str) -> None:
    text = " ".join(_skill(name).frontmatter.boundaries)
    assert "登入" in text  # says plainly when the user is signed out
    assert "其他使用者" in text  # never reveals other users' data
    assert "清單為空" in text  # says plainly when a list is empty
    assert "捏造" in text  # no invented numbers
    if name == "watchlist-review":
        assert "買進" in text
        assert "目標價" in text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("我的自選股有哪些", "account-overview"),
        ("我設定了哪些提醒", "account-overview"),
        ("我今天還剩多少額度", "account-overview"),
        ("看一下我的通知", "account-overview"),
        ("my alerts", "account-overview"),
        ("watchlist dividends coming up", "watchlist-review"),
        # The propose-only skills keep their requests.
        ("2317 收盤價高於 300 的時候提醒我", "alert-proposal"),
        ("提醒我 2330 收盤價高於 1000", "alert-proposal"),
        ("加入自選 2330", "watchlist-proposal"),
        ("每天開盤前幫我看一下 2330 和 2317 的簡報", "schedule-proposal"),
        # The weekly watchlist review: setting it up is a proposal, reading it is the review.
        ("每週六早上幫我回顧自選股", "schedule-proposal"),
        ("每週幫我回顧自選股", "schedule-proposal"),
        ("幫我設定每週自選股回顧", "schedule-proposal"),
        ("review my watchlist every Saturday", "schedule-proposal"),
        ("weekly watchlist review", "schedule-proposal"),
        ("這週自選股怎麼樣", "watchlist-review"),
        ("自選股週回顧", "watchlist-review"),
    ],
)
def test_keyword_fallback_routing(message: str, expected: str) -> None:
    assert _keyword_fallback(message, build_skill_registry()).skills == (expected,)


def test_the_private_data_class_is_what_gates_them() -> None:
    assert all(
        t.data_class is DataClass.PRIVATE
        for t in build_account_tools(object(), ToolRegistry())  # type: ignore[arg-type]
    )


def test_the_weekly_review_tool_is_private_and_only_the_review_skill_reads_it() -> None:
    tool = next(
        t
        for t in build_default_tool_registry(
            _router(), parquet_store=MagicMock(), account_reader=MagicMock()
        ).all_tools()
        if t.name == WEEKLY_REVIEW_TOOL
    )
    assert tool.data_class is DataClass.PRIVATE
    users = {
        s.name
        for s in build_skill_registry().all_skills()
        if WEEKLY_REVIEW_TOOL in s.frontmatter.tools
    }
    assert users == {"watchlist-review"}


def test_the_scheduled_review_run_reaches_the_review_skill_with_its_tool() -> None:
    skill = _skill("watchlist-review")
    assert "review" in skill.frontmatter.inputs
    assert WEEKLY_REVIEW_TOOL in skill.frontmatter.tools
    text = " ".join(skill.frontmatter.boundaries)
    assert "不得自行計算" in text  # the figures are the tool's, not the model's


def test_proposing_the_review_needs_no_tickers() -> None:
    skill = _skill("schedule-proposal")
    assert skill.frontmatter.inputs["targets"].required is False
    assert skill.frontmatter.tools == ("propose_schedule", "ask_user")
