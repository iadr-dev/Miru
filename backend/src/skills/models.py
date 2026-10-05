"""The `SKILL.md` frontmatter schema — the exact YAML shape a skill directory's manifest is
validated against on load."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DesignPattern(StrEnum):
    """A hint to the (later-phase) orchestrator about how this skill's tool calls are shaped
    — never enforced by the registry itself."""

    NONE = "none"
    TOOL_WRAPPER = "tool_wrapper"
    PIPELINE = "pipeline"
    SUBAGENT = "subagent"


class SkillInputSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    required: bool = False
    default: object | None = None


class SkillFrontmatter(BaseModel):
    """One `SKILL.md`'s parsed + validated frontmatter. `tools` is the allowlist the runtime
    enforces for the duration this skill is active (see `skills.registry.enforce_allowlist`)
    — a skill may reference other tools in prose, but the loop refuses to dispatch anything
    outside this list while the skill is active."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    triggers: tuple[str, ...] = Field(default_factory=tuple)
    design_pattern: DesignPattern = DesignPattern.NONE
    inputs: dict[str, SkillInputSpec] = Field(default_factory=dict)
    tools: tuple[str, ...] = Field(default_factory=tuple)
    output_structure: tuple[str, ...] = Field(default_factory=tuple)
    freshness_rule: str = ""
    boundaries: tuple[str, ...] = Field(default_factory=tuple)
    resources: tuple[str, ...] = Field(default_factory=tuple)
    outputs: str = "markdown"
    # Tools whose result card is NOT shown while this skill runs. The model still reads the tool's
    # data; only the card is dropped. For a report skill that fetches identity/profile data purely
    # to write about it, the raw key/value card would be clutter above the real charts.
    hide_cards: tuple[str, ...] = Field(default_factory=tuple)


class Skill(BaseModel):
    """A fully loaded skill: the validated frontmatter (level 1 + most of level 2's
    structured fields) plus the level-2 instructions body (free-form markdown, the
    tool-call-procedure prose) and the on-disk resource directory for level-3 loading."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    frontmatter: SkillFrontmatter
    body: str = Field(min_length=1)
    resource_dir: str | None = None

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def description(self) -> str:
        return self.frontmatter.description


class SkillMenuEntry(BaseModel):
    """Level-1 disclosure: the always-in-context one-liner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str


__all__ = [
    "DesignPattern",
    "Skill",
    "SkillFrontmatter",
    "SkillInputSpec",
    "SkillMenuEntry",
]
