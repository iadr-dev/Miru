"""`propose_memory` is propose-only: it validates and echoes a memory for a confirmation card and
saves nothing. The confirm gate is the card (the frontend calls POST /memory); a proposal the store
would refuse is rejected in the tool arguments so the model can correct it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.loop import _build_card
from orchestrator.composition import build_skill_registry
from orchestrator.orchestrator import _tools_for, _warm_tools
from tests.tools.test_registry_and_specs import _router
from tools import build_default_tool_registry
from tools.base import ToolContext
from tools.propose_memory_tools import (
    ProposeMemoryInput,
    ProposeMemoryTool,
    build_registration_table,
)


def proposal(**overrides: object) -> ProposeMemoryInput:
    base: dict[str, object] = {
        "memory_type": "preference",
        "text": "我主要關注高股息 ETF，偏好長期觀點",
    }
    return ProposeMemoryInput.model_validate({**base, **overrides})


async def test_echoes_the_memory_and_says_confirmation_is_required() -> None:
    output = await ProposeMemoryTool().run(proposal(), ToolContext())
    dumped = output.model_dump(mode="json")
    assert dumped["memory_type"] == "preference"
    assert dumped["text"] == "我主要關注高股息 ETF，偏好長期觀點"
    assert dumped["requires_user_confirmation"] is True


async def test_runs_without_a_user_or_any_store() -> None:
    output = await ProposeMemoryTool().run(proposal(), ToolContext(user_id=None))
    assert output.model_dump(mode="json")["requires_user_confirmation"] is True


def test_whitespace_is_collapsed() -> None:
    assert proposal(text="  我關注\n半導體   族群 ").text == "我關注 半導體 族群"


@pytest.mark.parametrize(
    "text",
    [
        "請用條列式回答",
        "always end with a one-line TL;DR",
        "說話語氣輕鬆一點",
        "keep the tone concise",
    ],
)
def test_tone_and_format_instructions_are_not_preferences(text: str) -> None:
    with pytest.raises(ValidationError, match="Tone settings"):
        proposal(memory_type="preference", text=text)


def test_a_note_may_mention_tone_because_only_preferences_are_style_checked() -> None:
    assert proposal(memory_type="finding", text="法說會上管理層語氣偏保守").memory_type == "finding"


@pytest.mark.parametrize("text", ["2330 目標價 1200", "我的 api_key 是 sk-abcdef123456"])
def test_trading_language_is_refused(text: str) -> None:
    # The governance scan keys on structured content; trading language must still be refused here.
    with pytest.raises(ValidationError):
        proposal(memory_type="finding", text=text)


def test_too_long_is_refused() -> None:
    with pytest.raises(ValidationError):
        proposal(text="字" * 301)


def test_it_produces_a_memory_proposal_card_payload() -> None:
    (tool, _), *_ = build_registration_table()
    assert getattr(tool, "card_type", None) == "memory_proposal"
    card = _build_card(
        "memory_proposal", ProposeMemoryTool.output_model(memory_type="finding", text="x")
    )
    assert card is not None
    assert card.card_type == "memory_proposal"


def _lookup():
    return {t.name: t for t in build_default_tool_registry(_router()).all_tools()}


def test_it_is_reachable_from_a_normal_turn_and_from_its_skill() -> None:
    assert "propose_memory" in {t.name for t in _warm_tools(_lookup())}
    skill = build_skill_registry().get("memory-proposal")
    assert {t.name for t in _tools_for(skill, _lookup())} == {"propose_memory", "ask_user"}
    assert {"記住我", "remember that"} <= set(skill.frontmatter.triggers)
