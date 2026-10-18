"""Concept-theme and entity value types (pure, frozen, validated on construction).

A concept theme is a cross-industry cut (a technology wave, an application) with member
companies placed upstream/midstream/downstream or merely related. An entity is a named thing the
operator's reports mention; a mention links one listed company to one entity.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from analytics.supply_chain.domain.models import MarketCode

_TW_SYMBOL = re.compile(r"^\d{4,6}[A-Z]?$")
_US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def market_of_symbol(symbol: str) -> MarketCode | None:
    """TW for a 4-6 digit code (optional trailing letter), US for a ticker, else None."""
    if _TW_SYMBOL.match(symbol):
        return "TW"
    if _US_SYMBOL.match(symbol):
        return "US"
    return None


class ConceptPosition(StrEnum):
    UPSTREAM = "upstream"
    MIDSTREAM = "midstream"
    DOWNSTREAM = "downstream"
    RELATED = "related"


# The label stored as the mirrored membership's `sub_industry`.
POSITION_LABEL_ZH: dict[ConceptPosition, str] = {
    ConceptPosition.UPSTREAM: "上游",
    ConceptPosition.MIDSTREAM: "中游",
    ConceptPosition.DOWNSTREAM: "下游",
    ConceptPosition.RELATED: "相關公司",
}
LABEL_TO_POSITION: dict[str, ConceptPosition] = {v: k for k, v in POSITION_LABEL_ZH.items()}


class ConceptOrigin(StrEnum):
    SEEDED = "seeded"
    CURATED = "curated"
    PROPOSED = "proposed"


class ConceptMember(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    market: MarketCode
    name: str = ""
    position: ConceptPosition
    origin: ConceptOrigin


class ConceptTheme(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    name_zh: str = Field(min_length=1)
    name_en: str = ""
    description: str = ""
    related: tuple[str, ...] = ()
    members: tuple[ConceptMember, ...] = ()

    @model_validator(mode="after")
    def _unique_members(self) -> ConceptTheme:
        seen = {(m.symbol, m.market) for m in self.members}
        if len(seen) != len(self.members):
            raise ValueError(f"theme {self.key!r} lists a company twice")
        return self


class EntityType(StrEnum):
    TW_COMPANY = "tw_company"
    INTL_COMPANY = "intl_company"
    TECHNOLOGY = "technology"
    MATERIAL = "material"
    APPLICATION = "application"
    PRODUCT = "product"


class MentionSection(StrEnum):
    BUSINESS = "business"
    CHAIN = "chain"
    COUNTERPARTY = "counterparty"


class MentionRole(StrEnum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"


class Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    type: EntityType
    symbol: str | None = None
    market: MarketCode | None = None
    aliases: tuple[str, ...] = ()
    mentions: int = Field(default=0, ge=0)
    reports: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _market_follows_symbol(self) -> Entity:
        if (self.symbol is None) != (self.market is None):
            raise ValueError("symbol and market go together")
        return self


class EntityMention(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    entity_id: str = Field(min_length=1)
    section: MentionSection
    role: MentionRole | None = None
    count: int = Field(default=1, ge=0)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (
            self.symbol,
            self.entity_id,
            self.section.value,
            self.role.value if self.role else "",
        )


__all__ = [
    "LABEL_TO_POSITION",
    "POSITION_LABEL_ZH",
    "ConceptMember",
    "ConceptOrigin",
    "ConceptPosition",
    "ConceptTheme",
    "Entity",
    "EntityMention",
    "EntityType",
    "MentionRole",
    "MentionSection",
    "market_of_symbol",
]
