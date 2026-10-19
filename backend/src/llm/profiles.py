"""Platform model profiles - which models the *server* uses when a user brings no key of their own.

A profile decides the default fallback chain of each `ModelRole`:

- `hosted` (default): the hosted-provider chains below (Anthropic / OpenAI keys on the server).
- `local`: every role runs on one local model (`ollama/<LOCAL_MODEL_ID>`, served by Ollama or
  Docker Model Runner at `OLLAMA_BASE_URL`). If that endpoint is down the turn fails plainly; the
  hosted chain is appended as a fallback only when `LOCAL_FALLBACK_TO_HOSTED` is set, so a dead
  GPU never silently turns into hosted spend.

`MODEL_ROLE_<ROLE>` env overrides win over either profile (the operator's escape hatch).
BYOK is not handled here: a user's own key bypasses the platform profile entirely.

This module owns `ModelRole` and the hosted defaults so `registry.py` can import them without a
cycle; `registry.py` re-exports `ModelRole`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from config.settings import PlatformProfile

if TYPE_CHECKING:
    from config.settings import Settings


class ModelRole(StrEnum):
    """Cost/latency-tiered roles the orchestrator (Phase 8) names instead of a model."""

    CLASSIFIER = "classifier"
    AGENT = "agent"
    SUBAGENT = "subagent"
    SYNTHESIS = "synthesis"


_ANTHROPIC_SONNET_FALLBACK: Final[str] = "anthropic/claude-sonnet-5-5"
_ANTHROPIC_HAIKU: Final[str] = "anthropic/claude-haiku-5-5"

# The `hosted` profile: each role an ordered fallback chain of "provider/model" specs - the first
# entry is tried first; on a `chat()`/`stream()` failure `FallbackLLMClient` tries the next.
HOSTED_ROLE_MODELS: Final[dict[ModelRole, tuple[str, ...]]] = {
    ModelRole.CLASSIFIER: (_ANTHROPIC_HAIKU, _ANTHROPIC_SONNET_FALLBACK),
    ModelRole.AGENT: (_ANTHROPIC_SONNET_FALLBACK, _ANTHROPIC_HAIKU),
    ModelRole.SUBAGENT: (_ANTHROPIC_SONNET_FALLBACK,),
    ModelRole.SYNTHESIS: ("openai/gpt-4o-mini", _ANTHROPIC_SONNET_FALLBACK),
}


def parse_role_chain(spec: str) -> tuple[str, ...]:
    """A role override is one or more comma-separated `provider/model` specs - the fallback
    chain, in try-order. Whitespace around each entry is stripped so `"a/b, c/d"` works."""
    return tuple(part.strip() for part in spec.split(",") if part.strip())


def _profile_chains(settings: Settings) -> dict[ModelRole, tuple[str, ...]]:
    if settings.platform_model_profile == "hosted":
        return dict(HOSTED_ROLE_MODELS)
    local = f"ollama/{settings.local_model_id}"
    return {
        role: (local, *HOSTED_ROLE_MODELS[role]) if settings.local_fallback_to_hosted else (local,)
        for role in ModelRole
    }


def platform_role_chains(settings: Settings) -> dict[ModelRole, tuple[str, ...]]:
    """The server's fallback chain per role: the active profile's, with any `MODEL_ROLE_*`
    override replacing that role's chain."""
    overrides: dict[ModelRole, str | None] = {
        ModelRole.CLASSIFIER: settings.model_role_classifier,
        ModelRole.AGENT: settings.model_role_agent,
        ModelRole.SUBAGENT: settings.model_role_subagent,
        ModelRole.SYNTHESIS: settings.model_role_synthesis,
    }
    chains = _profile_chains(settings)
    for role, spec in overrides.items():
        if spec:
            chains[role] = parse_role_chain(spec)
    return chains


__all__ = [
    "HOSTED_ROLE_MODELS",
    "ModelRole",
    "PlatformProfile",
    "parse_role_chain",
    "platform_role_chains",
]
