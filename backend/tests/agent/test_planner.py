"""`Planner.plan_single` — the default warm-plan seam for a skill-less query, and the
skill-prescribed-tool-order override path (exercised structurally; no skill bodies exist yet)."""

from __future__ import annotations

from agent.planner import Planner


def test_plan_single_default_warm_plan_filters_to_available_tools() -> None:
    planner = Planner()

    plan = planner.plan_single(immediate_tool_names=("get_canonical_context", "get_price_quote"))

    assert [step.tool_name for step in plan.steps] == ["get_canonical_context"]
    assert plan.step_budget == 6


def test_plan_single_default_warm_plan_is_empty_when_no_warm_tools_available() -> None:
    planner = Planner()

    plan = planner.plan_single(immediate_tool_names=("get_price_quote",))

    assert plan.steps == ()
    assert plan.step_budget == 6


def test_plan_single_respects_explicit_step_budget_override() -> None:
    planner = Planner()

    plan = planner.plan_single(immediate_tool_names=(), step_budget=2)

    assert plan.step_budget == 2


def test_plan_single_defers_to_skill_prescribed_tool_order_when_supplied() -> None:
    planner = Planner()

    plan = planner.plan_single(
        immediate_tool_names=("get_price_quote", "get_dividend_schedule"),
        skill_tool_order=("get_price_quote", "get_dividend_schedule"),
    )

    assert [step.tool_name for step in plan.steps] == ["get_price_quote", "get_dividend_schedule"]
    assert all(step.rationale == "skill-prescribed tool order" for step in plan.steps)
    assert plan.step_budget == 6  # max(len(order) + 1, default) = max(3, 6)


def test_plan_single_skill_tool_order_grows_budget_beyond_default_when_long() -> None:
    planner = Planner()
    long_order = tuple(f"tool_{i}" for i in range(10))

    plan = planner.plan_single(immediate_tool_names=(), skill_tool_order=long_order)

    assert plan.step_budget == len(long_order) + 1
