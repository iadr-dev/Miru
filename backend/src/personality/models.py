"""The personality-preset schema. A preset is a curated, file-defined tone fragment + bounded
tone parameters — never free user text (a free-text persona box is a prompt-injection +
governance hole, explicitly excluded). Tone params are enumerated, not arbitrary strings.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Verbosity(StrEnum):
    TERSE = "terse"
    MEDIUM = "medium"
    DETAILED = "detailed"


class Jargon(StrEnum):
    PLAIN = "plain"
    BALANCED = "balanced"
    TECHNICAL = "technical"


class Structure(StrEnum):
    PROSE = "prose"
    BULLETS = "bullets"
    TABLES = "tables"


class Warmth(StrEnum):
    NEUTRAL = "neutral"
    ENCOURAGING = "encouraging"


class ToneParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verbosity: Verbosity = Verbosity.MEDIUM
    jargon: Jargon = Jargon.BALANCED
    structure: Structure = Structure.PROSE
    warmth: Warmth = Warmth.NEUTRAL


class PersonalityPreset(BaseModel):
    """One curated agent-voice preset. `fragment` is the governance-reviewed system-prompt text
    injected into `PromptLayers.personality` — styling only, appended below the immovable
    governance layer, and (like everything) still scanned by `guardrails.check_output`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    name_zh: str = Field(min_length=1)
    name_en: str = Field(min_length=1)
    description: str = Field(min_length=1)
    default: bool = False
    version: str = "1.0.0"
    tone: ToneParams = Field(default_factory=ToneParams)
    fragment: str = Field(min_length=1)


__all__ = [
    "Jargon",
    "PersonalityPreset",
    "Structure",
    "ToneParams",
    "Verbosity",
    "Warmth",
]
