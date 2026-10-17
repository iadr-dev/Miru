"""The contract of the operator's private relation files.

`<reference dir>/supply_chain/relations/*.yaml`: each file is a YAML list of rows::

    - supplier: {symbol: "1234", market: TW, name: Example Co}
      customer: {symbol: ABC, market: US, name: Example Inc}
      kind: supplies                  # supplies | competes (default supplies)
      product: widgets                # optional
      status: verified                # candidate | verified | rejected (default verified)
      origin: research                # research | seeded | manual
      revenue_pct: 12.5               # optional, 0-100
      as_of: 2025-03-31               # optional
      grade: high                     # ONLY for origin=seeded (no evidence); required there
      evidence:                       # trust and grade are computed from the url's publisher
        - {url: https://..., publisher: ..., published_on: 2025-03-31, excerpt: "...",
           checker: human}

`published_on` may be omitted (null). Rows are validated one by one; see `loader`.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from analytics.supply_chain.domain.models import (
    EXCERPT_MAX,
    Checker,
    Endpoint,
    Grade,
    Kind,
    Origin,
    Status,
)


class EvidenceFileRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    published_on: date | None = None
    excerpt: str = Field(default="", max_length=EXCERPT_MAX)
    checker: Checker = Checker.HUMAN


class RelationFileRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier: Endpoint
    customer: Endpoint
    kind: Kind = Kind.SUPPLIES
    product: str | None = None
    status: Status = Status.VERIFIED
    origin: Origin
    revenue_pct: float | None = Field(default=None, ge=0, le=100)
    as_of: date | None = None
    last_verified_at: date | None = None
    grade: Grade | None = None
    evidence: tuple[EvidenceFileRow, ...] = ()

    @model_validator(mode="after")
    def _grade_only_when_seeded(self) -> RelationFileRow:
        if self.origin is Origin.SEEDED and self.grade is None:
            raise ValueError("a seeded row must state its grade")
        if self.origin is not Origin.SEEDED and self.grade is not None:
            raise ValueError("grade is derived from evidence; only seeded rows may state it")
        return self


__all__ = ["EvidenceFileRow", "RelationFileRow"]
