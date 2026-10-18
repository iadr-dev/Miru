"""Value types of the research pipeline: what is checked (Target), what the model said
(Judgement), and what came out (ClaimOutcome / RunSummary). Frozen and validated on construction."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from analytics.supply_chain.domain.models import (
    EXCERPT_MAX,
    Evidence,
    Grade,
    Kind,
    MarketCode,
    Status,
)

_CJK = re.compile(r"[一-鿿]")


class TargetSource(StrEnum):
    CANDIDATES = "candidates"
    GAPS = "gaps"
    STALE = "stale"


class Company(BaseModel):
    """One company the pipeline may name. `names` holds the display name first, then aliases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    market: MarketCode
    names: tuple[str, ...] = ()
    market_value: float = 0.0

    @property
    def name(self) -> str:
        return self.names[0] if self.names else self.symbol

    @property
    def key(self) -> tuple[MarketCode, str]:
        return (self.market, self.symbol)

    @property
    def zh_name(self) -> str | None:
        return next((n for n in self.names if _CJK.search(n)), None)

    @property
    def en_name(self) -> str | None:
        return next((n for n in self.names if not _CJK.search(n)), None)


class Target(BaseModel):
    """A claim to check. A gap target has no `customer`: the judge turns its pages into concrete
    claims about the supplier-side company's counterparties."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    source: TargetSource
    supplier: Company
    customer: Company | None = None
    kind: Kind = Kind.SUPPLIES
    product: str | None = None
    priority: float = 0.0

    @property
    def is_gap(self) -> bool:
        return self.customer is None


def claim_id_for(supplier: Company, customer: Company, kind: Kind) -> str:
    return f"{kind.value}:{supplier.market}-{supplier.symbol}>{customer.market}-{customer.symbol}"


def gap_id_for(company: Company) -> str:
    return f"gap:{company.market}-{company.symbol}"


class Judgement(BaseModel):
    """The model's strict-JSON verdict on one page. Never trusted on its own: see `guards`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supports: bool
    direction: Literal["supplies", "competes"] | None = None
    supplier_is_first: bool = True
    excerpt: str = ""
    current: bool = True
    anonymous_counterparty: bool = False
    as_of_year: int | None = Field(default=None, ge=1900, le=2200)


class Page(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    title: str = ""
    text: str
    hit_published: str | None = None


class OutcomeKind(StrEnum):
    VERIFIED = "verified"
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class ClaimResult(BaseModel):
    """One concrete claim with the supporting evidence the guards accepted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier: Company
    customer: Company
    kind: Kind
    status: Status
    grade: Grade
    product: str | None = None
    as_of: date | None = None
    evidence: tuple[Evidence, ...] = Field(default=(), max_length=20)


class ClaimOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    kind: OutcomeKind
    reason: str = ""
    results: tuple[ClaimResult, ...] = ()


class RunSummary(BaseModel):
    """Counts only. Safe to log, journal and print: it never carries page text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claims_tried: int = 0
    verified: int = 0
    candidates: int = 0
    rejected: int = 0
    skipped: int = 0
    searches: int = 0
    pages: int = 0
    llm_calls: int = 0
    spend_usd: float = 0.0
    stopped_by: str | None = None
    dry_run: bool = False
    rows_written: int = 0
    reasons: dict[str, int] = Field(default_factory=dict)
    outcomes: dict[str, str] = Field(default_factory=dict)

    def line(self) -> str:
        stop = self.stopped_by or "none"
        return (
            f"claims={self.claims_tried} verified={self.verified} candidates={self.candidates} "
            f"rejected={self.rejected} skipped={self.skipped} searches={self.searches} "
            f"pages={self.pages} llm_calls={self.llm_calls} spend_usd={self.spend_usd:.3f} "
            f"stopped_by={stop} {'would_write' if self.dry_run else 'written'}={self.rows_written}"
        )


__all__ = [
    "EXCERPT_MAX",
    "ClaimOutcome",
    "ClaimResult",
    "Company",
    "Judgement",
    "OutcomeKind",
    "Page",
    "RunSummary",
    "Target",
    "TargetSource",
    "claim_id_for",
    "gap_id_for",
]
