"""`event-study` skill wiring: the chat agent only receives the tools of the skill a turn routes to
(`Orchestrator._tools_for`), so `get_event_study` must sit in the allowlists of the skills a
disposition / attention / capital-reduction / buy-back question can land in, and the keyword
fallback must send historical-outcome questions to the new skill without taking the plain
disposition-list or corporate-action questions away from their own skills."""

from __future__ import annotations

import pytest

from governance.textscan import scan_structured_payload
from orchestrator.classifier import _keyword_fallback
from orchestrator.composition import build_skill_registry

_TOOL = "get_event_study"


def test_the_new_skill_exists_and_lists_the_tool_with_its_context_siblings() -> None:
    skill = build_skill_registry().get("event-study")

    assert skill is not None
    assert skill.frontmatter.tools == ("get_canonical_context", "get_source_health", _TOOL)
    assert skill.frontmatter.freshness_rule
    assert skill.frontmatter.boundaries


@pytest.mark.parametrize(
    "name", ["event-study", "disposition-watch", "corporate-actions-and-market-value"]
)
def test_every_skill_a_covered_event_question_can_route_to_has_the_tool(name: str) -> None:
    skill = build_skill_registry().get(name)

    assert skill is not None
    assert _TOOL in skill.frontmatter.tools


def test_the_skills_keep_the_descriptive_only_boundary() -> None:
    registry = build_skill_registry()
    for name in ("event-study", "disposition-watch", "corporate-actions-and-market-value"):
        skill = registry.get(name)
        assert skill is not None
        text = " ".join(skill.frontmatter.boundaries)
        assert "不構成投資建議" in text, name
    study = registry.get("event-study")
    assert study is not None
    boundaries = " ".join(study.frontmatter.boundaries)
    assert "樣本數 n" in boundaries and "insufficient_sample" in boundaries
    assert "不得說成訊號" in boundaries


def test_the_skill_menu_text_passes_the_governance_scan() -> None:
    skill = build_skill_registry().get("event-study")
    assert skill is not None
    advertised = {
        "description": skill.frontmatter.description,
        "output": list(skill.frontmatter.output_structure),
        "freshness": skill.frontmatter.freshness_rule,
    }

    cleaned, redacted = scan_structured_payload(advertised)

    assert redacted is False
    assert cleaned == advertised


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("處置股出關後 5 天、20 天的歷史表現如何", "event-study"),
        ("被列入注意股之後 20 個交易日平均漲跌多少", "event-study"),
        ("2330 減資恢復買賣後的歷史表現", "event-study"),
        ("庫藏股買回開始之後，歷史上股價怎麼走", "event-study"),
        ("What happened historically after stocks were put on the attention list?", "event-study"),
        ("Run an event study on disposition releases", "event-study"),
        # the neighbours keep their own questions
        ("今天有哪些處置股", "disposition-watch"),
        ("2330 的減資恢復買賣參考價", "corporate-actions-and-market-value"),
    ],
)
def test_the_keyword_fallback_routes_each_phrasing(message: str, expected: str) -> None:
    assert _keyword_fallback(message, build_skill_registry()).skills == (expected,)
