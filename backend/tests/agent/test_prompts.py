"""Prompt-assembly seams: fixed precedence governance -> personality -> skill body -> pet
hint. Tests structure/shape (which layers are present, in which order) — not content that
doesn't exist yet, since only the governance layer is populated today."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.prompts import PromptLayers, assemble_system_prompt, default_prompt_layers
from agent.prompts.governance import governance_prompt


def test_default_prompt_layers_only_populates_governance() -> None:
    layers = default_prompt_layers()

    assert layers.governance == governance_prompt()
    assert layers.personality is None
    assert layers.skill_body is None
    assert layers.pet_hint is None


def test_assemble_system_prompt_with_default_layers_is_just_governance() -> None:
    prompt = assemble_system_prompt()

    assert prompt == governance_prompt()


def test_assemble_system_prompt_places_governance_first_always() -> None:
    layers = PromptLayers(
        governance="GOVERNANCE_TEXT",
        personality="PERSONALITY_TEXT",
        skill_body="SKILL_TEXT",
        pet_hint="PET_TEXT",
    )

    prompt = assemble_system_prompt(layers)

    assert prompt.startswith("GOVERNANCE_TEXT")
    governance_index = prompt.index("GOVERNANCE_TEXT")
    personality_index = prompt.index("PERSONALITY_TEXT")
    skill_index = prompt.index("SKILL_TEXT")
    pet_index = prompt.index("PET_TEXT")
    assert governance_index < personality_index < skill_index < pet_index


def test_assemble_system_prompt_skips_unpopulated_optional_layers() -> None:
    layers = PromptLayers(governance="GOVERNANCE_TEXT", skill_body="SKILL_TEXT")

    prompt = assemble_system_prompt(layers)

    assert "GOVERNANCE_TEXT" in prompt
    assert "SKILL_TEXT" in prompt
    assert prompt.count("\n\n") == 1  # exactly two populated parts joined once


def test_prompt_layers_is_frozen() -> None:
    layers = PromptLayers(governance="x")

    try:
        layers.governance = "y"  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True

    assert raised


def test_governance_layer_cannot_be_omitted() -> None:
    """`PromptLayers.governance` is a required field — there is no constructor path that
    produces a layer stack with an empty/missing governance layer."""
    with pytest.raises((TypeError, ValidationError)):
        PromptLayers()  # type: ignore[call-arg]


def test_governance_prompt_defaults_to_zh_tw_locale() -> None:
    prompt = governance_prompt()

    assert "interface locale: zh-TW" in prompt


def test_governance_prompt_interpolates_explicit_locale() -> None:
    prompt = governance_prompt("en")

    assert "interface locale: en" in prompt
    assert "interface locale: zh-TW" not in prompt


def test_governance_prompt_states_provider_data_beats_web_search() -> None:
    prompt = governance_prompt()

    assert "always authoritative" in prompt
    assert "web search" in prompt
    assert "gap-filling" in prompt


def test_governance_prompt_bans_internal_identifiers_and_code_syntax() -> None:
    """Rule 11 — the fix for a live-found leak: a skill's own tool-call trace (e.g.
    `get_chip_broker_agg`) and raw Python-ish syntax (`rows: []`) surfaced verbatim in a
    zh-TW answer. Checked as literal example fragments so a future prompt edit that drops the
    rule's concrete guidance fails loudly, not just a vague "mentions tool names" substring."""
    prompt = governance_prompt()

    assert "get_chip_broker_agg" in prompt
    assert "rows: []" in prompt


def test_governance_prompt_encourages_decisive_descriptive_language() -> None:
    """Rule 12 — descriptive/comparative decisiveness is expected, never a relaxation of rule
    1's actionable-advice ban. Checked as literal example fragments so a future prompt edit
    that drops the rule's concrete guidance or its cross-reference to rule 1 fails loudly."""
    prompt = governance_prompt()

    assert "structurally the more volatile of the two" in prompt
    assert "rule 1's actionable territory" in prompt


def test_default_prompt_layers_forwards_locale_to_governance() -> None:
    layers = default_prompt_layers("en")

    assert layers.governance == governance_prompt("en")
    assert "interface locale: en" in layers.governance


def test_governance_prompt_tells_the_agent_to_honour_the_requested_format() -> None:
    prompt = governance_prompt()
    assert "In one sentence" in prompt
    assert "TL;DR" in prompt  # named as something not to add
    assert "never add" in prompt
