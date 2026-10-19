"""Curated model tiers for bring-your-own-key (BYOK) users - the single source of truth.

A BYOK user runs on their own provider key, so the platform role chains (`llm/profiles.py`) do not
apply. Instead each provider has one entry here:

- `default_main`: the model for the reply agent, sub-agent lanes and final synthesis when the user
  has not picked one;
- `cheap`: the model for the cost/latency-tiered plumbing (intent classifier, thread titles,
  branch summaries). It must be a model the same key can call;
- `selectable`: the model ids a BYOK user may choose as their main model (bare provider-local ids,
  no `provider/` prefix; `default_main` and `cheap` are always members).

Every id is priced in `llm/pricing.py` (enforced by `tests/llm/test_byok_profiles.py`), so usage
for a curated model is never unpriced.

OpenRouter is a router rather than a model family: it has no known cheap tier here, so
`cheap == default_main == "openrouter/auto"` and the only selectable id is that one. Free-form
OpenRouter model ids are deliberately not accepted until the catalog can validate them.

Provider order is deterministic (`BYOK_PROVIDER_ORDER`): when a user has keys for several
providers and picks no model, the first provider in this order that they hold a key for wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class ByokProviderTiers:
    default_main: str
    cheap: str
    selectable: tuple[str, ...]


BYOK_PROVIDER_ORDER: Final[tuple[str, ...]] = ("anthropic", "openai", "gemini", "openrouter")

BYOK_PROVIDER_TIERS: Final[dict[str, ByokProviderTiers]] = {
    "anthropic": ByokProviderTiers(
        default_main="claude-sonnet-5-5",
        cheap="claude-haiku-5-5",
        selectable=("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"),
    ),
    "openai": ByokProviderTiers(
        default_main="gpt-5.6-sol",
        cheap="gpt-4o-mini",
        selectable=("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4-mini", "gpt-4o-mini"),
    ),
    "gemini": ByokProviderTiers(
        default_main="gemini-3.5-flash",
        cheap="gemini-3.5-flash-lite",
        selectable=("gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"),
    ),
    "openrouter": ByokProviderTiers(
        default_main="openrouter/auto",
        cheap="openrouter/auto",
        selectable=("openrouter/auto",),
    ),
}


def provider_tiers(provider: str) -> ByokProviderTiers | None:
    """The tier entry for `provider`, or None when it is not a BYOK provider."""
    return BYOK_PROVIDER_TIERS.get(provider)


def is_selectable(provider: str, model: str) -> bool:
    """Whether `model` (a bare provider-local id) is one a BYOK user may pick for `provider`."""
    tiers = BYOK_PROVIDER_TIERS.get(provider)
    return tiers is not None and model in tiers.selectable


__all__ = [
    "BYOK_PROVIDER_ORDER",
    "BYOK_PROVIDER_TIERS",
    "ByokProviderTiers",
    "is_selectable",
    "provider_tiers",
]
