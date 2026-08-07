"""The pet persona hint: cosmetic tone only, subordinate to governance, never advisory."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.guardrails import check_output
from agent.prompts import PromptLayers, assemble_system_prompt
from pets.models import ObtainedVia, OwnedPet
from pets.persona import pet_hint_for


def _pet(pet_id: str, *, level: int = 1) -> OwnedPet:
    now = datetime.now(UTC)
    return OwnedPet(
        id="p1",
        user_id="u1",
        pet_id=pet_id,
        is_active=True,
        level=level,
        xp=0,
        obtained_via=ObtainedVia.PULL,
        created_at=now,
        updated_at=now,
    )


def test_no_pet_means_no_hint() -> None:
    assert pet_hint_for(None) is None


def test_hint_mentions_pet_and_is_tone_only() -> None:
    hint = pet_hint_for(_pet("sisi_magpie"))
    assert hint is not None
    assert "思思藍鵲" in hint
    # It explicitly disclaims changing data/analysis/caveats.
    assert "免責" in hint or "不改變" in hint


@pytest.mark.governance
def test_pet_hint_is_the_lowest_layer() -> None:
    layers = PromptLayers(
        governance="GOVERNANCE FLOOR",
        personality="PERSONALITY VOICE",
        pet_hint=pet_hint_for(_pet("sisi_magpie")),
    )
    prompt = assemble_system_prompt(layers)
    # Order: governance < personality < pet hint.
    assert prompt.index("GOVERNANCE FLOOR") < prompt.index("PERSONALITY VOICE")
    assert prompt.index("PERSONALITY VOICE") < prompt.index("思思藍鵲")


@pytest.mark.governance
def test_pet_hint_fragment_carries_no_trading_language() -> None:
    for pet_id in ("sisi_magpie", "leopard_cat", "mountain_sparrow"):
        hint = pet_hint_for(_pet(pet_id, level=15))
        assert hint is not None
        verdict = check_output(hint, has_market_data=False)
        assert verdict.reason != "trading_language_redacted"
