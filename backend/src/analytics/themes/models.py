"""Theme-domain Pydantic contracts — the immutable return/argument shapes the theme repository
speaks, kept separate from the SQLAlchemy rows in `db/models.py` so callers depend on a typed
domain object, not an ORM row."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ThemeKind(StrEnum):
    INDUSTRY = "industry"
    CONCEPT = "concept"


class ThemeTierName(StrEnum):
    UPSTREAM = "upstream"
    MIDSTREAM = "midstream"
    DOWNSTREAM = "downstream"


class ThemeTierSource(StrEnum):
    CURATED = "curated"
    HEURISTIC = "heuristic"


class ThemeStatus(StrEnum):
    ACTIVE = "active"
    PROPOSED = "proposed"
    RETIRED = "retired"


class ThemeSource(StrEnum):
    INDUSTRY_CHAIN = "industry_chain"
    CONCEPT_RESEARCH = "concept_research"
    LLM_DISCOVERY = "llm_discovery"


class EdgeType(StrEnum):
    SUPPLIES = "supplies"
    CUSTOMER = "customer"
    COMPETES = "competes"


class EdgeConfidence(StrEnum):
    """A textual trust band, not a number — it records how trustworthy an extraction's provenance
    is (a hand-seeded fact vs. a model's proposal), which a numeric score would obscure."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EdgeSource(StrEnum):
    SEED = "seed"
    LLM_EXTRACTION = "llm_extraction"


class ChangeEntity(StrEnum):
    THEME = "theme"
    MEMBERSHIP = "membership"
    TIER = "tier"
    EDGE = "edge"


class ChangeAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    REPLACE = "replace"


class Theme(BaseModel):
    """One `themes` row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    key: str
    name_zh: str
    name_en: str = ""
    kind: ThemeKind
    status: ThemeStatus = ThemeStatus.ACTIVE
    source: ThemeSource
    created_at: datetime
    updated_at: datetime


class ThemeMember(BaseModel):
    """One `theme_memberships` row, as returned by the reverse (symbol -> themes) lookup and
    accepted by the snapshot-replace write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: str
    sub_industry: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: ThemeSource


class SymbolTheme(BaseModel):
    """One row of the reverse lookup — a theme a symbol belongs to, plus that membership's
    own sub-industry/confidence for the symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_key: str
    name_zh: str
    kind: ThemeKind
    sub_industry: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ThemeTier(BaseModel):
    """One `theme_tiers` row — a sub-industry's upstream/midstream/downstream position within a
    theme, plus which producer (curated editorial map or keyword heuristic) assigned it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sub_industry: str
    tier: ThemeTierName
    source: ThemeTierSource
    updated_at: datetime


class SupplyChainEdge(BaseModel):
    """One directed supply-chain relationship between two symbols, as written by the seed or the
    in-theme edge generator and read back for provenance display."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_symbol: str
    to_symbol: str
    market: str
    edge_type: EdgeType
    confidence: EdgeConfidence
    revenue_pct: float | None = None
    source: EdgeSource
    from_name: str = ""
    to_name: str = ""


class ThemeChangeEntry(BaseModel):
    """One audit-log row correlating a create/update/replace against a theme, membership, tier, or
    edge with the run that produced it. `detail` is a short human summary, capped so the log stays
    scannable rather than becoming a payload dump."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity: ChangeEntity
    entity_ref: str
    action: ChangeAction
    source: str
    detail: str = Field(default="", max_length=200)
    run_id: str


__all__ = [
    "ChangeAction",
    "ChangeEntity",
    "EdgeConfidence",
    "EdgeSource",
    "EdgeType",
    "SupplyChainEdge",
    "SymbolTheme",
    "Theme",
    "ThemeChangeEntry",
    "ThemeKind",
    "ThemeMember",
    "ThemeSource",
    "ThemeStatus",
    "ThemeTier",
    "ThemeTierName",
    "ThemeTierSource",
]
