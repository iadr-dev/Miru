"""Skill discovery, validation, and the three-level progressive-disclosure surface.

`SkillRegistry.load(directories)` reads every `<dir>/*/SKILL.md` (YAML frontmatter between
`---` fences + a markdown body), validates the frontmatter against `skills.models.
SkillFrontmatter`, and indexes the result. `menu()` is level 1 (always in context); `get(name)`
is level 2 (loaded on trigger); `read_resource(name, filename)` is level 3 (loaded on demand,
the mechanism a future `read_skill_resource(name)` tool calls into).

Tool-allowlist enforcement (`enforce_allowlist`) is the other half of what makes a skill more
than documentation: while a skill is active, the runtime refuses any tool call outside its
`tools` list — this function is the pure predicate a caller (the agent loop's tool-dispatch
step, wired from outside `agent/` the same way the registered event/hook handlers are) checks
before dispatch.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

import structlog
import yaml
from pydantic import ValidationError

from skills.models import Skill, SkillFrontmatter, SkillMenuEntry

logger = structlog.get_logger(__name__)

_FRONTMATTER_FENCE = "---"


class SkillValidationError(ValueError):
    """Raised when a `SKILL.md` file is malformed (missing frontmatter fences, invalid YAML,
    or a frontmatter shape that fails `SkillFrontmatter` validation) — the registry refuses to
    load a bad skill rather than silently skipping it, so a typo in a manifest fails loudly at
    startup, not as a mysterious missing skill at query time."""


class UnknownSkillError(LookupError):
    pass


class ToolNotAllowedError(PermissionError):
    """Raised by `enforce_allowlist` when a tool call is attempted outside the active skill's
    `tools` list."""


def _split_frontmatter(raw_text: str, *, source: Path) -> tuple[str, str]:
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        raise SkillValidationError(f"{source}: missing opening '---' frontmatter fence")
    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == _FRONTMATTER_FENCE
        )
    except StopIteration as exc:
        raise SkillValidationError(f"{source}: missing closing '---' frontmatter fence") from exc
    frontmatter_text = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :]).strip()
    return frontmatter_text, body


def parse_skill_file(path: Path) -> Skill:
    raw_text = path.read_text(encoding="utf-8")
    frontmatter_text, body = _split_frontmatter(raw_text, source=path)
    loaded: object
    try:
        loaded = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SkillValidationError(f"{path}: frontmatter must be a YAML mapping")
    raw_frontmatter = cast("dict[str, object]", loaded)
    try:
        frontmatter = SkillFrontmatter.model_validate(raw_frontmatter)
    except ValidationError as exc:
        raise SkillValidationError(f"{path}: {exc}") from exc
    if not body:
        raise SkillValidationError(f"{path}: skill body (instructions) must not be empty")

    resource_dir = path.parent / "resources"
    return Skill(
        frontmatter=frontmatter,
        body=body,
        resource_dir=str(resource_dir) if resource_dir.is_dir() else None,
    )


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def load(self, directories: Iterable[Path]) -> None:
        for directory in directories:
            if not directory.is_dir():
                continue
            for skill_md in sorted(directory.glob("*/SKILL.md")):
                skill = parse_skill_file(skill_md)
                if skill.name in self._skills:
                    raise SkillValidationError(
                        f"{skill_md}: duplicate skill name {skill.name!r} — already loaded "
                        f"from another directory"
                    )
                self._skills[skill.name] = skill
                logger.info("skill_loaded", name=skill.name, version=skill.frontmatter.version)

    def menu(self) -> list[SkillMenuEntry]:
        """Level 1: the always-in-context name+description menu, sorted for a stable prompt."""
        return [
            SkillMenuEntry(name=skill.name, description=skill.description)
            for skill in sorted(self._skills.values(), key=lambda s: s.name)
        ]

    def get(self, name: str) -> Skill:
        """Level 2: the full skill body, loaded on trigger."""
        skill = self._skills.get(name)
        if skill is None:
            raise UnknownSkillError(name)
        return skill

    def read_resource(self, name: str, filename: str) -> str:
        """Level 3: a resource file's contents, loaded on demand only. Refuses any filename
        containing a path separator or `..` — a skill resource reference is always a bare
        filename inside that skill's own `resources/` directory, never an arbitrary path."""
        skill = self.get(name)
        if skill.resource_dir is None:
            raise UnknownSkillError(f"{name} has no resources/ directory")
        if filename not in skill.frontmatter.resources:
            raise UnknownSkillError(f"{name} does not declare resource {filename!r}")
        if "/" in filename or "\\" in filename or ".." in filename:
            raise SkillValidationError(f"invalid resource filename: {filename!r}")
        resource_path = Path(skill.resource_dir) / filename
        return resource_path.read_text(encoding="utf-8")

    def all_skills(self) -> list[Skill]:
        return list(self._skills.values())


def enforce_allowlist(skill: Skill, tool_name: str) -> None:
    """The pure predicate a tool-dispatch call site checks while `skill` is the active skill
    for a turn. Raises rather than returning a bool — a disallowed call is a hard refusal,
    the same shape as the mandatory `pre_tool` governance veto, not a soft warning."""
    if tool_name not in skill.frontmatter.tools:
        raise ToolNotAllowedError(
            f"skill {skill.name!r} may only call {skill.frontmatter.tools!r} — "
            f"{tool_name!r} is not in its allowlist"
        )


__all__ = [
    "SkillRegistry",
    "SkillValidationError",
    "ToolNotAllowedError",
    "UnknownSkillError",
    "enforce_allowlist",
    "parse_skill_file",
]
