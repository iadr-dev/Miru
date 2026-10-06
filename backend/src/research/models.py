"""Deep-research data shapes: the budget, the model's structured outputs, and the final report.

The report is built from **claims**: a sentence plus the ids of the sources that back it. Citations
are therefore structural, not something parsed out of prose - a claim with no valid source id simply
cannot reach the reader (`research.report.build_report` drops it).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResearchBudget(BaseModel):
    """Hard ceilings for one research run. Hitting any of them ends the run with what it has."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rounds: int = Field(default=3, ge=1, le=6)
    max_searches: int = Field(default=8, ge=1, le=30)
    max_pages: int = Field(default=12, ge=1, le=40)
    max_seconds: float = Field(default=120.0, gt=0)
    results_per_search: int = Field(default=6, ge=1, le=10)
    pages_per_round: int = Field(default=5, ge=1, le=10)
    page_chars: int = Field(default=6000, ge=500, le=20000)  # per source, as shown to the model
    concurrent_fetches: int = Field(default=3, ge=1, le=6)


# ---- what the model is asked to return ----


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    sub_questions: list[str] = Field(min_length=1, max_length=5)
    queries: list[str] = Field(min_length=1, max_length=5)


class GapReview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    sufficient: bool
    follow_up_queries: list[str] = Field(default_factory=list[str], max_length=3)


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    text: str = Field(min_length=1)
    source_ids: list[int] = Field(default_factory=list[int])


class DraftSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    heading: str = Field(min_length=1)
    claims: list[Claim] = Field(default_factory=list[Claim])


class DraftReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    title: str = Field(min_length=1)
    summary: list[Claim] = Field(default_factory=list[Claim])
    sections: list[DraftSection] = Field(default_factory=list[DraftSection])
    open_questions: list[str] = Field(default_factory=list[str], max_length=5)


# ---- what the reader gets ----


class ReportClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    source_ids: tuple[int, ...]


class ReportSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    heading: str
    claims: tuple[ReportClaim, ...]


class ReportSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    title: str
    url: str
    published: str | None = None


StopReason = Literal["sufficient", "rounds", "searches", "pages", "time", "no_sources"]


class FinalReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    title: str
    as_of: date
    summary: tuple[ReportClaim, ...]
    sections: tuple[ReportSection, ...]
    open_questions: tuple[str, ...]
    sources: tuple[ReportSource, ...]  # only sources the report actually cites
    stopped_because: StopReason
    dropped_claims: int = 0  # claims removed for lacking a valid source
    rounds: int = 0
    searches: int = 0
    pages_read: int = 0

    @property
    def has_content(self) -> bool:
        return bool(self.summary or self.sections)


class ResearchFailedError(RuntimeError):
    """The run could not produce a report (the model returned unusable output, etc.). The caller
    turns this into a degraded answer rather than letting it crash the turn."""


__all__ = [
    "Claim",
    "DraftReport",
    "DraftSection",
    "FinalReport",
    "GapReview",
    "Plan",
    "ReportClaim",
    "ReportSection",
    "ReportSource",
    "ResearchBudget",
    "ResearchFailedError",
    "StopReason",
]
