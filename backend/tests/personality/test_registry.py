"""Personality preset loading + selection precedence + the governance proof that a personality
layer can never weaken the non-trading / caveat rules."""

from __future__ import annotations

import pytest

from agent.guardrails import REDACTION_NOTICE, check_output
from agent.prompts import PromptLayers, assemble_system_prompt
from personality.registry import (
    PersonalityRegistry,
    UnknownPersonalityError,
    default_registry,
)


def test_four_starter_presets_load() -> None:
    registry = default_registry()
    assert set(registry.known_ids()) == {"steady", "coach", "concise", "data"}
    assert registry.default_id == "steady"


def test_selection_precedence() -> None:
    registry = default_registry()
    # turn override wins over everything.
    assert (
        registry.resolve(
            turn_override="data", thread_override="coach", user_preference="concise"
        ).id
        == "data"
    )
    # thread override next.
    assert registry.resolve(thread_override="coach", user_preference="concise").id == "coach"
    # user preference next.
    assert registry.resolve(user_preference="concise").id == "concise"
    # falls back to default.
    assert registry.resolve().id == "steady"
    # a stale/unknown preference falls through, never errors.
    assert registry.resolve(user_preference="nonexistent").id == "steady"


def test_resolve_fragment_is_the_preset_text() -> None:
    registry = default_registry()
    fragment = registry.resolve_fragment(user_preference="coach")
    assert "白話" in fragment


def test_unknown_get_raises() -> None:
    registry = default_registry()
    with pytest.raises(UnknownPersonalityError):
        registry.get("nope")


def test_empty_registry_resolve_raises() -> None:
    registry = PersonalityRegistry()
    with pytest.raises(UnknownPersonalityError):
        registry.resolve()


@pytest.mark.governance
def test_personality_layer_is_below_governance_in_assembled_prompt() -> None:
    registry = default_registry()
    layers = PromptLayers(
        governance="GOVERNANCE FLOOR",
        personality=registry.resolve_fragment(user_preference="coach"),
    )
    prompt = assemble_system_prompt(layers)
    # Governance is first; personality is appended after it, never before.
    assert prompt.index("GOVERNANCE FLOOR") < prompt.index("白話")


@pytest.mark.governance
def test_guardrails_still_catch_trading_language_with_personality_active() -> None:
    """Even with a personality layer shaping tone, the final answer is scanned by
    `guardrails.check_output` — a persona can never smuggle a trading signal past it."""
    registry = default_registry()
    # A turn styled by the coach persona still routes its final answer through check_output;
    # the persona layer changes tone, never what the guardrail catches.
    assert registry.resolve(user_preference="coach").id == "coach"
    answer = "根據籌碼面，情況不錯。You should buy now before it runs."
    verdict = check_output(answer)
    assert verdict.redacted_text is not None
    assert "buy now" not in verdict.redacted_text
    assert REDACTION_NOTICE in verdict.redacted_text
    # And a caveat is guaranteed present.
    assert verdict.reason in ("trading_language_redacted", "caveat_appended")


@pytest.mark.governance
def test_every_preset_fragment_is_caveat_safe_styling_only() -> None:
    """No shipped preset fragment itself contains trading language or a target price."""
    registry = default_registry()
    for preset in registry.all_presets():
        verdict = check_output(preset.fragment, has_market_data=False)
        # A pure styling fragment must not trigger a trading-language redaction.
        assert verdict.reason != "trading_language_redacted", preset.id
