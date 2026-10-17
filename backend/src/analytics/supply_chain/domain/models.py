"""Supply-chain value types: frozen, `extra="forbid"`, validated on construction."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EXCERPT_MAX = 300
type MarketCode = Literal["TW", "US"]


class Kind(StrEnum):
    SUPPLIES = "supplies"
    COMPETES = "competes"


class Status(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    STALE = "stale"
    REJECTED = "rejected"


class Grade(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Trust(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    REJECTED = "rejected"


class Origin(StrEnum):
    RESEARCH = "research"
    SEEDED = "seeded"
    MANUAL = "manual"


class Checker(StrEnum):
    RULE = "rule"
    LLM = "llm"
    HUMAN = "human"


class Endpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    market: MarketCode
    name: str = ""


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    published_on: date | None = None
    excerpt: str = Field(default="", max_length=EXCERPT_MAX)
    trust: Trust
    checker: Checker = Checker.HUMAN
    supports: bool = True

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("evidence url must start with http:// or https://")
        return value


class RelationKey(BaseModel):
    """The natural key of a relation; `product_key` is `''` when there is no product."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier_symbol: str
    supplier_market: MarketCode
    customer_symbol: str
    customer_market: MarketCode
    kind: Kind
    product_key: str = ""


class Relation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier: Endpoint
    customer: Endpoint
    kind: Kind
    product: str | None = None
    status: Status
    grade: Grade
    origin: Origin
    revenue_pct: float | None = Field(default=None, ge=0, le=100)
    as_of: date | None = None
    last_verified_at: datetime | None = None
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def _distinct_endpoints(self) -> Relation:
        if (self.supplier.symbol, self.supplier.market) == (
            self.customer.symbol,
            self.customer.market,
        ):
            raise ValueError("a relation needs two different companies")
        return self

    @property
    def key(self) -> RelationKey:
        return RelationKey(
            supplier_symbol=self.supplier.symbol,
            supplier_market=self.supplier.market,
            customer_symbol=self.customer.symbol,
            customer_market=self.customer.market,
            kind=self.kind,
            product_key=self.product or "",
        )


__all__ = [
    "EXCERPT_MAX",
    "Checker",
    "Endpoint",
    "Evidence",
    "Grade",
    "Kind",
    "MarketCode",
    "Origin",
    "Relation",
    "RelationKey",
    "Status",
    "Trust",
]
