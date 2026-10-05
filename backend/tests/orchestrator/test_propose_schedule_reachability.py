"""`propose_schedule` must be reachable in a normal chat turn, by both routes (the warm set for
skill-less turns, and its skill's allowlist) — see `test_propose_alert_reachability.py`."""

from __future__ import annotations

from orchestrator.classifier import _keyword_fallback
from orchestrator.composition import build_skill_registry
from orchestrator.orchestrator import _tools_for, _warm_tools
from tests.tools.test_registry_and_specs import _router
from tools import build_default_tool_registry
from tools.base import Tool


def _lookup() -> dict[str, Tool]:
    return {t.name: t for t in build_default_tool_registry(_router()).all_tools()}


def test_propose_schedule_is_in_the_warm_set() -> None:
    assert "propose_schedule" in {t.name for t in _warm_tools(_lookup())}


def test_the_schedule_proposal_skill_allowlists_it_and_ask_user() -> None:
    skill = build_skill_registry().get("schedule-proposal")
    assert {t.name for t in _tools_for(skill, _lookup())} == {"propose_schedule", "ask_user"}


def test_the_skill_is_trigger_matchable_in_chinese_and_english() -> None:
    triggers = set(build_skill_registry().get("schedule-proposal").frontmatter.triggers)
    assert {"每日簡報", "定期", "daily briefing"} <= triggers


def test_recurring_requests_route_to_the_schedule_skill_but_one_off_ones_do_not() -> None:
    """A recurring phrasing must beat the one-off skills that share its words (盤前 → daily brief,
    收盤後 → chip review); without that, 'brief me every morning' ran the brief instead of proposing
    a schedule."""
    registry = build_skill_registry()

    def routed(message: str) -> tuple[str, ...]:
        return _keyword_fallback(message, registry).skills

    assert routed("每天開盤前幫我看一下 2330 和 2317 的簡報") == ("schedule-proposal",)
    assert routed("每週五幫我出一份 0050 的 ETF 週報") == ("schedule-proposal",)
    assert routed("收盤後幫我看 2330 的籌碼") == ("chip-flow-review",)
    assert routed("盤前簡報 2330") == ("daily-context-brief",)
    assert routed("2317 收盤價高於 300 的時候提醒我") == ("alert-proposal",)
