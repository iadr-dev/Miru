"""US macro statistics tools: `get_us_macro_series`, `get_us_macro_dashboard`,
`get_us_economic_releases` and `get_series_revisions`.

All four read the Parquet store the nightly ingest job fills (`ingest/jobs/fred.py`, layout in
`ingest/fred_store.py`); none fetches anything, so they are cheap, never rate-limited by the
upstream and answer from the last successful run. When the store is empty (no API key configured,
or the job has not run yet) every tool degrades to `data_available=False` with a calm reason
instead of an error.

Public data: free for every caller, anonymous included. Output is descriptive context with the
standard currentness caveat; wording is neutral ("a higher value means ...") with no judgment of
good or bad and no trading language.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date as date_
from datetime import datetime, timedelta
from statistics import fmean
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.settings import get_settings
from ingest.fred_store import (
    ObservationPoint,
    read_meta,
    read_observations,
    read_release_dates,
    read_vintages,
)
from ingest.store import ParquetStore
from schemas.capabilities import MacroReleaseDateRow, MacroSeriesMetaRow, MacroVintageRow
from schemas.enums import DataClass
from schemas.fred_catalog import (
    CATALOG_BY_ID,
    GROUP_LABELS,
    HIGHER_MEANS_TEXT,
    RECESSION_SERIES_ID,
    SERIES_CATALOG,
    US_MACRO_CAVEAT,
    YIELD_CURVE_SPREAD_IDS,
    MacroGroup,
    MacroSeriesId,
    MacroSeriesSpec,
    MacroUnits,
    RevisionSeriesId,
    SeriesFrequency,
    UnitKind,
)
from tools.base import SyncStoreTool, Tool, ToolContext, parse_args

_NEW_YORK = ZoneInfo("America/New_York")

SPARKLINE_POINTS = 60
DEFAULT_RELEASE_PAST_DAYS = 14
DEFAULT_RELEASE_FUTURE_DAYS = 30
MAX_RELEASE_RANGE_DAYS = 366
MAX_SERIES_POINTS = 5000

_NOT_LOADED = (
    "US macro data has not been loaded yet. It is refreshed once a day, so please check back later."
)
_RECESSION_NOTE = (
    "Recession dates are set retroactively by an official dating committee, often many months "
    "after a turning point, so the most recent months are not yet classified."
)
_FIRST_RELEASE_NOTE = (
    "The first release is the earliest value in the stored revision history; an observation that "
    "was first published before that history begins shows its earliest archived value instead."
)

type Frequency = Literal["d", "w", "m", "q", "a"]
_FREQUENCY_RANK: dict[str, int] = {"d": 0, "w": 1, "m": 2, "q": 3, "a": 4}
_NATIVE_RANK: dict[SeriesFrequency, int] = {
    SeriesFrequency.DAILY: 0,
    SeriesFrequency.WEEKLY: 1,
    SeriesFrequency.MONTHLY: 2,
    SeriesFrequency.QUARTERLY: 3,
}


def _new_york_today() -> date_:
    return datetime.now(_NEW_YORK).date()


class MacroPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date_
    value: float


class _ToolOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(default=US_MACRO_CAVEAT, min_length=1)
    data_available: bool = True
    unavailable_reason: str | None = None
    # The date the stored data was last checked by the daily refresh.
    as_of: date_ | None = None


class _NoArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- shared helpers ---------------------------------------------------------------------------


def _next_release(
    releases: list[MacroReleaseDateRow], release_id: int | None, today: date_
) -> date_ | None:
    if release_id is None:
        return None
    upcoming = [
        r.release_date for r in releases if r.release_id == release_id and r.release_date >= today
    ]
    return min(upcoming) if upcoming else None


def _latest_prior_change(
    points: list[ObservationPoint],
) -> tuple[ObservationPoint | None, ObservationPoint | None, float | None]:
    if not points:
        return None, None, None
    latest = points[-1]
    prior = points[-2] if len(points) > 1 else None
    change = round(latest.value - prior.value, 10) if prior is not None else None
    return latest, prior, change


def _as_point(point: ObservationPoint | None) -> MacroPoint | None:
    return MacroPoint(date=point.observation_date, value=point.value) if point else None


# --- get_us_macro_series ----------------------------------------------------------------------


class SeriesMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    label: str
    group: MacroGroup
    group_label: str
    title: str | None = None
    # The units code the points are in, and the long description of the series' native units.
    units: MacroUnits
    units_description: str | None = None
    unit_kind: UnitKind
    default_units: MacroUnits
    available_units: tuple[MacroUnits, ...]
    frequency: str
    seasonal_adjustment: str | None = None
    last_updated: str | None = None
    notes: str | None = None
    higher_means: str
    higher_means_text: str
    copyrighted_third_party: bool = False
    release_name: str | None = None
    next_release_date: date_ | None = None


class ObservationRange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: date_
    end: date_


class GetUsMacroSeriesInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: MacroSeriesId = Field(description="A series from the curated US macro catalog.")
    units: MacroUnits | None = Field(
        default=None,
        description="Transformation of the stored values (lin level, chg change, pch percent "
        "change, pc1 percent change from a year ago, pca annualized rate). Omitted uses the "
        "series' default; only the transformations the series stores are available.",
    )
    frequency: Frequency | None = Field(
        default=None,
        description="Average the points down to a coarser frequency (w, m, q, a). Only for "
        "units=lin and only coarser than the series' own frequency.",
    )
    start_date: date_ | None = None
    end_date: date_ | None = None
    limit: int = Field(
        default=500,
        ge=1,
        le=MAX_SERIES_POINTS,
        description="Maximum points returned: the most recent ones in the requested range.",
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        spec = CATALOG_BY_ID[self.series_id]
        if self.units is not None and self.units not in spec.stored_units:
            allowed = ", ".join(u.value for u in spec.stored_units)
            raise ValueError(f"{self.series_id} is available in units: {allowed}")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date is after end_date")
        if self.frequency is not None:
            if (self.units or spec.default_units) is not MacroUnits.LEVEL:
                raise ValueError("frequency conversion is only available with units=lin")
            if _FREQUENCY_RANK[self.frequency] <= _NATIVE_RANK[spec.frequency]:
                raise ValueError(
                    f"{self.series_id} is {spec.frequency.value}; frequency must be coarser"
                )
        return self


class GetUsMacroSeriesOutput(_ToolOutput):
    series_id: str
    units: MacroUnits | None = None
    frequency: str | None = None
    metadata: SeriesMetadata | None = None
    points: tuple[MacroPoint, ...] = Field(default_factory=tuple)
    # Points available for this series/units in the requested range, before `limit` applies.
    total_observations: int = 0
    observation_range: ObservationRange | None = None


def _period_start(day: date_, frequency: str) -> date_:
    if frequency == "w":
        return day - timedelta(days=day.weekday())
    if frequency == "m":
        return day.replace(day=1)
    if frequency == "q":
        return day.replace(month=3 * ((day.month - 1) // 3) + 1, day=1)
    return day.replace(month=1, day=1)


def _aggregate(points: list[ObservationPoint], frequency: str) -> list[ObservationPoint]:
    buckets: dict[date_, list[float]] = {}
    for point in points:
        buckets.setdefault(_period_start(point.observation_date, frequency), []).append(point.value)
    return [ObservationPoint(day, fmean(values)) for day, values in sorted(buckets.items())]


def _series_metadata(
    spec: MacroSeriesSpec,
    units: MacroUnits,
    meta: MacroSeriesMetaRow | None,
    releases: list[MacroReleaseDateRow],
    today: date_,
) -> SeriesMetadata:
    return SeriesMetadata(
        series_id=spec.series_id,
        label=spec.label,
        group=spec.group,
        group_label=GROUP_LABELS[spec.group],
        title=meta.title if meta else None,
        units=units,
        units_description=(meta.units or None) if meta else None,
        unit_kind=spec.kind_for(units),
        default_units=spec.default_units,
        available_units=spec.stored_units,
        frequency=(meta.frequency if meta and meta.frequency else spec.frequency.value),
        seasonal_adjustment=(meta.seasonal_adjustment or None) if meta else None,
        last_updated=(meta.last_updated or None) if meta else None,
        notes=meta.notes if meta else None,
        higher_means=spec.higher_means,
        higher_means_text=HIGHER_MEANS_TEXT[spec.higher_means],
        copyrighted_third_party=spec.copyrighted_third_party,
        release_name=meta.release_name if meta else None,
        next_release_date=_next_release(releases, meta.release_id if meta else None, today),
    )


class GetUsMacroSeriesTool(SyncStoreTool):
    """`get_us_macro_series` - one catalog series' history plus its metadata, from the store."""

    name = "get_us_macro_series"
    description = (
        "Get the history of one US macroeconomic series (Treasury yields and spreads, inflation, "
        "unemployment and payrolls, jobless claims, financial conditions, policy rates and the "
        "Fed balance sheet, GDP and activity, housing, the dollar, money supply) with its "
        "metadata: units, frequency, seasonal adjustment, last update and next release date. "
        "Optionally pick a transformation (level, change, percent change) or average to a "
        "coarser frequency."
    )
    input_model: type[BaseModel] = GetUsMacroSeriesInput
    output_model: type[BaseModel] = GetUsMacroSeriesOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = None

    def __init__(self, store: ParquetStore, today: Callable[[], date_] = _new_york_today) -> None:
        self._store = store
        self._today = today

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # public data; nothing here depends on the caller
        typed = parse_args(GetUsMacroSeriesInput, args)
        spec = CATALOG_BY_ID[typed.series_id]
        units = typed.units or spec.default_units
        as_of, metas = read_meta(self._store)
        observations = read_observations(self._store, [(typed.series_id, units.value)])
        stored = observations.get((typed.series_id, units.value), [])
        if not stored:
            return GetUsMacroSeriesOutput(
                series_id=typed.series_id,
                units=units,
                data_available=False,
                unavailable_reason=_NOT_LOADED
                if as_of is None
                else (f"No stored observations for {typed.series_id} in this transformation yet."),
                as_of=as_of,
            )
        points = [
            p
            for p in stored
            if (typed.start_date is None or p.observation_date >= typed.start_date)
            and (typed.end_date is None or p.observation_date <= typed.end_date)
        ]
        if typed.frequency is not None:
            points = _aggregate(points, typed.frequency)
        total = len(points)
        shown = points[-typed.limit :]
        releases = read_release_dates(self._store)
        return GetUsMacroSeriesOutput(
            series_id=typed.series_id,
            units=units,
            frequency=typed.frequency or spec.frequency.value,
            metadata=_series_metadata(
                spec, units, metas.get(typed.series_id), releases, self._today()
            ),
            points=tuple(MacroPoint(date=p.observation_date, value=p.value) for p in shown),
            total_observations=total,
            observation_range=(
                ObservationRange(start=shown[0].observation_date, end=shown[-1].observation_date)
                if shown
                else None
            ),
            as_of=as_of,
            data_available=bool(shown),
            unavailable_reason=None if shown else "No observations fall in the requested range.",
        )


# --- get_us_macro_dashboard -------------------------------------------------------------------


class DashboardSeries(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    label: str
    title: str | None = None
    units: MacroUnits
    unit_kind: UnitKind
    frequency: str
    higher_means: str
    higher_means_text: str
    copyrighted_third_party: bool = False
    latest: MacroPoint | None = None
    prior: MacroPoint | None = None
    # latest minus prior, in the series' own units (percentage points for a percent series).
    change: float | None = None
    last_observation_date: date_ | None = None
    release_name: str | None = None
    next_release_date: date_ | None = None
    sparkline: tuple[MacroPoint, ...] = Field(default_factory=tuple)


class DashboardPanel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group: MacroGroup
    label: str
    series: tuple[DashboardSeries, ...]


class YieldCurveSpread(BaseModel):
    """Whether a Treasury spread (long yield minus short yield) is below zero ("inverted")."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    label: str
    latest: MacroPoint
    state: Literal["inverted", "flat", "positive"]
    # While inverted: when the current inverted stretch began (within the stored window) and how
    # many calendar days it has lasted. `since_window_start` is true when the stretch reaches back
    # to the first stored observation, so the true start may be earlier.
    inverted_since: date_ | None = None
    days_inverted: int = 0
    since_window_start: bool = False
    last_inverted_date: date_ | None = None


class RecessionBand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: date_
    end: date_
    ongoing: bool = False


class GetUsMacroDashboardOutput(_ToolOutput):
    panels: tuple[DashboardPanel, ...] = Field(default_factory=tuple)
    yield_curve: tuple[YieldCurveSpread, ...] = Field(default_factory=tuple)
    recession_bands: tuple[RecessionBand, ...] = Field(default_factory=tuple)
    recession_note: str = _RECESSION_NOTE


def _yield_curve_state(
    spec: MacroSeriesSpec, points: list[ObservationPoint]
) -> YieldCurveSpread | None:
    if not points:
        return None
    latest = points[-1]
    if latest.value < 0:
        start_index = len(points) - 1
        while start_index > 0 and points[start_index - 1].value < 0:
            start_index -= 1
        since = points[start_index].observation_date
        return YieldCurveSpread(
            series_id=spec.series_id,
            label=spec.label,
            latest=MacroPoint(date=latest.observation_date, value=latest.value),
            state="inverted",
            inverted_since=since,
            days_inverted=(latest.observation_date - since).days + 1,
            since_window_start=start_index == 0,
            last_inverted_date=latest.observation_date,
        )
    last_negative = next((p.observation_date for p in reversed(points) if p.value < 0), None)
    return YieldCurveSpread(
        series_id=spec.series_id,
        label=spec.label,
        latest=MacroPoint(date=latest.observation_date, value=latest.value),
        state="flat" if latest.value == 0 else "positive",
        last_inverted_date=last_negative,
    )


def _recession_bands(points: list[ObservationPoint]) -> list[RecessionBand]:
    bands: list[RecessionBand] = []
    start: date_ | None = None
    last_flagged: date_ | None = None
    for point in points:
        if point.value >= 0.5:  # noqa: PLR2004 - a 0/1 flag
            start = start or point.observation_date
            last_flagged = point.observation_date
        elif start is not None and last_flagged is not None:
            bands.append(RecessionBand(start=start, end=_month_end(last_flagged)))
            start = last_flagged = None
    if start is not None and last_flagged is not None:
        bands.append(RecessionBand(start=start, end=_month_end(last_flagged), ongoing=True))
    return bands


def _month_end(month_start: date_) -> date_:
    following = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


class GetUsMacroDashboardTool(SyncStoreTool):
    """`get_us_macro_dashboard` - every catalog series grouped into panels with latest/prior/change,
    a sparkline, the next release date, the yield-curve inversion state and recession bands."""

    name = "get_us_macro_dashboard"
    description = (
        "Get the US macro dashboard: grouped panels (yield curve and recession, inflation, labor, "
        "credit and financial conditions, policy rates and liquidity, growth, housing, dollar, "
        "money supply) with each series' latest value, prior value, change, last observation date, "
        "next release date and a short sparkline, plus the Treasury-spread inversion state and "
        "officially dated recession periods."
    )
    input_model: type[BaseModel] = _NoArgs
    output_model: type[BaseModel] = GetUsMacroDashboardOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = None

    def __init__(self, store: ParquetStore, today: Callable[[], date_] = _new_york_today) -> None:
        self._store = store
        self._today = today

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del args, ctx
        as_of, metas = read_meta(self._store)
        pairs = [(spec.series_id, u.value) for spec in SERIES_CATALOG for u in spec.dashboard_units]
        observations = read_observations(self._store, pairs)
        if as_of is None or not observations:
            return GetUsMacroDashboardOutput(
                data_available=False, unavailable_reason=_NOT_LOADED, as_of=as_of
            )
        releases = read_release_dates(self._store)
        today = self._today()
        panels: list[DashboardPanel] = []
        for group in MacroGroup:
            rows = [
                self._dashboard_series(spec, units, observations, metas, releases, today)
                for spec in SERIES_CATALOG
                if spec.group is group
                for units in spec.dashboard_units
            ]
            panels.append(
                DashboardPanel(group=group, label=GROUP_LABELS[group], series=tuple(rows))
            )
        spreads = [
            state
            for series_id in YIELD_CURVE_SPREAD_IDS
            if (
                state := _yield_curve_state(
                    CATALOG_BY_ID[series_id],
                    observations.get((series_id, CATALOG_BY_ID[series_id].default_units.value), []),
                )
            )
            is not None
        ]
        recession = observations.get(
            (RECESSION_SERIES_ID, CATALOG_BY_ID[RECESSION_SERIES_ID].default_units.value), []
        )
        return GetUsMacroDashboardOutput(
            as_of=as_of,
            panels=tuple(panels),
            yield_curve=tuple(spreads),
            recession_bands=tuple(_recession_bands(recession)),
        )

    @staticmethod
    def _dashboard_series(  # noqa: PLR0913, PLR0917 - one lookup table per argument
        spec: MacroSeriesSpec,
        units: MacroUnits,
        observations: dict[tuple[str, str], list[ObservationPoint]],
        metas: dict[str, MacroSeriesMetaRow],
        releases: list[MacroReleaseDateRow],
        today: date_,
    ) -> DashboardSeries:
        points = observations.get((spec.series_id, units.value), [])
        latest, prior, change = _latest_prior_change(points)
        meta = metas.get(spec.series_id)
        return DashboardSeries(
            series_id=spec.series_id,
            label=spec.label,
            title=meta.title if meta else None,
            units=units,
            unit_kind=spec.kind_for(units),
            frequency=spec.frequency.value,
            higher_means=spec.higher_means,
            higher_means_text=HIGHER_MEANS_TEXT[spec.higher_means],
            copyrighted_third_party=spec.copyrighted_third_party,
            latest=_as_point(latest),
            prior=_as_point(prior),
            change=change,
            last_observation_date=latest.observation_date if latest else None,
            release_name=meta.release_name if meta else None,
            next_release_date=_next_release(releases, meta.release_id if meta else None, today),
            sparkline=tuple(
                MacroPoint(date=p.observation_date, value=p.value)
                for p in points[-SPARKLINE_POINTS:]
            ),
        )


# --- get_us_economic_releases -----------------------------------------------------------------


class ReleaseSeries(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    label: str
    units: MacroUnits
    unit_kind: UnitKind
    latest: MacroPoint | None = None
    previous: MacroPoint | None = None
    change: float | None = None
    last_observation_date: date_ | None = None


class ReleaseEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    release_id: int
    # Stable text key: merge with other calendars by this name.
    release_name: str
    release_date: date_
    status: Literal["upcoming", "released"]
    series: tuple[ReleaseSeries, ...]


class GetUsEconomicReleasesInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_date: date_ | None = Field(
        default=None, description="First release date to include (default: two weeks ago)."
    )
    to_date: date_ | None = Field(
        default=None, description="Last release date to include (default: 30 days ahead)."
    )

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.from_date and self.to_date:
            if self.from_date > self.to_date:
                raise ValueError("from_date is after to_date")
            if (self.to_date - self.from_date).days > MAX_RELEASE_RANGE_DAYS:
                raise ValueError(f"the range must not exceed {MAX_RELEASE_RANGE_DAYS} days")
        return self


class GetUsEconomicReleasesOutput(_ToolOutput):
    from_date: date_ | None = None
    to_date: date_ | None = None
    releases: tuple[ReleaseEntry, ...] = Field(default_factory=tuple)


class GetUsEconomicReleasesTool(SyncStoreTool):
    """`get_us_economic_releases` - the 經濟數據發布 feed: upcoming and recent releases of the
    catalog's series with the latest and previous value. No consensus estimates here: those come
    from the macro-calendar feed, which this one merges with by `release_name`."""

    name = "get_us_economic_releases"
    description = (
        "Get upcoming and recent US economic data releases (release name, release date, the "
        "series it covers, latest value, previous value and change) for a date range. Contains "
        "no forecasts or consensus; use the macro calendar for those."
    )
    input_model: type[BaseModel] = GetUsEconomicReleasesInput
    output_model: type[BaseModel] = GetUsEconomicReleasesOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = None

    def __init__(self, store: ParquetStore, today: Callable[[], date_] = _new_york_today) -> None:
        self._store = store
        self._today = today

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetUsEconomicReleasesInput, args)
        today = self._today()
        start = typed.from_date or today - timedelta(days=DEFAULT_RELEASE_PAST_DAYS)
        end = typed.to_date or max(start, today + timedelta(days=DEFAULT_RELEASE_FUTURE_DAYS))
        as_of, metas = read_meta(self._store)
        calendar = read_release_dates(self._store)
        if as_of is None or not calendar:
            return GetUsEconomicReleasesOutput(
                data_available=False,
                unavailable_reason=_NOT_LOADED,
                as_of=as_of,
                from_date=start,
                to_date=end,
            )
        wanted = [r for r in calendar if start <= r.release_date <= end]
        linked: dict[int, list[MacroSeriesSpec]] = {}
        for spec in SERIES_CATALOG:
            meta = metas.get(spec.series_id)
            if meta is not None and meta.release_id is not None:
                linked.setdefault(meta.release_id, []).append(spec)
        pairs = {
            (spec.series_id, spec.default_units.value)
            for release in wanted
            for spec in linked.get(release.release_id, [])
        }
        observations = read_observations(self._store, sorted(pairs))
        entries = [
            ReleaseEntry(
                release_id=release.release_id,
                release_name=release.release_name,
                release_date=release.release_date,
                status="upcoming" if release.release_date > today else "released",
                series=tuple(
                    self._release_series(spec, observations)
                    for spec in linked.get(release.release_id, [])
                ),
            )
            for release in wanted
        ]
        return GetUsEconomicReleasesOutput(
            as_of=as_of, from_date=start, to_date=end, releases=tuple(entries)
        )

    @staticmethod
    def _release_series(
        spec: MacroSeriesSpec, observations: dict[tuple[str, str], list[ObservationPoint]]
    ) -> ReleaseSeries:
        units = spec.default_units
        latest, prior, change = _latest_prior_change(
            observations.get((spec.series_id, units.value), [])
        )
        return ReleaseSeries(
            series_id=spec.series_id,
            label=spec.label,
            units=units,
            unit_kind=spec.kind_for(units),
            latest=_as_point(latest),
            previous=_as_point(prior),
            change=change,
            last_observation_date=latest.observation_date if latest else None,
        )


# --- get_series_revisions ---------------------------------------------------------------------


class RevisionVintage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # The first date this value was the published one.
    as_of: date_
    value: float


class RevisionRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_date: date_
    first_release_date: date_
    first_release_value: float
    latest_value: float
    latest_vintage_date: date_
    # latest minus first release, in the series' level units.
    revision: float
    revision_count: int
    vintages: tuple[RevisionVintage, ...]


class GetSeriesRevisionsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: RevisionSeriesId = Field(
        description="A revision-tracked series (payrolls, GDP, CPI, PCE, industrial output)."
    )


class GetSeriesRevisionsOutput(_ToolOutput):
    series_id: str
    label: str | None = None
    units_description: str | None = None
    rows: tuple[RevisionRow, ...] = Field(default_factory=tuple)
    first_release_note: str = _FIRST_RELEASE_NOTE


def _revision_rows(vintages: list[MacroVintageRow]) -> list[RevisionRow]:
    by_observation: dict[date_, list[MacroVintageRow]] = {}
    for row in vintages:
        by_observation.setdefault(row.observation_date, []).append(row)
    rows: list[RevisionRow] = []
    for observation_date, history in by_observation.items():
        history.sort(key=lambda r: r.realtime_start)
        distinct: list[RevisionVintage] = []
        for entry in history:
            if not distinct or entry.value != distinct[-1].value:
                distinct.append(RevisionVintage(as_of=entry.realtime_start, value=entry.value))
        first, last = distinct[0], distinct[-1]
        rows.append(
            RevisionRow(
                observation_date=observation_date,
                first_release_date=first.as_of,
                first_release_value=first.value,
                latest_value=last.value,
                latest_vintage_date=last.as_of,
                revision=round(last.value - first.value, 10),
                revision_count=len(distinct) - 1,
                vintages=tuple(distinct),
            )
        )
    return sorted(rows, key=lambda r: r.observation_date, reverse=True)


class GetSeriesRevisionsTool(SyncStoreTool):
    """`get_series_revisions` - first release vs latest vintage for a revision-tracked series."""

    name = "get_series_revisions"
    description = (
        "Get how recent observations of a US macro series (payrolls, GDP, CPI, PCE, industrial "
        "production, retail sales, ...) have been revised: for each observation the first-release "
        "value, the latest value, the size of the revision and every value in between."
    )
    input_model: type[BaseModel] = GetSeriesRevisionsInput
    output_model: type[BaseModel] = GetSeriesRevisionsOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = None

    def __init__(self, store: ParquetStore) -> None:
        self._store = store

    def run_sync(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetSeriesRevisionsInput, args)
        spec = CATALOG_BY_ID[typed.series_id]
        as_of, metas = read_meta(self._store)
        vintages = read_vintages(self._store, typed.series_id)
        if not vintages:
            return GetSeriesRevisionsOutput(
                series_id=typed.series_id,
                label=spec.label,
                data_available=False,
                unavailable_reason=_NOT_LOADED
                if as_of is None
                else "No revision history is stored for this series yet.",
                as_of=as_of,
            )
        meta = metas.get(typed.series_id)
        return GetSeriesRevisionsOutput(
            series_id=typed.series_id,
            label=spec.label,
            units_description=(meta.units or None) if meta else None,
            rows=tuple(_revision_rows(vintages)),
            as_of=as_of,
        )


def build_registration_table(store: ParquetStore | None = None) -> list[tuple[Tool, bool]]:
    """All four tools read the ingest-owned Parquet store, so (like the other store-reading tools)
    the table builds its own `ParquetStore` from `config.settings` unless one is supplied."""
    resolved = store or ParquetStore(root=get_settings().parquet_root)
    return [
        (GetUsMacroSeriesTool(resolved), False),
        (GetUsMacroDashboardTool(resolved), False),
        (GetUsEconomicReleasesTool(resolved), False),
        (GetSeriesRevisionsTool(resolved), False),
    ]


__all__ = [
    "SPARKLINE_POINTS",
    "GetSeriesRevisionsInput",
    "GetSeriesRevisionsOutput",
    "GetSeriesRevisionsTool",
    "GetUsEconomicReleasesInput",
    "GetUsEconomicReleasesOutput",
    "GetUsEconomicReleasesTool",
    "GetUsMacroDashboardOutput",
    "GetUsMacroDashboardTool",
    "GetUsMacroSeriesInput",
    "GetUsMacroSeriesOutput",
    "GetUsMacroSeriesTool",
    "build_registration_table",
]
