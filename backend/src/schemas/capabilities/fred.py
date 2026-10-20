"""US macro statistics rows: the shapes the ingest job writes to Parquet (market `US`) and the
tools read back. The upstream is a statistical-series service, so every row is keyed by a series id
rather than a symbol."""

from __future__ import annotations

import math
from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MacroObservationRow(BaseModel):
    """`us_macro_observations` - one observation of one series under one units transformation.

    `realtime_start`/`realtime_end` is the period during which this value was the published one
    (a revised observation gets a new row in a later snapshot), so revisions are never silently
    overwritten. Observations the upstream marks missing are not stored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    units: str  # a `MacroUnits` code
    observation_date: date_
    value: float
    realtime_start: date_
    realtime_end: date_


class MacroSeriesMetaRow(BaseModel):
    """`us_macro_series_meta` - one series' descriptive metadata plus what the job knows about its
    stored copy. `last_updated` is kept as the upstream's own timestamp text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    title: str
    units: str
    units_short: str
    frequency: str
    frequency_short: str
    seasonal_adjustment: str
    seasonal_adjustment_short: str
    last_updated: str
    observation_start: date_ | None = None
    observation_end: date_ | None = None
    popularity: int | None = None
    notes: str | None = None
    release_id: int | None = None
    release_name: str | None = None
    release_link: str | None = None
    # How many observations of the series' default transform are in the stored window.
    stored_observations: int = Field(ge=0)
    # The day the stored window was last pulled in full (the weekly refresh re-reads revisions
    # that fall outside the short incremental window).
    full_refresh_date: date_
    latest_vintage_date: date_ | None = None

    @field_validator("popularity", "release_id", mode="before")
    @classmethod
    def _missing_int(cls, value: object) -> object:
        """A nullable integer column comes back from Parquet as a float with NaN for "none"."""
        if isinstance(value, float):
            return None if math.isnan(value) else int(value)
        return value


class MacroReleaseDateRow(BaseModel):
    """`us_macro_release_dates` - one scheduled or past release date of one release. The release
    name is the stable key a client merges against other calendars by."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    release_id: int
    release_name: str
    release_date: date_


class MacroVintageRow(BaseModel):
    """`us_macro_vintages` - one value an observation held during one real-time period (levels
    only). The earliest `realtime_start` of an observation is its first release; the latest is its
    current vintage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    observation_date: date_
    realtime_start: date_
    realtime_end: date_
    value: float


__all__ = [
    "MacroObservationRow",
    "MacroReleaseDateRow",
    "MacroSeriesMetaRow",
    "MacroVintageRow",
]
