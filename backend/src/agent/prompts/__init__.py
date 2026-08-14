"""System-prompt assembly in fixed precedence: governance -> personality -> skill body ->
pet hint. Lower layers may only style tone/verbosity; they can never override or remove a
higher one, and every assembled prompt is still subject to `agent.guardrails.check_output` on
every final answer regardless of which layers are populated.

Only the governance layer is built for real this phase. `personality`/`skill_body`/`pet_hint`
are typed seams — `None` means "no content for this layer today", not "skip the governance
layer" (that layer is always first and always present). A later phase fills these in by
constructing a `PromptLayers` with the relevant field set; `assemble_system_prompt` does not
need to change.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.prompts.governance import governance_prompt


@dataclass(frozen=True)
class PromptLayers:
    """The fixed-precedence prompt stack."""

    governance: str
    personality: str | None = None
    skill_body: str | None = None
    pet_hint: str | None = None


def default_prompt_layers(locale: str | None = None) -> PromptLayers:
    """The layer stack with only the (mandatory) governance layer populated — today's default
    until personality/skill/pet layers exist. `locale` (e.g. `"zh-TW"`/`"en"`) is interpolated
    into the governance layer's language-matching rule; omitted, `governance_prompt()`'s own
    default (`zh-TW`, the app's default interface locale) applies."""
    return PromptLayers(governance=governance_prompt(locale) if locale else governance_prompt())


def assemble_system_prompt(layers: PromptLayers | None = None) -> str:
    """Render the layer stack into one system-prompt string, governance first, in fixed
    order, skipping any layer that isn't populated."""
    layers = layers or default_prompt_layers()
    parts = [layers.governance]
    for optional_layer in (layers.personality, layers.skill_body, layers.pet_hint):
        if optional_layer:
            parts.append(optional_layer)
    return "\n\n".join(parts)


__all__ = ["PromptLayers", "assemble_system_prompt", "default_prompt_layers"]
