"""The BYOK model catalog: `llm/byok_profiles.py` selectable ids, with display names and the one
`provider/model` spec parser/validator shared by the API (overrides, saved default, `/models`).

A *spec* is the wire form of a model pick: `"<provider>/<model>"`, where `<model>` is the bare
provider-local id (it may itself contain `/`, e.g. `openrouter/auto`, so a spec is split on the
FIRST slash only).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from llm.byok_profiles import BYOK_PROVIDER_ORDER, BYOK_PROVIDER_TIERS

ByokProviderId = Literal["anthropic", "openai", "gemini", "openrouter"]
_PROVIDER_IDS: Final[tuple[ByokProviderId, ...]] = ("anthropic", "openai", "gemini", "openrouter")

# (zh, en) display names. Every selectable id has one (enforced by tests/llm/test_byok_catalog.py).
_LABELS: Final[dict[str, tuple[str, str]]] = {
    "anthropic/claude-sonnet-5-5": ("Claude Sonnet 5.5", "Claude Sonnet 5.5"),
    "anthropic/claude-opus-5-5": ("Claude Opus 5.5（深度）", "Claude Opus 5.5 (deep)"),
    "anthropic/claude-haiku-5-5": ("Claude Haiku 5.5（快速）", "Claude Haiku 5.5 (fast)"),
    "openai/gpt-5.6-sol": ("GPT-5.6 Sol", "GPT-5.6 Sol"),
    "openai/gpt-5.6-terra": ("GPT-5.6 Terra", "GPT-5.6 Terra"),
    "openai/gpt-5.6-luna": ("GPT-5.6 Luna（快速）", "GPT-5.6 Luna (fast)"),
    "openai/gpt-5.4-mini": ("GPT-5.4 mini（快速）", "GPT-5.4 mini (fast)"),
    "openai/gpt-4o-mini": ("GPT-4o mini（快速）", "GPT-4o mini (fast)"),
    "gemini/gemini-3.6-flash": ("Gemini 3.6 Flash", "Gemini 3.6 Flash"),
    "gemini/gemini-3.5-flash": ("Gemini 3.5 Flash", "Gemini 3.5 Flash"),
    "gemini/gemini-3.5-flash-lite": (
        "Gemini 3.5 Flash-Lite（快速）",
        "Gemini 3.5 Flash-Lite (fast)",
    ),
    "openrouter/openrouter/auto": ("OpenRouter 自動選模", "OpenRouter auto-select"),
}


@dataclass(frozen=True)
class CatalogModel:
    provider: ByokProviderId
    spec: str  # "provider/model"
    label_zh: str
    label_en: str


class InvalidModelSpecError(ValueError):
    """The pick is not a well-formed `provider/model` of a known BYOK provider."""


class UnknownModelError(ValueError):
    """The pick is well-formed and its provider is known, but the model is not in the catalog."""


def spec_of(provider: str, model: str) -> str:
    return f"{provider}/{model}"


def parse_spec(spec: str) -> tuple[str, str]:
    """Split a spec on its first slash. Raises `InvalidModelSpecError` for a malformed one or an
    unknown provider; does NOT check the model (see `validate_spec`)."""
    provider, sep, model = spec.strip().partition("/")
    if not sep or not provider or not model or provider not in BYOK_PROVIDER_TIERS:
        raise InvalidModelSpecError(spec)
    return provider, model


def validate_spec(spec: str) -> tuple[str, str]:
    """`(provider, model)` for a spec in the catalog; `InvalidModelSpecError` for a bad format or
    provider, `UnknownModelError` for a model the provider's tier does not offer."""
    provider, model = parse_spec(spec)
    if model not in BYOK_PROVIDER_TIERS[provider].selectable:
        raise UnknownModelError(spec)
    return provider, model


def catalog_for(providers: Iterable[str]) -> list[CatalogModel]:
    """Selectable models for the providers the user holds a key for, in provider order."""
    held = set(providers)
    out: list[CatalogModel] = []
    for provider in BYOK_PROVIDER_ORDER:
        if provider not in held or provider not in _PROVIDER_IDS:
            continue
        provider_id = _PROVIDER_IDS[_PROVIDER_IDS.index(provider)]
        for model in BYOK_PROVIDER_TIERS[provider].selectable:
            spec = spec_of(provider, model)
            zh, en = _LABELS.get(spec, (model, model))
            out.append(CatalogModel(provider=provider_id, spec=spec, label_zh=zh, label_en=en))
    return out


__all__ = [
    "ByokProviderId",
    "CatalogModel",
    "InvalidModelSpecError",
    "UnknownModelError",
    "catalog_for",
    "parse_spec",
    "spec_of",
    "validate_spec",
]
