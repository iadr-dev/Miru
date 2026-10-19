"""The curated BYOK tier table: complete, internally consistent and fully priced."""

from __future__ import annotations

from datetime import date

import pytest

from llm.byok_profiles import (
    BYOK_PROVIDER_ORDER,
    BYOK_PROVIDER_TIERS,
    is_selectable,
    provider_tiers,
)
from llm.pricing import price_for
from llm.registry import BYOK_PROVIDERS


def test_order_and_table_cover_the_same_providers() -> None:
    assert set(BYOK_PROVIDER_ORDER) == set(BYOK_PROVIDER_TIERS) == set(BYOK_PROVIDERS)
    assert BYOK_PROVIDER_ORDER == ("anthropic", "openai", "gemini", "openrouter")


@pytest.mark.parametrize("provider", sorted(BYOK_PROVIDER_TIERS))
def test_main_and_cheap_are_selectable_and_unique(provider: str) -> None:
    tiers = BYOK_PROVIDER_TIERS[provider]
    assert tiers.default_main in tiers.selectable
    assert tiers.cheap in tiers.selectable
    assert len(set(tiers.selectable)) == len(tiers.selectable)


@pytest.mark.parametrize("provider", [p for p in sorted(BYOK_PROVIDER_TIERS) if p != "openrouter"])
def test_every_curated_model_is_priced(provider: str) -> None:
    # OpenRouter reports its own charge per response, so it has no list price here.
    for model in BYOK_PROVIDER_TIERS[provider].selectable:
        assert price_for(provider, model, date(2026, 10, 19)) is not None, (provider, model)


def test_openrouter_has_no_separate_cheap_tier() -> None:
    tiers = BYOK_PROVIDER_TIERS["openrouter"]
    assert tiers.cheap == tiers.default_main
    assert tiers.selectable == (tiers.default_main,)


def test_helpers() -> None:
    assert provider_tiers("nope") is None
    assert provider_tiers("anthropic") is BYOK_PROVIDER_TIERS["anthropic"]
    assert is_selectable("anthropic", "claude-opus-5-5")
    assert not is_selectable("anthropic", "gpt-5.6-sol")  # another provider's model
    assert not is_selectable("nope", "claude-opus-5-5")
    assert not is_selectable("openrouter", "x-ai/anything-free-form")
