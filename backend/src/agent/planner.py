"""A thin, optional pre-pass for the `single` execution mode: seeds an initial tool plan and
a step-count budget so a skill-less query doesn't wander unbounded.

This is not a second planning engine — the ReAct loop and, once skills exist, a skill body's
own prescribed tool order remain the real per-step decision makers. `Planner.plan_single`
defers to a skill-prescribed tool order when one is supplied; skills are not built yet, so the
only path exercised today is the default warm plan below, structured so a future skill-body
override is a one-argument change at the call site, not a rewrite of this module.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_STEP_BUDGET: Final[int] = 6
_DEFAULT_WARM_TOOLS: Final[tuple[str, ...]] = ("get_canonical_context", "get_source_health")


class ToolPlanStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str
    rationale: str = Field(min_length=1)


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    steps: tuple[ToolPlanStep, ...] = Field(default_factory=tuple)
    step_budget: int = Field(gt=0)


class Planner:
    """Seeds a `Plan` for the `single` framework. Zero skills active today — every call
    lands on the default warm-plan branch below."""

    def plan_single(
        self,
        *,
        immediate_tool_names: tuple[str, ...],
        skill_tool_order: tuple[str, ...] | None = None,
        step_budget: int | None = None,
    ) -> Plan:
        if skill_tool_order:
            return Plan(
                steps=tuple(
                    ToolPlanStep(tool_name=name, rationale="skill-prescribed tool order")
                    for name in skill_tool_order
                ),
                step_budget=step_budget or max(len(skill_tool_order) + 1, _DEFAULT_STEP_BUDGET),
            )

        default_order = tuple(name for name in _DEFAULT_WARM_TOOLS if name in immediate_tool_names)
        return Plan(
            steps=tuple(
                ToolPlanStep(tool_name=name, rationale="default warm plan for a skill-less query")
                for name in default_order
            ),
            step_budget=step_budget or _DEFAULT_STEP_BUDGET,
        )


__all__ = ["Plan", "Planner", "ToolPlanStep"]
