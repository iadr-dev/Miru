from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import DataClass, TimingClass


class HealthStatus(StrEnum):
    """Provider currentness/closure diagnostics (M5Q)."""

    OK = "ok"
    STALE = "stale"
    UNREACHABLE = "unreachable"
    UNAVAILABLE_TIER = "unavailable-tier"  # out-of-plan dataset (e.g. FinMind tier gating)
    # Not served at all right now for a reason retrying will not fix (a credential that is not
    # configured, or an upstream that rejected the request outright) - distinct from UNREACHABLE.
    UNAVAILABLE = "unavailable"


class SourceHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: HealthStatus
    detail: str = Field(min_length=1)


class SourceResult(BaseModel):
    """Immutable, provenance-tagged result from a source adapter.

    currentness_caveat is never empty — every output must carry
    a staleness statement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    data_class: DataClass
    timing_class: TimingClass
    fetched_at: datetime  # UTC
    currentness_caveat: str = Field(min_length=1)
    payload: dict[str, object]  # validated against the source contract before construction
    health: SourceHealth
