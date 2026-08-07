"""Prompt-assembly seams: fixed precedence governance -> personality -> skill body -> pet
hint. Tests structure/shape (which layers are present, in which order) — not content that
doesn't exist yet, since only the governance layer is populated today."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.prompts import PromptLayers, assemble_system_prompt, default_prompt_layers
from agent.prompts.governance import GOVERNANCE_PROMPT, governance_prompt


def test_default_prompt_layers_only_populates_governance() -> None:
    layers = default_prompt_layers()

    assert layers.governance == governance_prompt()
    assert layers.personality is None
    assert layers.skill_body is None
    assert layers.pet_hint is None


def test_assemble_system_prompt_with_default_layers_is_just_governance() -> None:
    prompt = assemble_system_prompt()

    assert prompt == GOVERNANCE_PROMPT


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
