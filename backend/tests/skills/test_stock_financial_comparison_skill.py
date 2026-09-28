"""`stock-financial-comparison` — a regression guard against scope creep: this skill is
deliberately narrower than `earnings-quality` (no balance-sheet/cash-flow tools) to stay inside
one lane's step budget once every tool is called twice (once per stock)."""

from __future__ import annotations

from pathlib import Path

from skills.registry import SkillRegistry

_BUILT_INS = Path(__file__).resolve().parents[2] / "src" / "skills" / "built_ins"


def _loaded_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.load([_BUILT_INS])
    return registry


def test_stock_financial_comparison_loads_with_dual_symbol_inputs() -> None:
    registry = _loaded_registry()
    skill = registry.get("stock-financial-comparison")

    assert "stock_id_a" in skill.frontmatter.inputs
    assert "stock_id_b" in skill.frontmatter.inputs
    assert skill.frontmatter.inputs["stock_id_a"].required is True
    assert skill.frontmatter.inputs["stock_id_b"].required is True


def test_stock_financial_comparison_tool_allowlist_stays_narrow() -> None:
    """Exactly these 4 tools — no `get_financial_statement` — so a 2-stock comparison's tool
    calls (each tool called twice) stay inside `agent_max_steps_per_agent`'s per-lane budget."""
    registry = _loaded_registry()
    skill = registry.get("stock-financial-comparison")

    assert skill.frontmatter.tools == (
        "get_canonical_context",
        "get_source_health",
        "get_month_revenue",
        "get_dividend_policy_summary",
    )


def test_stock_financial_comparison_procedure_mentions_out_of_scope_disclosure() -> None:
    registry = _loaded_registry()
    skill = registry.get("stock-financial-comparison")

    assert "earnings-quality" in skill.body
    assert "尚無法提供" in skill.body
