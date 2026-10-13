"""Deep-research data shapes: the budget, the model's structured outputs, and the final report.

The report is built from **claims**: a sentence plus the ids of the sources that back it. Citations
are therefore structural, not something parsed out of prose - a claim with no valid source id simply
cannot reach the reader (`research.report.build_report` drops it).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research.datatools import DataToolName


class ResearchBudget(BaseModel):
    """Hard ceilings for one research run. Hitting any of them ends the run with what it has.

    The sub-questions run side by side and share the searches, pages and data calls, so those are
    totals for the whole run; `max_rounds` is per sub-question."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rounds: int = Field(default=3, ge=1, le=6)  # search -> read -> review cycles, each
    max_searches: int = Field(default=24, ge=1, le=60)
    max_pages: int = Field(default=30, ge=1, le=80)
    max_data_calls: int = Field(default=12, ge=0, le=40)
    max_seconds: float = Field(default=180.0, gt=0)
    results_per_search: int = Field(default=6, ge=1, le=10)
    pages_per_round: int = Field(default=4, ge=1, le=10)  # per sub-question, per round
    page_chars: int = Field(default=4000, ge=500, le=20000)  # per source, as shown to the model
    concurrent_fetches: int = Field(default=6, ge=1, le=10)


# ---- what the model is asked to return ----


class StandaloneQuestion(BaseModel):
    """The user's latest message, rewritten so it makes sense without the conversation."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    question: str = Field(min_length=1)


class DataRequest(BaseModel):
    """One lookup in Miru's own market data, from the fixed list in `research.datatools`."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    tool: DataToolName
    symbol: str = Field(pattern=r"^[A-Za-z0-9.\-]{1,12}$")
    market: Literal["TW", "US"] = "TW"


class SubQuestion(BaseModel):
    """One independent part of the question, researched on its own (alongside the others)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    question: str = Field(min_length=1)
    queries: list[str] = Field(default_factory=list[str], max_length=3)
    data: list[DataRequest] = Field(default_factory=list[DataRequest], max_length=3)

    @model_validator(mode="after")
    def _something_to_do(self) -> SubQuestion:
        if not self.queries and not self.data:
            raise ValueError("a sub-question needs at least one web query or data request")
        return self


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    sub_questions: list[SubQuestion] = Field(min_length=1, max_length=5)


class GapReview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    sufficient: bool
    follow_up_queries: list[str] = Field(default_factory=list[str], max_length=3)
    follow_up_data: list[DataRequest] = Field(default_factory=list[DataRequest], max_length=2)


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
    url: str | None  # None for Miru's own market data: there is no page to link to
    kind: Literal["web", "data"] = "web"
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
    data_calls: int = 0

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
