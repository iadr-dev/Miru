"""Named screener presets — `get_screener_presets(market)`'s data. Each preset is a starting-point
condition set the frontend's `PresetLibrary` loads into the condition builder (README.md §3): a
user sees and can tweak the thesis, never a black-box trade signal.

Every condition here references a factor key that actually exists in this market's
`factor_catalog.get_factor_catalog(market)` output — a preset naming an unbuilt factor would
silently 422 at `run_screener` time, so presets are scoped to what §1's catalog module actually
computes this pass (TW: `stock_scores` percentiles + boolean tags; US: yfinance-native valuation/
growth/profitability/ownership fields; ETF: premium/discount + size fields). Presets from the spec
table (README.md §3) that need an unbuilt factor (e.g. Piotroski's full F-score needs 9 raw
accounting ratios this pass's TW catalog doesn't carry — only score-breakdown percentiles) are
either approximated with the nearest available factor (documented per-preset below) or dropped
outright rather than shipped broken.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import Market
from schemas.screener import ScreenerOperator

_GROUP_OWNERSHIP = "持股 Ownership"

_GROUP_VALUATION = "估值 Valuation"

_GROUP_TECHNICAL = "技術 Technical"

_GROUP_CHIPS = "籌碼 Chips"


class PresetCondition(BaseModel):
    """One condition inside a preset — the same shape `run_screener` accepts, minus the `market`
    wrapper (a preset is scoped to one market by construction)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    factor: str = Field(min_length=1)
    op: ScreenerOperator
    value: float | str | None = None
    value2: float | str | None = None


class ScreenerPreset(BaseModel):
    """One named preset: a category, bilingual labels, a one-line description, a directional cue
    (`bull`/`bear`/`neutral`, drives the card's left-edge bar per README.md §3), and the condition
    set + match mode it loads into the builder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    category: str = Field(min_length=1)
    label_zh: str = Field(min_length=1)
    label_en: str = Field(min_length=1)
    description: str = Field(min_length=1)
    direction: Literal["bull", "bear", "neutral"] = "neutral"
    conditions: tuple[PresetCondition, ...] = Field(min_length=1)
    match: Literal["AND", "OR"] = "AND"


# -- TW --------------------------------------------------------------------------------------
#
# Trend/quality/growth presets approximate their textbook factor sets with the nearest available
# `stock_scores` percentile (e.g. "Piotroski品質改善" reads as high `quality_percentile`, not a
# literal 9-point F-score — that raw-ratio tier is the fundamentals_batch follow-up noted in
# `factor_catalog.py`). Chip/momentum presets use the real boolean tags directly (exact matches).

_TW_PRESETS: tuple[ScreenerPreset, ...] = (
    ScreenerPreset(
        key="tw_trend_strong",
        category="趨勢 Trend",
        label_zh="趨勢強勢股",
        label_en="Strong trend",
        description="趨勢品質與動能雙高的個股 — 技術面站在相對強勢的位置。",
        direction="bull",
        conditions=(
            PresetCondition(factor="trend_quality_percentile", op=ScreenerOperator.GTE, value=70),
            PresetCondition(factor="momentum_percentile", op=ScreenerOperator.GTE, value=70),
        ),
    ),
    ScreenerPreset(
        key="tw_quality_improving",
        category="品質 Quality",
        label_zh="品質改善股",
        label_en="Quality improving",
        description="品質百分位偏高的個股 — 近似 Piotroski 品質篩選的精神，非逐項 F-score。",
        direction="bull",
        conditions=(
            PresetCondition(factor="quality_percentile", op=ScreenerOperator.GTE, value=70),
        ),
    ),
    ScreenerPreset(
        key="tw_value_guard",
        category="品質 Quality",
        label_zh="價值防護股",
        label_en="Value guard",
        description="價值防護百分位偏高的個股 — 估值相對安全邊際較大。",
        direction="bull",
        conditions=(
            PresetCondition(factor="value_guard_percentile", op=ScreenerOperator.GTE, value=70),
        ),
    ),
    ScreenerPreset(
        key="tw_revenue_flow_strong",
        category="成長 Growth",
        label_zh="營收籌碼強勢股",
        label_en="Revenue & flow strength",
        description="營收籌碼百分位偏高的個股 — 近似三率三升、法人買超的篩選精神。",
        direction="bull",
        conditions=(
            PresetCondition(factor="revenue_flow_percentile", op=ScreenerOperator.GTE, value=70),
        ),
    ),
    ScreenerPreset(
        key="tw_trust_buying",
        category=_GROUP_CHIPS,
        label_zh="投信連買",
        label_en="Trust net-buy streak",
        description="投信連續3日買超的個股。",
        direction="bull",
        conditions=(PresetCondition(factor="trust_net_buy_streak_3", op=ScreenerOperator.EQ),),
    ),
    ScreenerPreset(
        key="tw_foreign_buying",
        category=_GROUP_CHIPS,
        label_zh="外資同買",
        label_en="Foreign net-buy streak",
        description="外資連續3日買超的個股。",
        direction="bull",
        conditions=(PresetCondition(factor="foreign_net_buy_streak_3", op=ScreenerOperator.EQ),),
    ),
    ScreenerPreset(
        key="tw_momentum_strong",
        category="動能 Momentum",
        label_zh="近期強勢股",
        label_en="Recent strength",
        description="動能百分位排名前段的個股。",
        direction="bull",
        conditions=(
            PresetCondition(factor="momentum_percentile", op=ScreenerOperator.GTE, value=80),
        ),
    ),
    ScreenerPreset(
        key="tw_volume_breakout",
        category="動能 Momentum",
        label_zh="爆量突破",
        label_en="Volume-confirmed breakout",
        description="站上20日均線且成交量放大的個股。",
        direction="bull",
        conditions=(PresetCondition(factor="smart_breakout_volume", op=ScreenerOperator.EQ),),
    ),
    ScreenerPreset(
        key="tw_low_pe_dividend",
        category="基本面 Fundamental",
        label_zh="低本益比高股息股",
        label_en="Low P/E, high dividend yield",
        description="本益比低於20倍且現金殖利率大於5%的個股 — 讀自TWSE/TPEx每日發布的真實比率。",
        direction="bull",
        conditions=(
            PresetCondition(factor="pe_ratio", op=ScreenerOperator.LT, value=20),
            PresetCondition(factor="dividend_yield", op=ScreenerOperator.GTE, value=5),
        ),
    ),
    ScreenerPreset(
        key="tw_high_roe_value",
        category="基本面 Fundamental",
        label_zh="高ROE價值股",
        label_en="High-ROE value",
        description="近4季ROE大於10%且股價淨值比低於2倍的個股。",
        direction="bull",
        conditions=(
            PresetCondition(factor="roe_ttm", op=ScreenerOperator.GTE, value=10),
            PresetCondition(factor="pb_ratio", op=ScreenerOperator.LT, value=2),
        ),
    ),
    ScreenerPreset(
        key="tw_margin_buildup",
        category=_GROUP_CHIPS,
        label_zh="融資連增股",
        label_en="Margin-purchase balance building",
        description="融資餘額連續3日增加的個股 — 純描述性籌碼觀察，非操作建議。",
        direction="neutral",
        conditions=(
            PresetCondition(factor="margin_purchase_balance_streak_up_3", op=ScreenerOperator.EQ),
        ),
    ),
    ScreenerPreset(
        key="tw_short_covering",
        category=_GROUP_CHIPS,
        label_zh="融券連減股",
        label_en="Short-sale balance shrinking",
        description="融券餘額連續3日減少的個股 — 空頭回補訊號，純描述性籌碼觀察。",
        direction="bull",
        conditions=(
            PresetCondition(factor="short_sale_balance_streak_down_3", op=ScreenerOperator.EQ),
        ),
    ),
    ScreenerPreset(
        key="tw_rsi_overbought",
        category=_GROUP_TECHNICAL,
        label_zh="RSI超買股",
        label_en="RSI overbought",
        description="RSI(14)大於70的個股 — 純描述性技術面觀察，非操作建議。",
        direction="neutral",
        conditions=(PresetCondition(factor="rsi_14", op=ScreenerOperator.GTE, value=70),),
    ),
    ScreenerPreset(
        key="tw_rsi_oversold",
        category=_GROUP_TECHNICAL,
        label_zh="RSI超賣股",
        label_en="RSI oversold",
        description="RSI(14)小於30的個股 — 純描述性技術面觀察，非操作建議。",
        direction="neutral",
        conditions=(PresetCondition(factor="rsi_14", op=ScreenerOperator.LT, value=30),),
    ),
    ScreenerPreset(
        key="tw_volume_spike_above_ma",
        category=_GROUP_TECHNICAL,
        label_zh="爆量站上均線股",
        label_en="Volume spike above the 20-day MA",
        description="成交量大於20日均量2倍且收盤價高於20日均線的個股。",
        direction="bull",
        conditions=(
            PresetCondition(factor="volume_vs_20d_avg_ratio", op=ScreenerOperator.GTE, value=2),
            PresetCondition(factor="ma20_bias_pct", op=ScreenerOperator.GT, value=0),
        ),
    ),
)


# -- US --------------------------------------------------------------------------------------

_US_PRESETS: tuple[ScreenerPreset, ...] = (
    ScreenerPreset(
        key="us_undervalued_large",
        category=_GROUP_VALUATION,
        label_zh="低估值大型股",
        label_en="Undervalued large-cap",
        description="本益比偏低、股東權益報酬率相對健康的個股。",
        direction="bull",
        conditions=(
            PresetCondition(factor="pe_ratio", op=ScreenerOperator.LT, value=15),
            PresetCondition(factor="return_on_equity", op=ScreenerOperator.GTE, value=10),
        ),
    ),
    ScreenerPreset(
        key="us_growth_tech",
        category="成長 Growth",
        label_zh="成長股",
        label_en="Growth",
        description="EPS 與營收成長率皆高的個股。",
        direction="bull",
        conditions=(
            PresetCondition(factor="eps_growth", op=ScreenerOperator.GTE, value=20),
            PresetCondition(factor="revenue_growth", op=ScreenerOperator.GTE, value=15),
        ),
    ),
    ScreenerPreset(
        key="us_dividend_income",
        category="獲利 Profitability",
        label_zh="高股息股",
        label_en="Dividend income",
        description="股息殖利率偏高的個股。",
        direction="neutral",
        conditions=(PresetCondition(factor="dividend_yield", op=ScreenerOperator.GTE, value=3),),
    ),
    ScreenerPreset(
        key="us_shorted",
        category=_GROUP_OWNERSHIP,
        label_zh="高放空比例股",
        label_en="Heavily shorted",
        description="流通股放空比例偏高的個股 — 純描述性篩選，非操作建議。",
        direction="bear",
        conditions=(
            PresetCondition(factor="short_pct_of_float", op=ScreenerOperator.GTE, value=10),
        ),
    ),
    ScreenerPreset(
        key="us_garp",
        category=_GROUP_VALUATION,
        label_zh="合理價成長股 (GARP)",
        label_en="Growth at a reasonable price",
        description="PEG 比率偏低且 EPS 成長率仍高的個股 — 成長性未被高估值稀釋。",
        direction="bull",
        conditions=(
            PresetCondition(factor="peg_ratio", op=ScreenerOperator.LT, value=1.5),
            PresetCondition(factor="eps_growth", op=ScreenerOperator.GTE, value=15),
        ),
    ),
    ScreenerPreset(
        key="us_low_pb_value",
        category=_GROUP_VALUATION,
        label_zh="低股價淨值比股",
        label_en="Low price/book value",
        description="股價淨值比偏低且股東權益報酬率健康的個股 — 資產面估值相對便宜。",
        direction="bull",
        conditions=(
            PresetCondition(factor="pb_ratio", op=ScreenerOperator.LT, value=1.5),
            PresetCondition(factor="return_on_equity", op=ScreenerOperator.GTE, value=8),
        ),
    ),
    ScreenerPreset(
        key="us_institutional_favorite",
        category=_GROUP_OWNERSHIP,
        label_zh="機構高持股股",
        label_en="High institutional ownership",
        description="機構持股比例偏高的個股 — 純描述性篩選，反映機構關注度，非操作建議。",
        direction="neutral",
        conditions=(
            PresetCondition(factor="pct_held_institutions", op=ScreenerOperator.GTE, value=70),
        ),
    ),
    ScreenerPreset(
        key="us_insider_conviction",
        category=_GROUP_OWNERSHIP,
        label_zh="內部人高持股股",
        label_en="High insider ownership",
        description="內部人持股比例偏高的個股 — 常見於創辦人主導型公司，純描述性篩選。",
        direction="neutral",
        conditions=(
            PresetCondition(factor="pct_held_insiders", op=ScreenerOperator.GTE, value=10),
        ),
    ),
)


# -- ETF -------------------------------------------------------------------------------------

_ETF_PRESETS: tuple[ScreenerPreset, ...] = (
    ScreenerPreset(
        key="etf_discount",
        category="折溢價 Premium",
        label_zh="折價ETF",
        label_en="Discounted to NAV",
        description="折價交易的ETF — 淨值折價幅度大於0.5%。",
        direction="neutral",
        conditions=(
            PresetCondition(factor="premium_discount_pct", op=ScreenerOperator.LT, value=-0.5),
        ),
    ),
    ScreenerPreset(
        key="etf_large_aum",
        category="規模/受益人 Size",
        label_zh="規模前段ETF",
        label_en="Large AUM",
        description="基金規模排名前段的ETF。",
        direction="neutral",
        conditions=(PresetCondition(factor="aum", op=ScreenerOperator.GTE, value=500),),
    ),
    ScreenerPreset(
        key="etf_monthly_high_yield",
        category="配息 Distribution",
        label_zh="月月配高股息",
        label_en="Monthly high-yield distributors",
        description="月配息且近12月配息率大於5%的ETF — 依真實除息事件推算，非發行機構宣告的政策",
        direction="neutral",
        conditions=(
            PresetCondition(factor="distribution_frequency", op=ScreenerOperator.EQ, value="月配"),
            PresetCondition(factor="distribution_yield_ttm_pct", op=ScreenerOperator.GTE, value=5),
        ),
    ),
    ScreenerPreset(
        key="etf_low_cost",
        category="費用 Cost",
        label_zh="低費用率ETF",
        label_en="Low-cost ETFs",
        description="費用率(經理費+保管費)低於0.3%的ETF — 為費用率下限估算值，未包含其他營運成本",
        direction="neutral",
        conditions=(
            PresetCondition(factor="expense_ratio_pct_approx", op=ScreenerOperator.LT, value=0.3),
        ),
    ),
    ScreenerPreset(
        key="etf_prototype_only",
        category="類型 Type",
        label_zh="原型ETF",
        label_en="Plain (non-leveraged/inverse) ETFs",
        description="排除槓桿型與反向型的原型ETF — 適合不熟悉槓桿型/反向型每日再平衡機制的投資人。",
        direction="neutral",
        conditions=(PresetCondition(factor="symbol_type", op=ScreenerOperator.EQ, value="etf"),),
    ),
    ScreenerPreset(
        key="etf_strong_1y_return",
        category="報酬 Return",
        label_zh="近一年強勢ETF",
        label_en="Strong 1-year performers",
        description="近一年價格報酬大於20%的ETF。",
        direction="bull",
        conditions=(PresetCondition(factor="return_1y_pct", op=ScreenerOperator.GTE, value=20),),
    ),
    ScreenerPreset(
        key="etf_strong_1m_momentum",
        category="報酬 Return",
        label_zh="近一月動能強勢ETF",
        label_en="Strong 1-month momentum",
        description="近一月價格報酬大於10%的ETF — 純描述性短線動能觀察，非操作建議。",
        direction="bull",
        conditions=(PresetCondition(factor="return_1m_pct", op=ScreenerOperator.GTE, value=10),),
    ),
)

_BY_MARKET: dict[str, tuple[ScreenerPreset, ...]] = {
    Market.TW.value: _TW_PRESETS,
    Market.US.value: _US_PRESETS,
    "ETF": _ETF_PRESETS,
}


def get_screener_presets(market: str) -> tuple[ScreenerPreset, ...]:
    """The named presets for `market` (`"TW"`, `"US"`, `"ETF"`). An unrecognized market returns an
    empty tuple rather than raising — same degrade-honestly rule as `factor_catalog.
    get_factor_catalog`."""
    return _BY_MARKET.get(market, ())


__all__ = [
    "PresetCondition",
    "ScreenerPreset",
    "get_screener_presets",
]
