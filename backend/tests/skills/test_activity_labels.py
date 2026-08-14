"""`skills.activity_labels.skill_activity_label` — the natural-language SSE-narration lookup
that replaced `orchestrator.py`'s literal `f"Running {skill_name}"` templating."""

from __future__ import annotations

from pathlib import Path

from skills.activity_labels import skill_activity_label

_BUILT_INS_DIR = Path(__file__).resolve().parents[2] / "src" / "skills" / "built_ins"


def _built_in_skill_names() -> list[str]:
    return sorted(p.name for p in _BUILT_INS_DIR.iterdir() if p.is_dir())


def test_every_built_in_skill_has_a_zh_tw_label() -> None:
    """Every skill directory under `skills/built_ins/` gets a bespoke zh-TW label — no skill
    silently falls through to the generic fallback phrase."""
    for name in _built_in_skill_names():
        label = skill_activity_label(name, locale="zh-TW")
        assert label != f"正在處理：{name.replace('-', ' ')}"
        assert name not in label  # never the raw skill id verbatim


def test_every_built_in_skill_has_an_en_label() -> None:
    for name in _built_in_skill_names():
        label = skill_activity_label(name, locale="en")
        assert label != f"Working on: {name.replace('-', ' ')}"
        assert name not in label


def test_known_skill_label_is_natural_language_zh_tw() -> None:
    assert skill_activity_label("chip-flow-review") == "正在檢視籌碼流向"


def test_known_skill_label_is_natural_language_en() -> None:
    assert (
        skill_activity_label("chip-flow-review", locale="en")
        == "Reviewing institutional and margin flows"
    )


def test_default_locale_is_zh_tw() -> None:
    assert skill_activity_label("catalyst-scan") == skill_activity_label(
        "catalyst-scan", locale="zh-TW"
    )


def test_unknown_locale_falls_back_to_zh_tw() -> None:
    assert skill_activity_label("catalyst-scan", locale="fr") == skill_activity_label(
        "catalyst-scan", locale="zh-TW"
    )


def test_unknown_skill_falls_back_to_a_generic_non_raw_phrase() -> None:
    label = skill_activity_label("some-future-skill", locale="zh-TW")
    assert "some-future-skill" not in label
    assert "some future skill" in label

    label_en = skill_activity_label("some-future-skill", locale="en")
    assert "some-future-skill" not in label_en
    assert "some future skill" in label_en
