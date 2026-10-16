"""The curated US macro catalog: integrity of the constants the ingest job, tools and OpenAPI spec
all derive from."""

from __future__ import annotations

import typing

import pytest
from pydantic import ValidationError

from schemas.fred_catalog import (
    CATALOG_BY_ID,
    GROUP_LABELS,
    HIGHER_MEANS_TEXT,
    RECESSION_SERIES_ID,
    REVISION_SERIES,
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

_EXPECTED_BY_GROUP: dict[MacroGroup, set[str]] = {
    MacroGroup.YIELD_CURVE_RECESSION: {
        "DGS2",
        "DGS5",
        "DGS10",
        "DGS30",
        "T10Y2Y",
        "T10Y3M",
        "USREC",
    },
    MacroGroup.INFLATION: {
        "CPIAUCSL",
        "CPILFESL",
        "PCEPI",
        "PCEPILFE",
        "T5YIE",
        "T10YIE",
        "T5YIFR",
    },
    MacroGroup.LABOR: {"UNRATE", "PAYEMS", "ICSA", "CCSA", "JTSJOL"},
    MacroGroup.CREDIT_FINANCIAL_CONDITIONS: {"NFCI", "STLFSI4", "BAMLH0A0HYM2", "BAMLC0A0CM"},
    MacroGroup.POLICY_LIQUIDITY: {
        "DFF",
        "FEDFUNDS",
        "SOFR",
        "DFEDTARU",
        "DFEDTARL",
        "IORB",
        "WALCL",
        "RRPONTSYD",
    },
    MacroGroup.GROWTH: {"GDPC1", "A191RL1Q225SBEA", "INDPRO", "RSAFS", "DGORDER", "UMCSENT"},
    MacroGroup.HOUSING: {"MORTGAGE30US", "HOUST", "CSUSHPINSA"},
    MacroGroup.DOLLAR: {"DTWEXBGS", "DEXJPUS", "DEXUSEU"},
    MacroGroup.MONEY: {"M2SL", "M2REAL"},
}


def test_every_requested_series_is_in_its_group() -> None:
    by_group = {
        group: {s.series_id for s in SERIES_CATALOG if s.group is group} for group in MacroGroup
    }
    assert by_group == _EXPECTED_BY_GROUP


def test_ids_are_unique_and_match_the_published_literal() -> None:
    ids = [s.series_id for s in SERIES_CATALOG]
    assert len(ids) == len(set(ids)) == 45
    assert set(typing.get_args(MacroSeriesId.__value__)) == set(ids)
    assert set(CATALOG_BY_ID) == set(ids)


def test_the_revision_literal_matches_the_flagged_series() -> None:
    flagged = {s.series_id for s in REVISION_SERIES}
    assert set(typing.get_args(RevisionSeriesId.__value__)) == flagged
    assert {"PAYEMS", "GDPC1", "CPIAUCSL"} <= flagged


def test_every_group_has_a_label_and_every_key_a_sentence() -> None:
    assert set(GROUP_LABELS) == set(MacroGroup)
    for spec in SERIES_CATALOG:
        assert HIGHER_MEANS_TEXT[spec.higher_means].startswith("A ")


def test_wording_is_neutral_no_good_bad_or_trading_language() -> None:
    forbidden = ("buy", "sell", "bullish", "bearish", "good", "bad", "red", "green", "recommend")
    for key, text in HIGHER_MEANS_TEXT.items():
        words = (key + " " + text).lower().replace("_", " ").split()
        assert not [w for w in forbidden if w in words], key
    assert "FRED" not in US_MACRO_CAVEAT
    assert US_MACRO_CAVEAT


def test_default_and_panel_units_are_stored_units() -> None:
    for spec in SERIES_CATALOG:
        assert spec.default_units in spec.stored_units
        assert set(spec.panel_units) <= set(spec.stored_units)
        assert spec.dashboard_units


def test_price_indexes_show_year_over_year_and_month_over_month() -> None:
    for series_id in ("CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE"):
        spec = CATALOG_BY_ID[series_id]
        assert spec.default_units is MacroUnits.PERCENT_CHANGE_YEAR_AGO
        assert spec.dashboard_units == (
            MacroUnits.PERCENT_CHANGE_YEAR_AGO,
            MacroUnits.PERCENT_CHANGE,
        )
        assert spec.unit_kind is UnitKind.PERCENT


def test_payrolls_are_a_monthly_change() -> None:
    spec = CATALOG_BY_ID["PAYEMS"]
    assert spec.default_units is MacroUnits.CHANGE
    assert spec.unit_kind is UnitKind.COUNT
    assert spec.frequency is SeriesFrequency.MONTHLY


def test_third_party_copyrighted_series_are_flagged_but_present() -> None:
    flagged = {s.series_id for s in SERIES_CATALOG if s.copyrighted_third_party}
    assert flagged == {"BAMLH0A0HYM2", "BAMLC0A0CM"}


def test_unit_kind_follows_the_transform() -> None:
    cpi = CATALOG_BY_ID["CPIAUCSL"]
    assert cpi.kind_for(MacroUnits.PERCENT_CHANGE) is UnitKind.PERCENT
    assert cpi.kind_for(MacroUnits.LEVEL) is UnitKind.INDEX
    payems = CATALOG_BY_ID["PAYEMS"]
    assert payems.kind_for(MacroUnits.CHANGE) is UnitKind.COUNT
    assert payems.kind_for(MacroUnits.LEVEL) is UnitKind.COUNT
    assert CATALOG_BY_ID["DGS10"].kind_for(MacroUnits.LEVEL) is UnitKind.PERCENT


def test_special_series_exist() -> None:
    assert RECESSION_SERIES_ID in CATALOG_BY_ID
    assert set(YIELD_CURVE_SPREAD_IDS) <= set(CATALOG_BY_ID)
    assert CATALOG_BY_ID[RECESSION_SERIES_ID].frequency is SeriesFrequency.MONTHLY


def test_a_spec_with_inconsistent_units_is_rejected() -> None:
    with pytest.raises(ValidationError, match="default_units"):
        MacroSeriesSpec(
            series_id="DGS2",
            group=MacroGroup.YIELD_CURVE_RECESSION,
            label="x",
            unit_kind=UnitKind.PERCENT,
            default_units=MacroUnits.CHANGE,
            stored_units=(MacroUnits.LEVEL,),
            frequency=SeriesFrequency.DAILY,
            higher_means="higher_yield",
        )
    with pytest.raises(ValidationError, match="higher_means"):
        MacroSeriesSpec(
            series_id="DGS2",
            group=MacroGroup.YIELD_CURVE_RECESSION,
            label="x",
            unit_kind=UnitKind.PERCENT,
            frequency=SeriesFrequency.DAILY,
            higher_means="nonsense",
        )
