"""The BYOK model catalog: labels for every selectable id, spec parsing and validation."""

from __future__ import annotations

import pytest

from llm.byok_catalog import (
    InvalidModelSpecError,
    UnknownModelError,
    catalog_for,
    parse_spec,
    spec_of,
    validate_spec,
)
from llm.byok_profiles import BYOK_PROVIDER_TIERS


def test_every_selectable_model_has_a_friendly_label_and_is_listed_once() -> None:
    models = catalog_for(BYOK_PROVIDER_TIERS)
    expected = [spec_of(p, m) for p, t in BYOK_PROVIDER_TIERS.items() for m in t.selectable]
    assert sorted(m.spec for m in models) == sorted(expected)
    for m in models:
        bare = m.spec.partition("/")[2]
        assert m.label_zh != bare, f"{m.spec} has no zh label"
        assert m.label_en != bare, f"{m.spec} has no en label"


def test_catalog_follows_the_provider_order_and_filters() -> None:
    assert {m.provider for m in catalog_for(["openai"])} == {"openai"}
    assert catalog_for([]) == []
    assert catalog_for(["unknown"]) == []
    providers = [m.provider for m in catalog_for(["openai", "anthropic"])]
    assert providers[0] == "anthropic"


def test_specs_split_on_the_first_slash_only() -> None:
    assert parse_spec("openrouter/openrouter/auto") == ("openrouter", "openrouter/auto")
    assert validate_spec("openrouter/openrouter/auto") == ("openrouter", "openrouter/auto")


@pytest.mark.parametrize("bad", ["", "claude", "anthropic/", "/x", "nobody/model"])
def test_malformed_specs_are_invalid(bad: str) -> None:
    with pytest.raises(InvalidModelSpecError):
        parse_spec(bad)


def test_a_well_formed_spec_outside_the_catalog_is_unknown() -> None:
    with pytest.raises(UnknownModelError):
        validate_spec("anthropic/claude-opus-4-8")
