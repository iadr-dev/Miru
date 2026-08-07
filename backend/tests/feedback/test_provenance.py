from __future__ import annotations

from feedback.provenance import SkillProvenance


def test_skill_provenance_is_frozen_and_typed() -> None:
    provenance = SkillProvenance(skill_name="chip-flow-review", skill_version="1.0.0", turn_id="t1")
    assert provenance.skill_name == "chip-flow-review"
    assert provenance.model_config.get("frozen") is True
