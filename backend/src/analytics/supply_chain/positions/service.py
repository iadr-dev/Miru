"""Gather the inputs (industry chains, tier map, verified supply pairs, overrides) from the stores
and resolve positions. Read-only: the caller decides whether to write the result."""

from __future__ import annotations

from collections import Counter

from analytics.supply_chain.positions.models import (
    ChainMember,
    ChainPosition,
    ChainSpec,
    Override,
    Position,
    Prior,
    PriorSource,
)
from analytics.supply_chain.positions.resolver import resolve_positions
from analytics.supply_chain.store.protocol import SupplyChainStore
from analytics.themes.models import ThemeKind, ThemeStatus
from analytics.themes.store import ThemeStore


async def load_chains(themes: ThemeStore) -> list[ChainSpec]:
    """Every active industry theme as a chain: Taiwan members plus its sub-industry tier map."""
    chains: list[ChainSpec] = []
    for theme in await themes.list_themes(kind=ThemeKind.INDUSTRY, status=ThemeStatus.ACTIVE):
        members = await themes.get_theme_member_details(theme_key=theme.key)
        tiers = await themes.get_theme_tiers(theme_key=theme.key)
        chains.append(
            ChainSpec(
                chain_key=theme.key,
                chain_name=theme.name_zh,
                members=tuple(
                    ChainMember(symbol=m.symbol, market="TW", sub_industry=m.sub_industry)
                    for m in members
                    if m.market == "TW"
                ),
                tiers={
                    t.sub_industry: Prior(
                        position=Position(t.tier.value), source=PriorSource(t.source.value)
                    )
                    for t in tiers
                },
            )
        )
    return chains


async def compute_positions(
    themes: ThemeStore, sc_store: SupplyChainStore, overrides: list[Override]
) -> tuple[list[ChainPosition], int]:
    """(positions, membership count). Zero memberships means there is nothing to classify."""
    chains = await load_chains(themes)
    memberships = sum(len(c.members) for c in chains)
    if memberships == 0:
        return [], 0
    pairs = await sc_store.list_supply_pairs()
    return resolve_positions(chains, pairs, overrides), memberships


def summarize(positions: list[ChainPosition]) -> dict[str, dict[str, int]]:
    """Counts only (per position and per method) - safe to print or journal."""
    return {
        "position": dict(Counter(p.position.value for p in positions)),
        "method": dict(Counter(p.method.value for p in positions)),
    }


__all__ = ["compute_positions", "load_chains", "summarize"]
