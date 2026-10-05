"""`propose_alert` must be reachable by the model in a normal chat turn — a registered tool is NOT
automatically callable; the orchestrator hands a turn only its skill's allowlist, or the warm set
when no skill is chosen. Two routes exist and both are pinned here, so dropping either one (which
would silently make 'alert me when…' do nothing) fails a test."""

from __future__ import annotations

from orchestrator.composition import build_skill_registry
from orchestrator.orchestrator import _tools_for, _warm_tools
from tests.tools.test_registry_and_specs import _router
from tools import build_default_tool_registry
from tools.base import Tool


def _lookup() -> dict[str, Tool]:
    return {t.name: t for t in build_default_tool_registry(_router()).all_tools()}


def test_propose_alert_is_in_the_warm_set_for_skill_less_turns() -> None:
    assert "propose_alert" in {t.name for t in _warm_tools(_lookup())}


def test_the_alert_proposal_skill_allowlists_it_and_ask_user() -> None:
    skill = build_skill_registry().get("alert-proposal")
    names = {t.name for t in _tools_for(skill, _lookup())}
    assert names == {"propose_alert", "ask_user"}


def test_the_skill_is_trigger_matchable_in_chinese_and_english() -> None:
    triggers = set(build_skill_registry().get("alert-proposal").frontmatter.triggers)
    assert {"提醒我", "通知我", "alert me"} <= triggers


def test_no_mutating_tool_is_exposed_to_the_agent() -> None:
    # The confirm gate is the card. If a tool that *creates* alerts ever appears in the agent's
    # reachable set, the propose-only guarantee is gone.
    reachable = {t.name for t in _warm_tools(_lookup())}
    assert not {n for n in reachable if n.startswith(("create_", "delete_", "update_"))}
