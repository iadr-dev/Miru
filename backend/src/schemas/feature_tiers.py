"""Feature -> minimum-tier table and the pure tier-rank predicate.

Lives in the foundation layer so both the product layer (`accounts.entitlements`, the pricing
page and `/me`) and the tool-dispatch gate (`tools.base.dispatch_tool`) read ONE table: a gated
feature is one row here, enforced identically everywhere. Nothing here does I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from schemas.enums import Tier

# Ordering for min-tier comparisons - Free < Premium < Pro.
TIER_RANK: Final[dict[Tier, int]] = {Tier.FREE: 0, Tier.PREMIUM: 1, Tier.PRO: 2}

# Feature -> minimum tier. A feature absent from this table is unrestricted (available to every
# tier, incl. anonymous/Free). Premium rows backed by sponsor-class tools (`ai_summary` ->
# `get_stock_summary`, `tick_minute_k`) are enforced by the `dispatch_tool` sponsor gate, which
# answers an unentitled caller with the HTTP-200 locked teaser; only the Pro rows use
# `access_for` previews (`tests/tools/test_ai_summary_tier.py` keeps the row and gate aligned).
# Voice STT and TTS read-aloud are both deliberately NOT here - both are Free, quota-bounded
# rather than tier-gated (voice conversation mode is built on both, and is a Free-tier feature
# end to end).
FEATURE_MIN_TIER: Final[dict[str, Tier]] = {
    "live_quote_stream": Tier.PREMIUM,
    "ai_summary": Tier.PREMIUM,
    "swot": Tier.PREMIUM,
    "full_score": Tier.PREMIUM,
    "deep_research": Tier.PRO,
    # Tick and minute-K are sponsor-class data, which the tool-dispatch gate unlocks at Premium and
    # above - so this row says Premium, matching what is actually enforced.
    "tick_minute_k": Tier.PREMIUM,
    # Supply/customer/competitor relations, chain positions and the entity graph.
    "supply_chain": Tier.PRO,
    # Concept themes (themes with kind='concept'); industry themes stay free.
    "concept_themes": Tier.PRO,
}


def tier_at_least(tier: Tier, minimum: Tier) -> bool:
    return TIER_RANK[tier] >= TIER_RANK[minimum]


def feature_available(tier: Tier, feature: str) -> bool:
    """Is `feature` available at `tier`? Unlisted features are unrestricted."""
    minimum = FEATURE_MIN_TIER.get(feature)
    return minimum is None or tier_at_least(tier, minimum)


@dataclass(frozen=True, slots=True)
class Access:
    """How much of a Pro-only feature a caller gets: everything (`full`) or the preview."""

    full: bool


def access_for(feature: str, tier: Tier) -> Access:
    """The central, pure preview policy. A caller below the feature's minimum tier (Free,
    Premium, anonymous) gets the preview; the tools apply it server-side, so the REST routes, the
    agent and MCP all return the same limited data. Never a 403: the preview is a normal result."""
    return Access(full=feature_available(tier, feature))


# Preview limits for callers without full access - the only place these numbers live.
PREVIEW_PEERS_PER_ROLE = 3  # supply chain: suppliers / customers / competitors, each
PREVIEW_CHAIN_ROWS = 1  # chain positions: the primary chain only
PREVIEW_ENTITY_NODES = 8  # entity graph: the top entities by weight (no peer companies)
PREVIEW_CONCEPT_MEMBERS_PER_POSITION = 3  # concept theme members per position

# Relayed by the model / shown by clients when a preview withheld something.
UPGRADE_NOTE = (
    "This is a preview. The full view (all peers, sources, chains, graph and theme members) "
    "is part of the Pro plan."
)


__all__ = [
    "FEATURE_MIN_TIER",
    "PREVIEW_CHAIN_ROWS",
    "PREVIEW_CONCEPT_MEMBERS_PER_POSITION",
    "PREVIEW_ENTITY_NODES",
    "PREVIEW_PEERS_PER_ROLE",
    "TIER_RANK",
    "UPGRADE_NOTE",
    "Access",
    "access_for",
    "feature_available",
    "tier_at_least",
]
