"""Industry-chain membership as overlap groups for the candidate miner.

The mining itself is file-only; the only runtime source of industry-chain membership is the themes
table (seeded from the industry-chain roster), so the weekly job reads it here and injects it. Each
industry theme yields one group (`industry:<theme key>`) and each of its non-empty sub-industries
another (`sub:<theme key>/<sub-industry>`), holding listed (TW) symbols only.
"""

from __future__ import annotations

from collections import defaultdict

from analytics.supply_chain.theme_discovery.mining import normalize
from analytics.supply_chain.themes_io.promote import IndustryRoster
from analytics.themes.models import ThemeKind
from analytics.themes.store import ThemeStore


async def load_industry_groups(store: ThemeStore) -> dict[str, frozenset[str]]:
    groups: dict[str, frozenset[str]] = {}
    for theme in await store.list_themes(kind=ThemeKind.INDUSTRY):
        members = [
            m for m in await store.get_theme_member_details(theme_key=theme.key) if m.market == "TW"
        ]
        if not members:
            continue
        groups[f"industry:{theme.key}"] = frozenset(m.symbol for m in members)
        subs: dict[str, set[str]] = defaultdict(set)
        for m in members:
            if m.sub_industry:
                subs[m.sub_industry].add(m.symbol)
        for sub, symbols in subs.items():
            groups[f"sub:{theme.key}/{sub}"] = frozenset(symbols)
    return groups


async def load_industry_roster(store: ThemeStore) -> IndustryRoster:
    """Keys, display names and listed symbols of the industry-chain themes (for `promote`)."""
    keys: set[str] = set()
    names: set[str] = set()
    symbols: set[str] = set()
    for theme in await store.list_themes(kind=ThemeKind.INDUSTRY):
        keys.add(theme.key)
        names.update(n for n in (normalize(theme.name_zh), normalize(theme.name_en)) if n)
        symbols.update(
            m.symbol
            for m in await store.get_theme_member_details(theme_key=theme.key)
            if m.market == "TW"
        )
    return IndustryRoster(frozenset(keys), frozenset(names), frozenset(symbols))


__all__ = ["load_industry_groups", "load_industry_roster"]
