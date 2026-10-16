"""The four US macro tools, reading a store filled by the real ingest job (against the fake service
in `tests/fred_support.py`) - never a live fetch."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ingest.store import ParquetStore
from schemas.enums import DataClass, Tier
from schemas.fred_catalog import (
    CATALOG_BY_ID,
    GROUP_LABELS,
    SERIES_CATALOG,
    US_MACRO_CAVEAT,
    MacroGroup,
    MacroUnits,
)
from tests.fred_support import ANCHOR, FakeFredServer, run_job, series_dates
from tools.base import ToolContext, dispatch_tool
from tools.us_macro_tools import (
    GetSeriesRevisionsInput,
    GetSeriesRevisionsOutput,
    GetSeriesRevisionsTool,
    GetUsEconomicReleasesInput,
    GetUsEconomicReleasesOutput,
    GetUsEconomicReleasesTool,
    GetUsMacroDashboardOutput,
    GetUsMacroDashboardTool,
    GetUsMacroSeriesInput,
    GetUsMacroSeriesOutput,
    GetUsMacroSeriesTool,
    _NoArgs,
    build_registration_table,
)

_TODAY = date(2026, 10, 16)
_REVISED_MONTH = date(2026, 10, 1)


def _today() -> date:
    return _TODAY


@pytest.fixture(scope="module")
def store_root(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("us_macro_store")
    server = FakeFredServer()
    server.revisions["PAYEMS"] = {
        _REVISED_MONTH: [
            (date(2026, 11, 6), 100.0),
            (date(2026, 12, 4), 112.0),
            (date(2027, 1, 8), 112.0),  # re-published unchanged: not a revision
            (date(2027, 2, 5), 95.5),
        ],
    }
    with pytest.MonkeyPatch.context() as patch:
        run, _ = asyncio.run(run_job(root, patch, server))
    assert run.status.value == "success"
    yield root


@pytest.fixture
def series_tool(store_root: Path) -> GetUsMacroSeriesTool:
    return GetUsMacroSeriesTool(ParquetStore(store_root), today=_today)


@pytest.fixture
def dashboard_tool(store_root: Path) -> GetUsMacroDashboardTool:
    return GetUsMacroDashboardTool(ParquetStore(store_root), today=_today)


@pytest.fixture
def releases_tool(store_root: Path) -> GetUsEconomicReleasesTool:
    return GetUsEconomicReleasesTool(ParquetStore(store_root), today=_today)


@pytest.fixture
def revisions_tool(store_root: Path) -> GetSeriesRevisionsTool:
    return GetSeriesRevisionsTool(ParquetStore(store_root))


_CTX = ToolContext()


def _series(tool: GetUsMacroSeriesTool, **kwargs: object) -> GetUsMacroSeriesOutput:
    args = GetUsMacroSeriesInput.model_validate(kwargs)
    out = tool.run_sync(args, _CTX)
    assert isinstance(out, GetUsMacroSeriesOutput)
    return out


# --- registration -----------------------------------------------------------------------------


def test_all_four_tools_are_registered_public_and_deferred(tmp_path: Path) -> None:
    table = build_registration_table(ParquetStore(tmp_path))

    assert {tool.name for tool, _ in table} == {
        "get_us_macro_series",
        "get_us_macro_dashboard",
        "get_us_economic_releases",
        "get_series_revisions",
    }
    assert all(tool.data_class is DataClass.PUBLIC for tool, _ in table)
    assert not any(immediate for _, immediate in table)


def test_no_tool_text_names_the_data_provider(tmp_path: Path) -> None:
    for tool, _ in build_registration_table(ParquetStore(tmp_path)):
        text = (tool.description + tool.input_model.model_json_schema().__str__()).lower()
        assert "fred" not in text
        assert "st. louis" not in text
    assert "fred" not in US_MACRO_CAVEAT.lower()


async def test_a_free_anonymous_caller_gets_the_real_answer(store_root: Path) -> None:
    tool = GetUsMacroSeriesTool(ParquetStore(store_root), today=_today)

    out = await dispatch_tool(
        tool,
        GetUsMacroSeriesInput(series_id="DGS10", limit=5),
        ToolContext(user_id=None, tier=Tier.FREE),
    )

    assert isinstance(out, GetUsMacroSeriesOutput)
    assert out.data_available
    assert len(out.points) == 5


# --- get_us_macro_series ----------------------------------------------------------------------


def test_series_defaults_to_the_catalog_transform_and_carries_metadata(
    series_tool: GetUsMacroSeriesTool,
) -> None:
    out = _series(series_tool, series_id="CPIAUCSL")

    assert out.data_available
    assert out.units is MacroUnits.PERCENT_CHANGE_YEAR_AGO
    assert out.currentness_caveat == US_MACRO_CAVEAT
    meta = out.metadata
    assert meta is not None
    assert meta.title == "Title of CPIAUCSL"
    assert meta.unit_kind.value == "percent"
    assert meta.available_units == (
        MacroUnits.PERCENT_CHANGE_YEAR_AGO,
        MacroUnits.PERCENT_CHANGE,
        MacroUnits.LEVEL,
    )
    assert meta.seasonal_adjustment == "Seasonally Adjusted"
    assert meta.last_updated == "2026-10-14 08:00:00-05"
    assert meta.notes == "Notes for CPIAUCSL"
    assert meta.higher_means == "faster_price_growth"
    assert meta.higher_means_text.startswith("A higher value")
    assert meta.release_name == "Consumer Price Index"
    assert meta.next_release_date == ANCHOR + timedelta(
        days=7 + 10
    )  # the fake calendar: anchor + 7 + id % 20
    assert out.as_of is not None
    # pc1 values are 3.0 + 0.01 * index (the fake service's synthetic series)
    assert out.points[-1].value == pytest.approx(round(1.0 + 0.01 * 39 + 2.0, 4))
    assert out.total_observations == len(series_dates(CATALOG_BY_ID["CPIAUCSL"]))
    assert out.observation_range is not None
    assert out.observation_range.end == date(2026, 10, 1)


def test_series_other_transform_and_limit_keep_the_most_recent_points(
    series_tool: GetUsMacroSeriesTool,
) -> None:
    out = _series(series_tool, series_id="CPIAUCSL", units="lin", limit=3)

    assert out.units is MacroUnits.LEVEL
    assert len(out.points) == 3
    assert out.points[-1].date > out.points[0].date
    assert out.total_observations > 3
    assert out.metadata is not None
    assert out.metadata.unit_kind.value == "index"


def test_series_date_range_is_applied_before_the_limit(series_tool: GetUsMacroSeriesTool) -> None:
    out = _series(
        series_tool,
        series_id="DGS10",
        start_date=ANCHOR - timedelta(days=30),
        end_date=ANCHOR - timedelta(days=10),
    )

    assert out.points
    assert all(
        ANCHOR - timedelta(days=30) <= p.date <= ANCHOR - timedelta(days=10) for p in out.points
    )
    assert out.total_observations == len(out.points)


def test_series_without_points_in_range_is_unavailable_but_calm(
    series_tool: GetUsMacroSeriesTool,
) -> None:
    out = _series(series_tool, series_id="DGS10", start_date=date(2030, 1, 1))

    assert not out.data_available
    assert out.unavailable_reason == "No observations fall in the requested range."
    assert out.currentness_caveat


def test_series_frequency_averages_a_level_series_down(series_tool: GetUsMacroSeriesTool) -> None:
    daily = _series(series_tool, series_id="DGS10", limit=5000)
    quarterly = _series(series_tool, series_id="DGS10", frequency="q", limit=5000)

    assert quarterly.frequency == "q"
    assert len(quarterly.points) < len(daily.points)
    first = quarterly.points[0]
    assert first.date.day == 1
    assert first.date.month in (1, 4, 7, 10)
    in_first = [
        p.value
        for p in daily.points
        if (p.date.year, (p.date.month - 1) // 3) == (first.date.year, (first.date.month - 1) // 3)
    ]
    assert first.value == pytest.approx(sum(in_first) / len(in_first))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"series_id": "DGS10", "units": "pc1"}, "available in units"),
        ({"series_id": "DGS10", "frequency": "d"}, "must be coarser"),
        ({"series_id": "DGS10", "frequency": "w", "units": "lin", "limit": 0}, "greater than"),
        ({"series_id": "CPIAUCSL", "frequency": "q"}, "units=lin"),
        (
            {"series_id": "DGS10", "start_date": date(2026, 2, 1), "end_date": date(2026, 1, 1)},
            "after end_date",
        ),
        ({"series_id": "NOT_A_SERIES"}, "Input should be"),
        ({"series_id": "DGS10", "limit": 5001}, "less than or equal"),
    ],
)
def test_series_input_validation(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        GetUsMacroSeriesInput.model_validate(kwargs)


def test_series_on_an_empty_store_degrades(tmp_path: Path) -> None:
    out = _series(GetUsMacroSeriesTool(ParquetStore(tmp_path), today=_today), series_id="DGS10")

    assert not out.data_available
    assert out.unavailable_reason is not None
    assert "not been loaded" in out.unavailable_reason
    assert out.points == ()
    assert out.currentness_caveat


def test_series_missing_one_transform_says_so(
    store_root: Path, series_tool: GetUsMacroSeriesTool
) -> None:
    out = _series(series_tool, series_id="PAYEMS", units="lin", limit=2)

    assert out.data_available  # PAYEMS stores both chg and lin
    assert out.points[-1].value > 1


# --- get_us_macro_dashboard -------------------------------------------------------------------


def _dashboard(tool: GetUsMacroDashboardTool) -> GetUsMacroDashboardOutput:
    out = tool.run_sync(_NoArgs(), _CTX)
    assert isinstance(out, GetUsMacroDashboardOutput)
    return out


def test_dashboard_groups_every_series_in_display_order(
    dashboard_tool: GetUsMacroDashboardTool,
) -> None:
    out = _dashboard(dashboard_tool)

    assert out.data_available
    assert [p.group for p in out.panels] == list(MacroGroup)
    assert [p.label for p in out.panels] == [GROUP_LABELS[g] for g in MacroGroup]
    rows = [s for p in out.panels for s in p.series]
    # 45 series, plus the month-over-month row of the four price indexes
    assert len(rows) == len(SERIES_CATALOG) + 4
    inflation = next(p for p in out.panels if p.group is MacroGroup.INFLATION)
    cpi_rows = [s for s in inflation.series if s.series_id == "CPIAUCSL"]
    assert [s.units for s in cpi_rows] == [
        MacroUnits.PERCENT_CHANGE_YEAR_AGO,
        MacroUnits.PERCENT_CHANGE,
    ]
    assert out.currentness_caveat == US_MACRO_CAVEAT


def test_dashboard_series_carry_latest_prior_change_and_sparkline(
    dashboard_tool: GetUsMacroDashboardTool,
) -> None:
    out = _dashboard(dashboard_tool)
    unrate = next(s for p in out.panels for s in p.series if s.series_id == "UNRATE")

    assert unrate.latest is not None
    assert unrate.prior is not None
    assert unrate.latest.date == date(2026, 10, 1)
    assert unrate.prior.date == date(2026, 9, 1)
    assert unrate.change == pytest.approx(unrate.latest.value - unrate.prior.value)
    assert unrate.last_observation_date == unrate.latest.date
    assert unrate.higher_means == "higher_unemployment_share"
    assert unrate.release_name == "Employment Situation"
    assert unrate.next_release_date == ANCHOR + timedelta(days=7 + 50 % 20)
    assert len(unrate.sparkline) == 40  # fewer than 60 stored
    assert unrate.sparkline[-1] == unrate.latest


def test_dashboard_sparkline_is_capped_at_sixty_points(
    dashboard_tool: GetUsMacroDashboardTool,
) -> None:
    out = _dashboard(dashboard_tool)
    dgs10 = next(s for p in out.panels for s in p.series if s.series_id == "DGS10")

    assert 0 < len(dgs10.sparkline) <= 60
    assert dgs10.latest is not None
    assert dgs10.latest.date == ANCHOR  # the latest real observation, not a missing one


def test_dashboard_flags_third_party_series_but_includes_them(
    dashboard_tool: GetUsMacroDashboardTool,
) -> None:
    out = _dashboard(dashboard_tool)
    rows = {s.series_id: s for p in out.panels for s in p.series}

    assert rows["BAMLH0A0HYM2"].copyrighted_third_party
    assert not rows["DGS10"].copyrighted_third_party


def test_dashboard_reports_how_long_the_curve_has_been_inverted(
    dashboard_tool: GetUsMacroDashboardTool,
) -> None:
    out = _dashboard(dashboard_tool)
    spreads = {s.series_id: s for s in out.yield_curve}

    inverted = spreads["T10Y2Y"]
    assert inverted.state == "inverted"
    assert inverted.latest.value < 0
    assert inverted.inverted_since == ANCHOR - timedelta(days=19)
    assert inverted.days_inverted == 20
    assert inverted.since_window_start is False
    positive = spreads["T10Y3M"]
    assert positive.state == "positive"
    assert positive.days_inverted == 0
    assert positive.inverted_since is None
    assert positive.last_inverted_date is None


def test_dashboard_derives_recession_bands_from_the_indicator(
    dashboard_tool: GetUsMacroDashboardTool,
) -> None:
    out = _dashboard(dashboard_tool)

    months = series_dates(CATALOG_BY_ID["USREC"])
    assert len(out.recession_bands) == 1
    band = out.recession_bands[0]
    assert band.start == months[20]
    assert band.end == (months[25].replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(
        days=1
    )
    assert band.ongoing is False
    assert "retroactively" in out.recession_note


def test_dashboard_on_an_empty_store_degrades(tmp_path: Path) -> None:
    out = _dashboard(GetUsMacroDashboardTool(ParquetStore(tmp_path), today=_today))

    assert not out.data_available
    assert out.panels == ()
    assert out.unavailable_reason
    assert out.currentness_caveat


# --- get_us_economic_releases -----------------------------------------------------------------


def _releases(tool: GetUsEconomicReleasesTool, **kwargs: object) -> GetUsEconomicReleasesOutput:
    out = tool.run_sync(GetUsEconomicReleasesInput.model_validate(kwargs), _CTX)
    assert isinstance(out, GetUsEconomicReleasesOutput)
    return out


def test_releases_default_window_mixes_recent_and_upcoming(
    releases_tool: GetUsEconomicReleasesTool,
) -> None:
    out = _releases(releases_tool)

    assert out.from_date == _TODAY - timedelta(days=14)
    assert out.to_date == _TODAY + timedelta(days=30)
    assert {r.status for r in out.releases} == {"upcoming", "released"}
    assert all(out.from_date <= r.release_date <= out.to_date for r in out.releases)
    assert [r.release_date for r in out.releases] == sorted(r.release_date for r in out.releases)


def test_a_release_lists_every_catalog_series_it_covers_with_latest_and_previous(
    releases_tool: GetUsEconomicReleasesTool,
) -> None:
    out = _releases(releases_tool, from_date=date(2026, 9, 1), to_date=date(2026, 12, 31))

    employment = [r for r in out.releases if r.release_name == "Employment Situation"]
    assert employment
    covered = {s.series_id for s in employment[0].series}
    assert covered == {"PAYEMS", "UNRATE"}
    payems = next(s for s in employment[0].series if s.series_id == "PAYEMS")
    assert payems.units is MacroUnits.CHANGE  # the default transform
    assert payems.latest is not None and payems.previous is not None
    assert payems.change == pytest.approx(payems.latest.value - payems.previous.value)
    assert payems.last_observation_date == date(2026, 10, 1)


def test_release_names_are_stable_keys_and_no_consensus_is_present(
    releases_tool: GetUsEconomicReleasesTool,
) -> None:
    out = _releases(releases_tool, from_date=date(2026, 9, 1), to_date=date(2026, 12, 31))

    names = {r.release_name for r in out.releases}
    assert {"Employment Situation", "Consumer Price Index", "Gross Domestic Product"} <= names
    dumped = out.model_dump_json()
    assert "consensus" not in dumped
    assert "forecast" not in dumped


def test_releases_range_validation() -> None:
    with pytest.raises(ValidationError, match="after to_date"):
        GetUsEconomicReleasesInput(from_date=date(2026, 2, 1), to_date=date(2026, 1, 1))
    with pytest.raises(ValidationError, match="must not exceed"):
        GetUsEconomicReleasesInput(from_date=date(2024, 1, 1), to_date=date(2026, 1, 1))


def test_releases_on_an_empty_store_degrade(tmp_path: Path) -> None:
    out = _releases(GetUsEconomicReleasesTool(ParquetStore(tmp_path), today=_today))

    assert not out.data_available
    assert out.releases == ()
    assert out.unavailable_reason


def test_releases_window_with_no_release_is_available_but_empty(
    releases_tool: GetUsEconomicReleasesTool,
) -> None:
    out = _releases(releases_tool, from_date=date(2020, 1, 1), to_date=date(2020, 1, 31))

    assert out.data_available
    assert out.releases == ()


# --- get_series_revisions ---------------------------------------------------------------------


def _revisions(tool: GetSeriesRevisionsTool, series_id: str) -> GetSeriesRevisionsOutput:
    out = tool.run_sync(GetSeriesRevisionsInput.model_validate({"series_id": series_id}), _CTX)
    assert isinstance(out, GetSeriesRevisionsOutput)
    return out


def test_revisions_compare_first_release_with_the_latest_vintage(
    revisions_tool: GetSeriesRevisionsTool,
) -> None:
    out = _revisions(revisions_tool, "PAYEMS")

    assert out.data_available
    assert out.currentness_caveat == US_MACRO_CAVEAT
    assert "first release" in out.first_release_note.lower()
    row = next(r for r in out.rows if r.observation_date == _REVISED_MONTH)
    assert row.first_release_date == date(2026, 11, 6)
    assert row.first_release_value == 100.0
    assert row.latest_value == 95.5
    assert row.latest_vintage_date == date(2027, 2, 5)
    assert row.revision == pytest.approx(-4.5)
    assert row.revision_count == 2  # the unchanged re-publication is not a revision
    assert [(v.as_of, v.value) for v in row.vintages] == [
        (date(2026, 11, 6), 100.0),
        (date(2026, 12, 4), 112.0),
        (date(2027, 2, 5), 95.5),
    ]


def test_revisions_are_newest_observation_first_and_unrevised_rows_say_so(
    revisions_tool: GetSeriesRevisionsTool,
) -> None:
    out = _revisions(revisions_tool, "GDPC1")

    dates = [r.observation_date for r in out.rows]
    assert dates == sorted(dates, reverse=True)
    assert all(r.revision == 0 and r.revision_count == 0 for r in out.rows)
    assert out.units_description == "Index"


def test_revisions_reject_a_series_that_is_not_tracked() -> None:
    with pytest.raises(ValidationError):
        GetSeriesRevisionsInput.model_validate({"series_id": "DGS10"})


def test_revisions_on_an_empty_store_degrade(tmp_path: Path) -> None:
    out = _revisions(GetSeriesRevisionsTool(ParquetStore(tmp_path)), "PAYEMS")

    assert not out.data_available
    assert out.rows == ()
    assert out.unavailable_reason
