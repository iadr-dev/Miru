"""Regression gate for the Pro-only previews (supply chain, chain positions, entity graph,
concept-theme members): the stored data behind them is read by exactly one request-path module,
which applies the central preview policy - so no route, agent tool, MCP tool or report can return
withheld content around it."""

from __future__ import annotations

import ast
from pathlib import Path

from schemas.enums import Tier
from schemas.feature_tiers import FEATURE_MIN_TIER

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

# Modules whose contents are the Pro-gated stored data.
_GATED_STORE_MODULES = (
    "analytics.themes.store",
    "analytics.supply_chain.store",
    "analytics.supply_chain.entity_graph",
    "analytics.supply_chain.edges",
)
# Packages that write/refresh the data offline (ingest, jobs, CLIs) or define it - not request path.
_OFFLINE_PACKAGES = ("analytics", "ingest")
# The one request-path module allowed to read it, and the tools in it that apply the policy.
_READER = Path("tools/theme_tools.py")
_PREVIEW_AWARE_TOOLS = {
    "get_themes": "concept_themes",
    "get_theme_members": "concept_themes",
    "get_symbol_themes": "concept_themes",
    "get_supply_chain_edges": "supply_chain",
    "get_chain_position": "supply_chain",
    "get_entity_graph": "supply_chain",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_only_the_preview_aware_tool_module_reads_the_gated_data_on_the_request_path() -> None:
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT)
        if rel.parts[0] in _OFFLINE_PACKAGES or rel == _READER:
            continue
        for module in _imported_modules(path):
            if any(module == m or module.startswith(f"{m}.") for m in _GATED_STORE_MODULES):
                offenders.append(f"{rel.as_posix()} imports {module}")
    assert offenders == [], (
        "These modules read Pro-gated stored data without going through the preview-aware "
        f"tools in {_READER.as_posix()}: {offenders}"
    )


def test_preview_policy_rows_are_pro_only() -> None:
    assert FEATURE_MIN_TIER["supply_chain"] is Tier.PRO
    assert FEATURE_MIN_TIER["concept_themes"] is Tier.PRO


def test_every_preview_aware_tool_consults_the_policy_for_its_feature() -> None:
    """Each tool's `run` calls `access_for(<its feature constant>, ctx.tier)`."""
    source = (SRC_ROOT / _READER).read_text(encoding="utf-8")
    tree = ast.parse(source)
    constants = {"SUPPLY_CHAIN_FEATURE": "supply_chain", "CONCEPT_THEMES_FEATURE": "concept_themes"}
    for tool_name, feature in _PREVIEW_AWARE_TOOLS.items():
        cls = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and any(
                isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "name" for t in stmt.targets)
                and isinstance(stmt.value, ast.Constant)
                and stmt.value.value == tool_name
                for stmt in node.body
            )
        )
        run = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
        used = {
            constants[arg.id]
            for call in ast.walk(run)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "access_for"
            for arg in call.args
            if isinstance(arg, ast.Name) and arg.id in constants
        }
        assert feature in used, f"{tool_name}.run does not apply the {feature} preview policy"
        # the run() must read the caller's tier, not discard the context
        assert not any(isinstance(stmt, ast.Delete) for stmt in run.body), (
            f"{tool_name}.run discards its ToolContext"
        )
