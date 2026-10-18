"""No registered tool may be orphaned from the internal chat agent.

`Orchestrator._tools_for` hands a turn ONLY its matched skill's `tools:` allowlist (plus, with no
skill, the warm set; the web-search toggle adds `web_search`/`fetch_url`). A registered tool that
none of those expose is unreachable in chat - MCP clients still reach it through `search_tools`.
So every tool is either in a skill allowlist, the warm set, the web-search set, or on the explicit
`CHAT_EXEMPT_TOOLS` list below (with a reason)."""

from __future__ import annotations

from unittest.mock import MagicMock

from orchestrator.composition import build_skill_registry
from orchestrator.orchestrator import _WARM_TOOLS, _WEB_SEARCH_TOOLS
from tests.tools.test_registry_and_specs import _router
from tools import build_default_tool_registry

# tool name -> why the internal chat agent deliberately does not get it. Every other registered
# tool must appear in a skill allowlist, the warm set or the web-search set.
_THEME_PENDING = (
    "temporary: the theme-lookup skill is being reworked on another branch and will list this "
    "tool; delete this entry when it does (test_no_stale_chat_exempt_entries then enforces it)"
)
CHAT_EXEMPT_TOOLS: dict[str, str] = {
    "get_themes": _THEME_PENDING,
    "get_supply_chain_edges": _THEME_PENDING,
    "get_chain_position": _THEME_PENDING,
    "get_theme_tier_heatmap": _THEME_PENDING,
}


def _registered() -> set[str]:
    # Fake the optional seams so the DB/Parquet/LLM-backed tool groups (scores, trend score,
    # summary, run_screener) register too; nothing here is ever called.
    registry = build_default_tool_registry(
        _router(),
        session_factory=MagicMock(),
        market_data_reader=MagicMock(),
        summary_llm=MagicMock(),
        parquet_store=MagicMock(),
    )
    return {t.name for t in registry.all_tools()}


def _skill_tools() -> set[str]:
    return {
        name for skill in build_skill_registry().all_skills() for name in skill.frontmatter.tools
    }


def test_every_registered_tool_is_reachable_from_chat() -> None:
    exposed = _skill_tools() | set(_WARM_TOOLS) | set(_WEB_SEARCH_TOOLS) | set(CHAT_EXEMPT_TOOLS)
    orphans = sorted(_registered() - exposed)
    assert not orphans, f"orphaned tools (add to a skill allowlist or CHAT_EXEMPT_TOOLS): {orphans}"


def test_every_skill_tool_exists_in_the_registry() -> None:
    unknown = sorted(_skill_tools() - _registered())
    assert not unknown, f"skills allowlist unregistered tools: {unknown}"


def test_no_stale_chat_exempt_entries() -> None:
    registered = _registered()
    stale = sorted(n for n in CHAT_EXEMPT_TOOLS if n not in registered)
    assert not stale, f"CHAT_EXEMPT_TOOLS names unregistered tools: {stale}"
    overlap = sorted(set(CHAT_EXEMPT_TOOLS) & (_skill_tools() | set(_WARM_TOOLS)))
    assert not overlap, f"exempt tools that are actually exposed: {overlap}"
    assert all(reason.strip() for reason in CHAT_EXEMPT_TOOLS.values())
