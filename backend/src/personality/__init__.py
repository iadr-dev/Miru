"""Agent personality presets — the base analyst voice.

Curated, file-defined tone fragments (never free user text) that resolve to the [2]
`PromptLayers.personality` layer, styling tone only. Subordinate to the governance prompt and
to `guardrails.check_output`: a preset can never emit a signal or drop a caveat.
"""

from __future__ import annotations

from personality.models import PersonalityPreset, ToneParams
from personality.registry import (
    PersonalityRegistry,
    PersonalityValidationError,
    UnknownPersonalityError,
    default_registry,
)

__all__ = [
    "PersonalityPreset",
    "PersonalityRegistry",
    "PersonalityValidationError",
    "ToneParams",
    "UnknownPersonalityError",
    "default_registry",
]
