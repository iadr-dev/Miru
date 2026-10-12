"""The per-market screener factor catalog — `get_factor_catalog(market)`'s data, describing what
`run_screener` can filter on for TW / US / ETF.

Every factor here maps to a **real, already-computable** input — either an already
nightly-batch-computed store (`screener_flags` tags, `stock_scores` percentiles, raw literal
values from a whole-market-ingested partition like `valuation_ratios`) or a native yfinance
screener field (`sources/yfinance/screener.py`'s `EquityQuery`/`ETFQuery` field sets). Nothing here
is aspirational: `docs/backend/governance.md` §1.3/§6 forbids a full-market scan outside the ingest
layer, so a factor that would require a LIVE per-symbol fetch across the whole TW universe at
query time is deliberately **not** listed. 本益比/股價淨值比/現金殖利率
(`_TW_RAW_VALUATION_FACTORS`) ARE listed as raw literal values, not just percentiles —
`VALUATION_RATIOS_JOB` already ingests
`valuation_ratios` whole-market daily (PUBLIC tier), so reading it back is the same "already-
materialized store" shape `stock_scores` uses, not a live fetch. 近4季ROE/EPS as raw values remain
a follow-up: the underlying financial-statement partitions are whole-market-ingested too, but
computing them needs a new batch pass (`analytics/scoring_batch/`'s per-symbol `fundamentals.py`
read, not yet run as a whole-market batch) rather than a one-partition read-back.

US omits 動能 (momentum/technical) and 分析師 (analyst) families this pass: yfinance's screener
query (`yfinance.const.EQUITY_SCREENER_FIELDS`) carries no technical-indicator or analyst-rating
fields natively — those live in separate tools (`get_indicators`, `get_analyst_consensus`) that
would need a post-filter join against screener candidates, not a native query condition. Folding
them in as real filterable US conditions is a follow-up.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from analytics.screener.catalog import TagCategory, all_tags
from schemas.enums import DataClass, Market


class FactorOperator(StrEnum):
    """The operator vocabulary a `FactorField` accepts — mirrors `schemas.screener.
    ScreenerOperator` but scoped to what the condition builder's `FilterValueInput` renders."""

    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    BETWEEN = "between"
    EQ = "eq"
    IN = "in"


class FactorField(BaseModel):
    """One filterable factor. `key` is the token `run_screener`'s `conditions[].factor` expects.
    `min`/`max`/`step` bound the slider; `None` for an enum-like field (`op_set` = `{eq, in}`
    only), which instead lists its choices in `enum_values`. `source_field` is the underlying
    upstream field name the engine maps this key to (a yfinance screener field for US/ETF, or a
    `stock_scores` column name for TW) — kept alongside the bilingual label so the engine and the
    catalog never drift apart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    label_zh: str = Field(min_length=1)
    label_en: str = Field(min_length=1)
    family: str = Field(min_length=1)
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    preset_chips: tuple[float, ...] = Field(default_factory=tuple)
    enum_values: tuple[str, ...] | None = None
    op_set: tuple[FactorOperator, ...] = (
        FactorOperator.GT,
        FactorOperator.GTE,
        FactorOperator.LT,
        FactorOperator.LTE,
        FactorOperator.BETWEEN,
    )
    data_class: DataClass = DataClass.PUBLIC
    source_field: str = Field(min_length=1)


class SignalTag(BaseModel):
    """One boolean 訊號 tag, the catalog's copy of a `TagDefinition` (see `catalog.py`) reshaped
    for the condition builder's Signals sub-mode — a checklist item, not a slider row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    label_zh: str = Field(min_length=1)
    label_en: str = Field(min_length=1)
    family: str = Field(min_length=1)
    description: str = Field(min_length=1)
    data_class: DataClass = DataClass.PUBLIC


class FactorCatalog(BaseModel):
    """The full per-market catalog `get_factor_catalog` returns: boolean signal tags (TW only —
    US/ETF have no batch-computed tag partition) plus numeric factor fields grouped by family."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    signals: tuple[SignalTag, ...] = Field(default_factory=tuple)
    fields: tuple[FactorField, ...] = Field(default_factory=tuple)
    families: tuple[str, ...] = Field(default_factory=tuple)


# -- TW: numeric factors sourced from the batch-computed `stock_scores` cache -------------------
#
# Each maps 1:1 to one of `StockScoreBreakdown`'s five percentile factors (`analytics/models.py`).
# `source_field` names the `StockScoreRow` column this reads (`f"{name}_percentile"`, built by
# the engine, not spelled out here — kept as the bare factor name so this catalog does not import
# the relational row shape).

_TW_SCORE_FACTORS: tuple[FactorField, ...] = (
    FactorField(
        key="momentum_percentile",
        label_zh="動能百分位",
        label_en="Momentum percentile",
        family="動能 Momentum",
        unit="%",
        min=0,
        max=100,
        step=5,
        preset_chips=(70, 80, 90),
        source_field="momentum",
    ),
    FactorField(
        key="trend_quality_percentile",
        label_zh="趨勢品質百分位",
        label_en="Trend quality percentile",
        family="技術 Technical",
        unit="%",
        min=0,
        max=100,
        step=5,
        preset_chips=(70, 80, 90),
        source_field="trend_quality",
    ),
    FactorField(
        key="value_guard_percentile",
        label_zh="價值防護百分位",
        label_en="Value guard percentile",
        family="基本面 Fundamental",
        unit="%",
        min=0,
        max=100,
        step=5,
        preset_chips=(70, 80, 90),
        source_field="value_guard",
    ),
    FactorField(
        key="quality_percentile",
        label_zh="品質百分位",
        label_en="Quality percentile",
        family="基本面 Fundamental",
        unit="%",
        min=0,
        max=100,
        step=5,
        preset_chips=(70, 80, 90),
        source_field="quality",
    ),
    FactorField(
        key="revenue_flow_percentile",
        label_zh="營收籌碼百分位",
        label_en="Revenue/flow percentile",
        family="籌碼 Chips/Flow",
        unit="%",
        min=0,
        max=100,
        step=5,
        preset_chips=(70, 80, 90),
        source_field="revenue_flow",
    ),
)

# -- TW: raw valuation ratios from the `valuation_ratios` whole-market partition ----------------
#
# Unlike `_TW_SCORE_FACTORS` above (batch-computed cross-sectional PERCENTILES in `stock_scores`),
# these are the LITERAL per-stock values TWSE/TPEx publish daily (`ValuationRatiosRow.per`/`pbr`/
# `dividend_yield`, ingested by `VALUATION_RATIOS_JOB` — whole-market, PUBLIC tier, already
# flowing since it backs no other feature today). This is exactly the raw-value tier
# `_TW_SCORE_FACTORS`'s own module docstring flagged as "a follow-up, not built this pass" — it
# turned out the underlying dataset was already ingested, just unused by the screener; no new
# ingest work was needed to add these three fields.

_TW_RAW_VALUATION_FACTORS: tuple[FactorField, ...] = (
    FactorField(
        key="pe_ratio",
        label_zh="本益比",
        label_en="P/E ratio",
        family="基本面 Fundamental",
        unit="倍",
        min=0,
        max=100,
        step=1,
        preset_chips=(15, 20, 30),
        source_field="per",
    ),
    FactorField(
        key="pb_ratio",
        label_zh="股價淨值比",
        label_en="Price/book ratio",
        family="基本面 Fundamental",
        unit="倍",
        min=0,
        max=20,
        step=0.5,
        preset_chips=(1, 2, 3),
        source_field="pbr",
    ),
    FactorField(
        key="dividend_yield",
        label_zh="現金殖利率",
        label_en="Cash dividend yield",
        family="基本面 Fundamental",
        unit="%",
        min=0,
        max=15,
        step=0.5,
        preset_chips=(3, 5, 8),
        source_field="dividend_yield",
    ),
    FactorField(
        key="roe_ttm",
        label_zh="近4季ROE",
        label_en="Return on equity (TTM)",
        family="基本面 Fundamental",
        unit="%",
        min=-50,
        max=50,
        step=1,
        preset_chips=(5, 10, 15),
        source_field="roe_ttm",
    ),
    FactorField(
        key="eps_latest_quarter",
        label_zh="最近一季單季EPS",
        label_en="Latest-quarter EPS",
        family="基本面 Fundamental",
        unit="元",
        min=-10,
        max=30,
        step=0.5,
        preset_chips=(0, 1, 3),
        source_field="eps_latest_quarter",
    ),
)

# -- TW: real technical-indicator VALUES from `analytics/indicators.py` -------------------------
#
# Distinct from `catalog.py`'s boolean 技術面 tags (golden-cross/breakout — a CROSS-EVENT on one
# specific day) and from `_TW_SCORE_FACTORS.trend_quality_percentile` (a cross-sectional RANK) —
# these are the indicator's own literal numeric reading today (RSI(14)=72, not "RSI crossed 70").
# Every one reuses `analytics/indicators.py`'s pure functions over the same `price_eod` bars
# `batch.py`'s tag computation already builds — no new data source, no new pattern-detection
# logic. 背離 (RSI/MACD-vs-price divergence) is NOT listed: it needs pivot-point detection across
# two series simultaneously, which does not exist anywhere in this codebase yet (the same class of
# follow-up `indicators.py`'s own module docstring flags for 支撐/壓力/N波回檔) — never guessed at.

_TW_TECHNICAL_INDICATOR_FACTORS: tuple[FactorField, ...] = (
    FactorField(
        key="rsi_14",
        label_zh="RSI(14)",
        label_en="RSI (14-day)",
        family="技術 Technical",
        unit="",
        min=0,
        max=100,
        step=5,
        preset_chips=(30, 50, 70),
        source_field="rsi_14",
    ),
    FactorField(
        key="ma20_bias_pct",
        label_zh="收盤價相對20日均線偏離率",
        label_en="Close vs. 20-day MA bias",
        family="技術 Technical",
        unit="%",
        min=-30,
        max=30,
        step=1,
        preset_chips=(-10, 0, 10),
        source_field="ma20_bias_pct",
    ),
    FactorField(
        key="volume_vs_20d_avg_ratio",
        label_zh="成交量相對20日均量倍數",
        label_en="Volume vs. 20-day average (multiple)",
        family="技術 Technical",
        unit="倍",
        min=0,
        max=10,
        step=0.5,
        preset_chips=(1.5, 2, 3),
        source_field="volume_vs_20d_avg_ratio",
    ),
    FactorField(
        key="macd_histogram",
        label_zh="MACD柱狀圖數值",
        label_en="MACD histogram",
        family="技術 Technical",
        unit="",
        min=-10,
        max=10,
        step=0.5,
        preset_chips=(-2, 0, 2),
        source_field="macd_histogram",
    ),
    FactorField(
        key="kd_k",
        label_zh="KD-K值",
        label_en="Stochastic %K",
        family="技術 Technical",
        unit="",
        min=0,
        max=100,
        step=5,
        preset_chips=(20, 50, 80),
        source_field="kd_k",
    ),
    FactorField(
        key="kd_d",
        label_zh="KD-D值",
        label_en="Stochastic %D",
        family="技術 Technical",
        unit="",
        min=0,
        max=100,
        step=5,
        preset_chips=(20, 50, 80),
        source_field="kd_d",
    ),
)

# 分點主力進出 · 八大官股買賣超 — sponsor-classed, value never resolved here; the results-table
# render layer 馬賽克s the column for an unentitled caller. Listed so the condition builder can
# show the row (with its 🔒 chip) even for a Free user, per the spec's "selectable, but blurred"
# rule (README.md §4).
_TW_SPONSOR_FACTORS: tuple[FactorField, ...] = (
    FactorField(
        key="broker_branch_net_buy",
        label_zh="分點主力買超",
        label_en="Broker-branch net buy",
        family="籌碼 Chips/Flow",
        unit="張",
        min=0,
        max=5000,
        step=100,
        preset_chips=(500, 1000, 2000),
        data_class=DataClass.SPONSOR,
        source_field="chip_broker_net_buy",
    ),
    FactorField(
        key="government_bank_net_buy",
        label_zh="八大官股買超",
        label_en="Government-bank net buy",
        family="籌碼 Chips/Flow",
        unit="張",
        min=0,
        max=5000,
        step=100,
        preset_chips=(500, 1000, 2000),
        data_class=DataClass.SPONSOR,
        source_field="government_bank_net_buy",
    ),
)


def _tw_signal_tags() -> tuple[SignalTag, ...]:
    return tuple(
        SignalTag(
            key=tag.key,
            label_zh=tag.label_zh,
            label_en=tag.label_en,
            family=tag.category.value,
            description=tag.description,
            data_class=tag.data_class,
        )
        for tag in all_tags()
    )


def _tw_catalog() -> FactorCatalog:
    fields = (
        _TW_SCORE_FACTORS
        + _TW_RAW_VALUATION_FACTORS
        + _TW_TECHNICAL_INDICATOR_FACTORS
        + _TW_SPONSOR_FACTORS
    )
    families = tuple(dict.fromkeys(field.family for field in fields))
    families += tuple(dict.fromkeys(category.value for category in TagCategory))
    return FactorCatalog(
        market=Market.TW.value,
        signals=_tw_signal_tags(),
        fields=fields,
        families=tuple(dict.fromkeys(families)),
    )


# -- US: native yfinance EquityQuery screener fields ---------------------------------------------
#
# `source_field` is the exact `yfinance.const.EQUITY_SCREENER_FIELDS` key (verified against
# installed yfinance 1.6.0) — the engine passes it straight through to `EquityQuery`, no further
# remapping. Scoped to 估值/成長/獲利/持股: valuation, growth, profitability/dividend, and
# ownership/short-interest fields yfinance's screener natively supports. 動能/分析師 dropped this
# pass — see module docstring.

_US_FIELDS: tuple[FactorField, ...] = (
    FactorField(
        key="pe_ratio",
        label_zh="本益比 (PE)",
        label_en="P/E ratio (TTM)",
        family="估值 Valuation",
        unit="x",
        min=0,
        max=100,
        step=1,
        preset_chips=(15, 25, 40),
        source_field="peratio.lasttwelvemonths",
    ),
    FactorField(
        key="peg_ratio",
        label_zh="PEG 比率",
        label_en="PEG ratio (5y)",
        family="估值 Valuation",
        unit="x",
        min=0,
        max=10,
        step=0.1,
        preset_chips=(1, 1.5, 2),
        source_field="pegratio_5y",
    ),
    FactorField(
        key="pb_ratio",
        label_zh="股價淨值比 (PB)",
        label_en="Price/book ratio",
        family="估值 Valuation",
        unit="x",
        min=0,
        max=20,
        step=0.5,
        preset_chips=(1, 3, 5),
        source_field="pricebookratio.quarterly",
    ),
    FactorField(
        key="eps_growth",
        label_zh="EPS 成長率 (近12月)",
        label_en="EPS growth (TTM)",
        family="成長 Growth",
        unit="%",
        min=-50,
        max=200,
        step=5,
        preset_chips=(10, 20, 30),
        source_field="epsgrowth.lasttwelvemonths",
    ),
    FactorField(
        key="revenue_growth",
        label_zh="營收成長率 (近12月)",
        label_en="Revenue growth (TTM)",
        family="成長 Growth",
        unit="%",
        min=-50,
        max=200,
        step=5,
        preset_chips=(10, 20, 30),
        source_field="totalrevenues1yrgrowth.lasttwelvemonths",
    ),
    FactorField(
        key="return_on_equity",
        label_zh="股東權益報酬率 (ROE)",
        label_en="Return on equity",
        family="獲利 Profitability",
        unit="%",
        min=-50,
        max=100,
        step=5,
        preset_chips=(10, 15, 20),
        source_field="returnonequity.lasttwelvemonths",
    ),
    FactorField(
        key="dividend_yield",
        label_zh="股息殖利率",
        label_en="Dividend yield",
        family="獲利 Profitability",
        unit="%",
        min=0,
        max=15,
        step=0.5,
        preset_chips=(2, 4, 6),
        source_field="dividendyield",
    ),
    FactorField(
        key="pct_held_institutions",
        label_zh="機構持股比例",
        label_en="Institutional ownership",
        family="持股 Ownership",
        unit="%",
        min=0,
        max=100,
        step=5,
        preset_chips=(50, 70, 90),
        source_field="pctheldinst",
    ),
    FactorField(
        key="pct_held_insiders",
        label_zh="內部人持股比例",
        label_en="Insider ownership",
        family="持股 Ownership",
        unit="%",
        min=0,
        max=100,
        step=5,
        preset_chips=(5, 10, 20),
        source_field="pctheldinsider",
    ),
    FactorField(
        key="short_pct_of_float",
        label_zh="流通股放空比例",
        label_en="Short % of float",
        family="持股 Ownership",
        unit="%",
        min=0,
        max=50,
        step=1,
        preset_chips=(5, 10, 20),
        source_field="short_percentage_of_float.value",
    ),
)


def _us_catalog() -> FactorCatalog:
    families = tuple(dict.fromkeys(field.family for field in _US_FIELDS))
    return FactorCatalog(market=Market.US.value, signals=(), fields=_US_FIELDS, families=families)


# -- ETF (TW): whole-market `etf_nav`/`etf_aum`/`etf_expense_ratio`/`dividends`/`price_eod`
# Parquet partitions ------------------------------------------------------------------------------
#
# Every field below is readable as an already-ingested **whole-market partition** for the whole
# TW-ETF universe in one read — the exact same partitions/joins `get_etf_list` already assembles
# for its own whole-market aggregate view (`tools/market/etf_list.py::_gather_etf_list_inputs`);
# this
# catalog just exposes those already-computed fields as screener-filterable factors instead of
# duplicating their derivation. 受益人數 (`get_etf_theme_tags`) and 主動ETF 操作日報
# (`get_active_etf_report`) are still excluded — genuine **per-symbol** tool reads with no
# whole-market partition behind them, so fanning either out live across the ETF universe (~300
# symbols) on every screener request is exactly the expensive, ungoverned-scan shape this codebase
# avoids elsewhere. Both remain a follow-up: either accept the bounded per-symbol fan-out cost
# explicitly, or add a nightly batch snapshot mirroring `job_screener_flags`.

_ETF_FIELDS: tuple[FactorField, ...] = (
    FactorField(
        key="premium_discount_pct",
        label_zh="折溢價幅度",
        label_en="Premium/discount to NAV",
        family="折溢價 Premium",
        unit="%",
        min=-10,
        max=10,
        step=0.5,
        preset_chips=(-2, 0, 2),
        source_field="premium_discount_pct",
    ),
    FactorField(
        key="aum",
        label_zh="基金規模 (AUM)",
        label_en="Assets under management",
        family="規模/受益人 Size",
        unit="億元",
        min=0,
        max=5000,
        step=50,
        preset_chips=(100, 500, 1000),
        source_field="aum",
    ),
    FactorField(
        key="distribution_yield_ttm_pct",
        label_zh="近12月配息率",
        label_en="Trailing 12-month distribution yield",
        family="配息 Distribution",
        unit="%",
        min=0,
        max=20,
        step=0.5,
        preset_chips=(4, 6, 8),
        source_field="distribution_yield_ttm_pct",
    ),
    FactorField(
        key="expense_ratio_pct_approx",
        label_zh="費用率 (經理費+保管費)",
        label_en="Expense ratio (management + custody fee)",
        family="費用 Cost",
        unit="%",
        min=0,
        max=2,
        step=0.05,
        preset_chips=(0.3, 0.5, 1.0),
        source_field="expense_ratio_pct_approx",
        op_set=(FactorOperator.LT, FactorOperator.LTE, FactorOperator.GT, FactorOperator.GTE),
    ),
    FactorField(
        key="return_1w_pct",
        label_zh="近一週報酬",
        label_en="1-week price return",
        family="報酬 Return",
        unit="%",
        min=-20,
        max=20,
        step=1,
        preset_chips=(2, 5, 10),
        source_field="return_1w_pct",
    ),
    FactorField(
        key="return_1m_pct",
        label_zh="近一月報酬",
        label_en="1-month price return",
        family="報酬 Return",
        unit="%",
        min=-30,
        max=30,
        step=1,
        preset_chips=(5, 10, 15),
        source_field="return_1m_pct",
    ),
    FactorField(
        key="return_1q_pct",
        label_zh="近一季報酬",
        label_en="1-quarter price return",
        family="報酬 Return",
        unit="%",
        min=-40,
        max=60,
        step=2,
        preset_chips=(5, 10, 20),
        source_field="return_1q_pct",
    ),
    FactorField(
        key="return_6m_pct",
        label_zh="近半年報酬",
        label_en="6-month price return",
        family="報酬 Return",
        unit="%",
        min=-50,
        max=80,
        step=5,
        preset_chips=(10, 15, 25),
        source_field="return_6m_pct",
    ),
    FactorField(
        key="return_1y_pct",
        label_zh="近一年報酬",
        label_en="1-year price return",
        family="報酬 Return",
        unit="%",
        min=-50,
        max=100,
        step=5,
        preset_chips=(10, 20, 30),
        source_field="return_1y_pct",
    ),
    FactorField(
        key="symbol_type",
        label_zh="ETF類型",
        label_en="ETF type",
        family="類型 Type",
        source_field="symbol_type",
        op_set=(FactorOperator.EQ,),
        enum_values=("etf", "etf_leveraged", "etf_inverse"),
    ),
    FactorField(
        key="distribution_frequency",
        label_zh="配息頻率",
        label_en="Distribution frequency",
        family="配息 Distribution",
        source_field="distribution_frequency",
        op_set=(FactorOperator.EQ,),
        enum_values=("月配", "季配", "半年配", "年配", "不配息"),
    ),
)


def _etf_catalog() -> FactorCatalog:
    families = tuple(dict.fromkeys(field.family for field in _ETF_FIELDS))
    return FactorCatalog(
        market="ETF",
        signals=(),
        fields=_ETF_FIELDS,
        families=families,
    )


def get_factor_catalog(market: str) -> FactorCatalog:
    """The per-market factor catalog `run_screener`'s condition builder discovers factors from.
    `market` is `"TW"`, `"US"`, or `"ETF"` — the screener's own three-tab model (README.md §2),
    distinct from `schemas.enums.Market` (which has no `ETF` value; TW-ETF is `(Market.TW, etf)`
    everywhere else in this codebase, but the screener tab set treats it as a peer tab, so this
    function takes the plain string the frontend's market tabs send rather than the `Market` enum).
    An unrecognized market returns an empty catalog rather than raising — a stale/typo'd market
    query degrades to "no factors", never a 500."""
    if market == Market.TW.value:
        return _tw_catalog()
    if market == Market.US.value:
        return _us_catalog()
    if market == "ETF":
        return _etf_catalog()
    return FactorCatalog(market=market, signals=(), fields=(), families=())


__all__ = [
    "FactorCatalog",
    "FactorField",
    "FactorOperator",
    "SignalTag",
    "get_factor_catalog",
]
