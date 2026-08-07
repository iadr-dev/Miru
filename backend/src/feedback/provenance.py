"""A minimal scaffold: the skill+version provenance record a turn's journal entry carries, so
a future rating feature (thumbs up/down on a turn) can attribute the rating back to the
skill+version that produced it — without this module owning the actual rating storage, UI, or
quality-alert pipeline (that belongs to a dedicated feedback feature, not built here).

`SkillProvenance` is deliberately just a typed record, not a store: nothing here writes to a
table. A future rating store is that feature's own responsibility to build against this
shape — this module exists so skill-invocation code has somewhere to construct a provenance
record without reaching forward into an unbuilt feature.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SkillProvenance(BaseModel):
    """Which skill+version produced a turn's finding — attached to the journal entry for a
    skill-driven turn so an external handoff (or a future feedback rating) is reproducible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skill_name: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    turn_id: str


__all__ = ["SkillProvenance"]
