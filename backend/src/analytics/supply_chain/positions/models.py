"""Position value types. The reason codes are a stable contract with the web client."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from analytics.supply_chain.domain.models import MarketCode


class Position(StrEnum):
    UPSTREAM = "upstream"
    MIDSTREAM = "midstream"
    DOWNSTREAM = "downstream"


class Method(StrEnum):
    OVERRIDE = "override"
    EVIDENCE = "evidence"
    PRIOR = "prior"


class ReasonCode(StrEnum):
    OVERRIDE = "override"
    PRIOR_CURATED = "prior_curated"
    PRIOR_KEYWORD = "prior_keyword"
    GRAPH_LAYERING = "graph_layering"
    GRAPH_PROPAGATION = "graph_propagation"
    AGREE_PRIOR_GRAPH = "agree_prior_graph"
    CONFLICT_GRAPH_OVER_PRIOR = "conflict_graph_over_prior"
    CONFLICT_PRIOR_KEPT = "conflict_prior_kept"


class PriorSource(StrEnum):
    CURATED = "curated"
    HEURISTIC = "heuristic"


class Reason(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ReasonCode
    detail: str = ""


class ChainPosition(BaseModel):
    """One company's position in one chain. `computed_at` is stamped by the store on write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    market: MarketCode = "TW"
    chain_key: str = Field(min_length=1)
    chain_name: str = ""
    position: Position
    is_primary: bool = False
    method: Method
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: tuple[Reason, ...] = ()
    inputs_hash: str = ""
    computed_at: datetime | None = None


class Prior(BaseModel):
    """A position implied by the tier map for a (chain, sub-industry)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    position: Position
    source: PriorSource


class ChainMember(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: MarketCode = "TW"
    sub_industry: str = ""


class ChainSpec(BaseModel):
    """One industry chain: its members and the tier map (sub-industry -> prior)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chain_key: str
    chain_name: str = ""
    members: tuple[ChainMember, ...] = ()
    tiers: dict[str, Prior] = Field(default_factory=dict)


class Override(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    market: MarketCode = "TW"
    chain_key: str = Field(min_length=1)
    position: Position
    reason: str = Field(min_length=1)


__all__ = [
    "ChainMember",
    "ChainPosition",
    "ChainSpec",
    "Method",
    "Override",
    "Position",
    "Prior",
    "PriorSource",
    "Reason",
    "ReasonCode",
]
