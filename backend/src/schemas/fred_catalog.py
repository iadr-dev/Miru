"""The curated US macro series catalog: the one list of series the ingest job pulls and the US macro
tools/dashboard expose.

Everything here is a constant. A series is in the catalog because the dashboard shows it; the
ingest job fetches exactly this list (never "every series"), the tools refuse any id outside it,
and the `MacroSeriesId` literal below is what the OpenAPI spec publishes as the allowed values (a
test keeps the literal and the tuple identical).

Wording rule: `higher_means` is a neutral, descriptive key ("a higher value means ...") with the
English sentence in `HIGHER_MEANS_TEXT`. There is deliberately no good/bad or red/green notion and
no trading language anywhere in this module.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

type MacroSeriesId = Literal[
    "DGS2",
    "DGS5",
    "DGS10",
    "DGS30",
    "T10Y2Y",
    "T10Y3M",
    "USREC",
    "CPIAUCSL",
    "CPILFESL",
    "PCEPI",
    "PCEPILFE",
    "T5YIE",
    "T10YIE",
    "T5YIFR",
    "UNRATE",
    "PAYEMS",
    "ICSA",
    "CCSA",
    "JTSJOL",
    "NFCI",
    "STLFSI4",
    "BAMLH0A0HYM2",
    "BAMLC0A0CM",
    "DFF",
    "FEDFUNDS",
    "SOFR",
    "DFEDTARU",
    "DFEDTARL",
    "IORB",
    "WALCL",
    "RRPONTSYD",
    "GDPC1",
    "A191RL1Q225SBEA",
    "INDPRO",
    "RSAFS",
    "DGORDER",
    "UMCSENT",
    "MORTGAGE30US",
    "HOUST",
    "CSUSHPINSA",
    "DTWEXBGS",
    "DEXJPUS",
    "DEXUSEU",
    "M2SL",
    "M2REAL",
]

# The series whose first-release vs latest-vintage history is ingested (`get_series_revisions`).
type RevisionSeriesId = Literal[
    "PAYEMS",
    "GDPC1",
    "A191RL1Q225SBEA",
    "CPIAUCSL",
    "CPILFESL",
    "PCEPI",
    "PCEPILFE",
    "INDPRO",
    "RSAFS",
    "DGORDER",
    "UNRATE",
    "JTSJOL",
    "HOUST",
]

# Carried on every US macro result (tools and source results alike). Deliberately names no data
# provider: it reaches the user.
US_MACRO_CAVEAT = (
    "Official US macroeconomic statistics, published on each series' own schedule and not a "
    "real-time quote: daily series usually trail by a business day, weekly series by about a "
    "week, monthly series by roughly two to six weeks after the month ends and quarterly series "
    "by one to three months. Recent values can be revised by the statistical agencies. "
    "Descriptive context only, never a trading signal."
)

# How far back the first run (and every weekly full refresh) pulls.
LOOKBACK_YEARS = 5


class MacroUnits(StrEnum):
    """The upstream `units` transformation codes the catalog uses (stored per series)."""

    LEVEL = "lin"  # levels, no transformation
    CHANGE = "chg"  # change from the previous observation
    CHANGE_YEAR_AGO = "ch1"  # change from a year ago
    PERCENT_CHANGE = "pch"  # percent change from the previous observation
    PERCENT_CHANGE_YEAR_AGO = "pc1"  # percent change from a year ago
    PERCENT_CHANGE_ANNUALIZED = "pca"  # compounded annual rate of change


class SeriesFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class UnitKind(StrEnum):
    PERCENT = "percent"
    LEVEL = "level"
    INDEX = "index"
    COUNT = "count"


class MacroGroup(StrEnum):
    """Dashboard panels, in display order."""

    YIELD_CURVE_RECESSION = "yield_curve_recession"
    INFLATION = "inflation"
    LABOR = "labor"
    CREDIT_FINANCIAL_CONDITIONS = "credit_financial_conditions"
    POLICY_LIQUIDITY = "policy_liquidity"
    GROWTH = "growth"
    HOUSING = "housing"
    DOLLAR = "dollar"
    MONEY = "money"


GROUP_LABELS: dict[MacroGroup, str] = {
    MacroGroup.YIELD_CURVE_RECESSION: "Yield curve and recession",
    MacroGroup.INFLATION: "Inflation",
    MacroGroup.LABOR: "Labor market",
    MacroGroup.CREDIT_FINANCIAL_CONDITIONS: "Credit and financial conditions",
    MacroGroup.POLICY_LIQUIDITY: "Policy rates and liquidity",
    MacroGroup.GROWTH: "Growth and activity",
    MacroGroup.HOUSING: "Housing",
    MacroGroup.DOLLAR: "Dollar",
    MacroGroup.MONEY: "Money supply",
}

# Neutral "a higher value means ..." sentences, keyed by `MacroSeriesSpec.higher_means`.
HIGHER_MEANS_TEXT: dict[str, str] = {
    "higher_yield": "A higher value means a higher yield.",
    "steeper_curve": (
        "A higher value means longer-term yields sit further above shorter-term yields; "
        "a negative value means they sit below."
    ),
    "recession_month": "A value of 1 marks a month inside an officially dated recession.",
    "faster_price_growth": "A higher value means prices are rising faster.",
    "higher_expected_inflation": (
        "A higher value means the market-implied average inflation rate is higher."
    ),
    "higher_unemployment_share": "A higher value means a larger share of the labor force is "
    "unemployed.",
    "more_jobs_added": "A higher value means more jobs were added over the month.",
    "more_claims": "A higher value means more people are filing claims.",
    "more_openings": "A higher value means more job openings.",
    "tighter_conditions": "A higher value means financial conditions are tighter than average.",
    "more_financial_stress": "A higher value means more financial-market stress.",
    "wider_credit_spread": "A higher value means a wider yield gap over Treasuries.",
    "higher_rate": "A higher value means a higher interest rate.",
    "larger_balance_sheet": "A higher value means a larger central-bank balance sheet.",
    "more_reverse_repo": "A higher value means more overnight reverse-repo take-up.",
    "larger_output": "A higher value means a larger volume of goods and services produced.",
    "faster_growth": "A higher value means output grew at a faster annualized pace.",
    "higher_industrial_output": "A higher value means higher industrial production.",
    "higher_retail_sales": "A higher value means higher retail and food-service sales.",
    "more_orders": "A higher value means more new orders for durable goods.",
    "higher_sentiment": "A higher value means consumers report more favorable sentiment.",
    "higher_mortgage_rate": "A higher value means a higher average 30-year mortgage rate.",
    "more_housing_starts": "A higher value means more new homes were started.",
    "higher_home_prices": "A higher value means higher home prices.",
    "higher_dollar_value": (
        "A higher value means the dollar is worth more against a broad basket of currencies."
    ),
    "more_yen_per_dollar": "A higher value means one dollar buys more yen.",
    "more_dollars_per_euro": "A higher value means one euro buys more dollars.",
    "larger_money_stock": "A higher value means a larger money stock.",
}

_PERCENT_UNITS = frozenset(
    {
        MacroUnits.PERCENT_CHANGE,
        MacroUnits.PERCENT_CHANGE_YEAR_AGO,
        MacroUnits.PERCENT_CHANGE_ANNUALIZED,
    }
)


class MacroSeriesSpec(BaseModel):
    """One catalog entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: MacroSeriesId
    group: MacroGroup
    label: str = Field(min_length=1)
    # What the default transform's values are measured in.
    unit_kind: UnitKind
    default_units: MacroUnits = MacroUnits.LEVEL
    # Every transform the ingest job stores for this series (the tools serve only these).
    stored_units: tuple[MacroUnits, ...] = (MacroUnits.LEVEL,)
    # The transforms the dashboard shows as separate rows (year-over-year and month-over-month
    # for the price indexes); always a subset of `stored_units`.
    panel_units: tuple[MacroUnits, ...] = ()
    frequency: SeriesFrequency
    higher_means: str
    # Redistribution of these series is restricted by their original publisher. Included on the
    # product owner's instruction; flagged so that decision stays visible.
    copyrighted_third_party: bool = False
    # First-release vs latest-vintage history is ingested for this series.
    track_revisions: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> MacroSeriesSpec:
        if self.default_units not in self.stored_units:
            raise ValueError(f"{self.series_id}: default_units must be a stored unit")
        if not set(self.panel_units) <= set(self.stored_units):
            raise ValueError(f"{self.series_id}: panel_units must be stored units")
        if self.higher_means not in HIGHER_MEANS_TEXT:
            raise ValueError(f"{self.series_id}: unknown higher_means key {self.higher_means!r}")
        return self

    @property
    def dashboard_units(self) -> tuple[MacroUnits, ...]:
        return self.panel_units or (self.default_units,)

    def kind_for(self, units: MacroUnits) -> UnitKind:
        """The measurement kind of this series under `units`: a percent-style transform is
        always a percent; a change keeps the base series' kind (count stays count)."""
        if units in _PERCENT_UNITS:
            return UnitKind.PERCENT
        if self.default_units is units:
            return self.unit_kind
        return self.base_kind

    @property
    def base_kind(self) -> UnitKind:
        """The kind of the untransformed (`lin`) values."""
        if self.default_units in _PERCENT_UNITS:
            # The default is a transform of an index/level (e.g. CPI year-over-year): the
            # underlying levels are an index.
            return UnitKind.INDEX
        return self.unit_kind


def _spec(  # noqa: PLR0913, PLR0917 - one argument per catalog column
    series_id: MacroSeriesId,
    group: MacroGroup,
    label: str,
    unit_kind: UnitKind,
    frequency: SeriesFrequency,
    higher_means: str,
    *,
    default_units: MacroUnits = MacroUnits.LEVEL,
    stored_units: tuple[MacroUnits, ...] | None = None,
    panel_units: tuple[MacroUnits, ...] = (),
    copyrighted_third_party: bool = False,
    track_revisions: bool = False,
) -> MacroSeriesSpec:
    return MacroSeriesSpec(
        series_id=series_id,
        group=group,
        label=label,
        unit_kind=unit_kind,
        frequency=frequency,
        higher_means=higher_means,
        default_units=default_units,
        stored_units=stored_units if stored_units is not None else (default_units,),
        panel_units=panel_units,
        copyrighted_third_party=copyrighted_third_party,
        track_revisions=track_revisions,
    )


_D, _W, _M, _Q = (
    SeriesFrequency.DAILY,
    SeriesFrequency.WEEKLY,
    SeriesFrequency.MONTHLY,
    SeriesFrequency.QUARTERLY,
)
_LIN, _CHG, _PCH, _PC1, _PCA = (
    MacroUnits.LEVEL,
    MacroUnits.CHANGE,
    MacroUnits.PERCENT_CHANGE,
    MacroUnits.PERCENT_CHANGE_YEAR_AGO,
    MacroUnits.PERCENT_CHANGE_ANNUALIZED,
)
_P, _L, _I, _C = UnitKind.PERCENT, UnitKind.LEVEL, UnitKind.INDEX, UnitKind.COUNT
_G = MacroGroup

SERIES_CATALOG: tuple[MacroSeriesSpec, ...] = (
    # --- Yield curve and recession -------------------------------------------------------
    _spec("DGS2", _G.YIELD_CURVE_RECESSION, "2-Year Treasury yield", _P, _D, "higher_yield"),
    _spec("DGS5", _G.YIELD_CURVE_RECESSION, "5-Year Treasury yield", _P, _D, "higher_yield"),
    _spec("DGS10", _G.YIELD_CURVE_RECESSION, "10-Year Treasury yield", _P, _D, "higher_yield"),
    _spec("DGS30", _G.YIELD_CURVE_RECESSION, "30-Year Treasury yield", _P, _D, "higher_yield"),
    _spec(
        "T10Y2Y",
        _G.YIELD_CURVE_RECESSION,
        "10-Year minus 2-Year Treasury spread",
        _P,
        _D,
        "steeper_curve",
    ),
    _spec(
        "T10Y3M",
        _G.YIELD_CURVE_RECESSION,
        "10-Year minus 3-Month Treasury spread",
        _P,
        _D,
        "steeper_curve",
    ),
    _spec(
        "USREC",
        _G.YIELD_CURVE_RECESSION,
        "Recession indicator (officially dated)",
        _C,
        _M,
        "recession_month",
    ),
    # --- Inflation -----------------------------------------------------------------------
    _spec(
        "CPIAUCSL",
        _G.INFLATION,
        "Consumer prices (CPI), all items",
        _P,
        _M,
        "faster_price_growth",
        default_units=_PC1,
        stored_units=(_PC1, _PCH, _LIN),
        panel_units=(_PC1, _PCH),
        track_revisions=True,
    ),
    _spec(
        "CPILFESL",
        _G.INFLATION,
        "Core consumer prices (CPI), excluding food and energy",
        _P,
        _M,
        "faster_price_growth",
        default_units=_PC1,
        stored_units=(_PC1, _PCH, _LIN),
        panel_units=(_PC1, _PCH),
        track_revisions=True,
    ),
    _spec(
        "PCEPI",
        _G.INFLATION,
        "Personal consumption expenditures price index",
        _P,
        _M,
        "faster_price_growth",
        default_units=_PC1,
        stored_units=(_PC1, _PCH, _LIN),
        panel_units=(_PC1, _PCH),
        track_revisions=True,
    ),
    _spec(
        "PCEPILFE",
        _G.INFLATION,
        "Core personal consumption expenditures price index",
        _P,
        _M,
        "faster_price_growth",
        default_units=_PC1,
        stored_units=(_PC1, _PCH, _LIN),
        panel_units=(_PC1, _PCH),
        track_revisions=True,
    ),
    _spec(
        "T5YIE",
        _G.INFLATION,
        "5-Year breakeven inflation rate",
        _P,
        _D,
        "higher_expected_inflation",
    ),
    _spec(
        "T10YIE",
        _G.INFLATION,
        "10-Year breakeven inflation rate",
        _P,
        _D,
        "higher_expected_inflation",
    ),
    _spec(
        "T5YIFR",
        _G.INFLATION,
        "5-Year, 5-Year forward inflation expectation rate",
        _P,
        _D,
        "higher_expected_inflation",
    ),
    # --- Labor ---------------------------------------------------------------------------
    _spec(
        "UNRATE",
        _G.LABOR,
        "Unemployment rate",
        _P,
        _M,
        "higher_unemployment_share",
        track_revisions=True,
    ),
    _spec(
        "PAYEMS",
        _G.LABOR,
        "Nonfarm payrolls, monthly change",
        _C,
        _M,
        "more_jobs_added",
        default_units=_CHG,
        stored_units=(_CHG, _LIN),
        track_revisions=True,
    ),
    _spec("ICSA", _G.LABOR, "Initial jobless claims", _C, _W, "more_claims"),
    _spec("CCSA", _G.LABOR, "Continued jobless claims", _C, _W, "more_claims"),
    _spec(
        "JTSJOL",
        _G.LABOR,
        "Job openings (JOLTS)",
        _C,
        _M,
        "more_openings",
        track_revisions=True,
    ),
    # --- Credit and financial conditions -----------------------------------------------
    _spec(
        "NFCI",
        _G.CREDIT_FINANCIAL_CONDITIONS,
        "National Financial Conditions Index",
        _I,
        _W,
        "tighter_conditions",
    ),
    _spec(
        "STLFSI4",
        _G.CREDIT_FINANCIAL_CONDITIONS,
        "Financial Stress Index",
        _I,
        _W,
        "more_financial_stress",
    ),
    _spec(
        "BAMLH0A0HYM2",
        _G.CREDIT_FINANCIAL_CONDITIONS,
        "High-yield corporate bond spread",
        _P,
        _D,
        "wider_credit_spread",
        copyrighted_third_party=True,
    ),
    _spec(
        "BAMLC0A0CM",
        _G.CREDIT_FINANCIAL_CONDITIONS,
        "Investment-grade corporate bond spread",
        _P,
        _D,
        "wider_credit_spread",
        copyrighted_third_party=True,
    ),
    # --- Policy and liquidity ------------------------------------------------------------
    _spec(
        "DFF", _G.POLICY_LIQUIDITY, "Effective federal funds rate (daily)", _P, _D, "higher_rate"
    ),
    _spec(
        "FEDFUNDS",
        _G.POLICY_LIQUIDITY,
        "Effective federal funds rate (monthly average)",
        _P,
        _M,
        "higher_rate",
    ),
    _spec("SOFR", _G.POLICY_LIQUIDITY, "Secured Overnight Financing Rate", _P, _D, "higher_rate"),
    _spec(
        "DFEDTARU",
        _G.POLICY_LIQUIDITY,
        "Federal funds target range, upper limit",
        _P,
        _D,
        "higher_rate",
    ),
    _spec(
        "DFEDTARL",
        _G.POLICY_LIQUIDITY,
        "Federal funds target range, lower limit",
        _P,
        _D,
        "higher_rate",
    ),
    _spec(
        "IORB",
        _G.POLICY_LIQUIDITY,
        "Interest rate on reserve balances",
        _P,
        _D,
        "higher_rate",
    ),
    _spec(
        "WALCL",
        _G.POLICY_LIQUIDITY,
        "Federal Reserve total assets",
        _L,
        _W,
        "larger_balance_sheet",
    ),
    _spec(
        "RRPONTSYD",
        _G.POLICY_LIQUIDITY,
        "Overnight reverse repurchase agreements",
        _L,
        _D,
        "more_reverse_repo",
    ),
    # --- Growth and activity -------------------------------------------------------------
    _spec(
        "GDPC1",
        _G.GROWTH,
        "Real gross domestic product",
        _L,
        _Q,
        "larger_output",
        stored_units=(_LIN, _PCA),
        track_revisions=True,
    ),
    _spec(
        "A191RL1Q225SBEA",
        _G.GROWTH,
        "Real GDP growth (annualized quarterly rate)",
        _P,
        _Q,
        "faster_growth",
        track_revisions=True,
    ),
    _spec(
        "INDPRO",
        _G.GROWTH,
        "Industrial production index",
        _I,
        _M,
        "higher_industrial_output",
        track_revisions=True,
    ),
    _spec(
        "RSAFS",
        _G.GROWTH,
        "Retail and food services sales",
        _L,
        _M,
        "higher_retail_sales",
        track_revisions=True,
    ),
    _spec(
        "DGORDER",
        _G.GROWTH,
        "Durable goods new orders",
        _L,
        _M,
        "more_orders",
        track_revisions=True,
    ),
    _spec("UMCSENT", _G.GROWTH, "Consumer sentiment", _I, _M, "higher_sentiment"),
    # --- Housing -------------------------------------------------------------------------
    _spec(
        "MORTGAGE30US",
        _G.HOUSING,
        "30-Year fixed mortgage rate",
        _P,
        _W,
        "higher_mortgage_rate",
    ),
    _spec(
        "HOUST",
        _G.HOUSING,
        "Housing starts",
        _C,
        _M,
        "more_housing_starts",
        track_revisions=True,
    ),
    _spec(
        "CSUSHPINSA",
        _G.HOUSING,
        "Home price index (national)",
        _I,
        _M,
        "higher_home_prices",
        stored_units=(_LIN, _PC1),
    ),
    # --- Dollar --------------------------------------------------------------------------
    _spec(
        "DTWEXBGS",
        _G.DOLLAR,
        "Broad trade-weighted dollar index",
        _I,
        _D,
        "higher_dollar_value",
    ),
    _spec("DEXJPUS", _G.DOLLAR, "Japanese yen per US dollar", _L, _D, "more_yen_per_dollar"),
    _spec("DEXUSEU", _G.DOLLAR, "US dollars per euro", _L, _D, "more_dollars_per_euro"),
    # --- Money supply --------------------------------------------------------------------
    _spec(
        "M2SL",
        _G.MONEY,
        "M2 money stock",
        _L,
        _M,
        "larger_money_stock",
        stored_units=(_LIN, _PC1),
    ),
    _spec(
        "M2REAL",
        _G.MONEY,
        "Real M2 money stock",
        _L,
        _M,
        "larger_money_stock",
        stored_units=(_LIN, _PC1),
    ),
)

CATALOG_BY_ID: dict[str, MacroSeriesSpec] = {spec.series_id: spec for spec in SERIES_CATALOG}

REVISION_SERIES: tuple[MacroSeriesSpec, ...] = tuple(
    spec for spec in SERIES_CATALOG if spec.track_revisions
)

# Recession bands come from this series; the yield-curve state from these two.
RECESSION_SERIES_ID: MacroSeriesId = "USREC"
YIELD_CURVE_SPREAD_IDS: tuple[MacroSeriesId, ...] = ("T10Y2Y", "T10Y3M")


def spec_for(series_id: str) -> MacroSeriesSpec:
    """The catalog entry for `series_id`; raises `KeyError` for an id outside the catalog."""
    return CATALOG_BY_ID[series_id]


__all__ = [
    "CATALOG_BY_ID",
    "GROUP_LABELS",
    "HIGHER_MEANS_TEXT",
    "LOOKBACK_YEARS",
    "RECESSION_SERIES_ID",
    "REVISION_SERIES",
    "SERIES_CATALOG",
    "US_MACRO_CAVEAT",
    "YIELD_CURVE_SPREAD_IDS",
    "MacroGroup",
    "MacroSeriesId",
    "MacroSeriesSpec",
    "MacroUnits",
    "RevisionSeriesId",
    "SeriesFrequency",
    "UnitKind",
    "spec_for",
]
