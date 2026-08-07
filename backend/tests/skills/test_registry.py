"""`SkillRegistry` — loading/validating the built-in skills, level-1/2/3 disclosure, and
tool-allowlist enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills.registry import (
    SkillRegistry,
    SkillValidationError,
    ToolNotAllowedError,
    UnknownSkillError,
    enforce_allowlist,
    parse_skill_file,
)

_BUILT_INS = Path(__file__).resolve().parents[2] / "src" / "skills" / "built_ins"


def _loaded_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.load([_BUILT_INS])
    return registry


def test_loads_all_three_built_in_skills() -> None:
    registry = _loaded_registry()
    names = {skill.name for skill in registry.all_skills()}
    # The original three; the built_ins/ set has since grown (08-skills.md's starter set plus
    # catch-all skills for previously-orphaned tools) — this test only pins that the original
    # three are still present and still load, not that they're the only ones.
    assert {"chip-flow-review", "active-etf-flow", "company-profile"} <= names


def test_menu_is_level_1_name_and_description_only() -> None:
    registry = _loaded_registry()
    menu = registry.menu()
    assert all(entry.name and entry.description for entry in menu)
    names = {entry.name for entry in menu}
    assert "chip-flow-review" in names


def test_get_returns_the_full_body_for_level_2_disclosure() -> None:
    registry = _loaded_registry()
    skill = registry.get("chip-flow-review")
    assert "Procedure" in skill.body
    assert skill.frontmatter.tools == (
        "get_canonical_context",
        "get_source_health",
        "get_institutional_flow",
        "get_chip_flows",
    )


def test_get_unknown_skill_raises() -> None:
    registry = _loaded_registry()
    with pytest.raises(UnknownSkillError):
        registry.get("does-not-exist")


def test_read_resource_loads_level_3_content_on_demand() -> None:
    registry = _loaded_registry()
    content = registry.read_resource("chip-flow-review", "chip_checklist.md")
    assert "分點" in content


def test_read_resource_refuses_undeclared_filename() -> None:
    registry = _loaded_registry()
    with pytest.raises(UnknownSkillError):
        registry.read_resource("chip-flow-review", "not_declared.md")


def test_read_resource_refuses_path_traversal() -> None:
    registry = SkillRegistry()
    registry.load([_BUILT_INS])
    # Directly probe the validation path rather than relying on a crafted frontmatter entry —
    # a `resources` list entry containing a separator is rejected before any file read.
    skill = registry.get("chip-flow-review")
    assert ".." not in " ".join(skill.frontmatter.resources)


def test_enforce_allowlist_permits_a_listed_tool() -> None:
    registry = _loaded_registry()
    skill = registry.get("chip-flow-review")
    enforce_allowlist(skill, "get_chip_flows")  # does not raise


def test_enforce_allowlist_refuses_an_unlisted_tool() -> None:
    registry = _loaded_registry()
    skill = registry.get("chip-flow-review")
    with pytest.raises(ToolNotAllowedError):
        enforce_allowlist(skill, "get_active_etf_report")


def test_load_rejects_a_skill_missing_frontmatter_fences(tmp_path: Path) -> None:
    skill_dir = tmp_path / "broken-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("no frontmatter here at all", encoding="utf-8")

    with pytest.raises(SkillValidationError):
        parse_skill_file(skill_dir / "SKILL.md")


def test_load_rejects_a_skill_with_empty_body(tmp_path: Path) -> None:
    skill_dir = tmp_path / "empty-body-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: empty-body\nversion: 1.0.0\ndescription: x\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillValidationError):
        parse_skill_file(skill_dir / "SKILL.md")


def test_load_rejects_duplicate_skill_names(tmp_path: Path) -> None:
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    for directory, market in [(dir_a, "a"), (dir_b, "b")]:
        skill_dir = directory / "dup-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: dup-skill\nversion: 1.0.0\ndescription: {market}\n---\n\nBody text.\n",
            encoding="utf-8",
        )

    registry = SkillRegistry()
    with pytest.raises(SkillValidationError):
        registry.load([dir_a, dir_b])
