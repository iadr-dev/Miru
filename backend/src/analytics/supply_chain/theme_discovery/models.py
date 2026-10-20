"""Value types of the concept-theme candidate miner (pure, frozen)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Trend(StrEnum):
    NEW = "new"
    RISING = "rising"
    STEADY = "steady"
    FALLING = "falling"


class ThemeCandidate(BaseModel):
    """One proposed concept theme. Symbols only: no company name is ever stored here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: str
    company_count: int = Field(ge=0)
    total_mentions: int = Field(ge=0)
    top_companies: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    first_seen: str
    trend: Trend
    trend_delta: int = 0

    def trend_text(self) -> str:
        if self.trend in (Trend.NEW, Trend.STEADY):
            return self.trend.value
        sign = "+" if self.trend_delta > 0 else ""
        return f"{self.trend.value} ({sign}{self.trend_delta} companies)"


class PreviousCandidate(BaseModel):
    """What the trend comparison needs from the previous run's file."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    candidate_id: str
    company_count: int = 0
    first_seen: str = ""


__all__ = ["PreviousCandidate", "ThemeCandidate", "Trend"]
