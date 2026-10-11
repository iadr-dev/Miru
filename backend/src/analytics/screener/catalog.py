"""The screener tag catalog — a fixed, enumerable list of typed tag definitions.

Code-defined, not a YAML file: this is a closed set of typed things whose keys are referenced
directly by the batch job's predicate wiring and by tests, exactly the shape the pet catalog
(`pets/config.py`) uses — an editor cannot add a tag without also wiring its predicate in code,
so a data file would only invite drift between "declared" and "computable" tags. Platform config
that operators tune without a code change (source priority, tier maps) is YAML; a catalog of tags
whose meaning IS their code predicate is not.

`data_class` on every entry is PUBLIC, including the chip-streak tags. The precedent is the
existing `get_institutional_flow` tool, which classes three-major-institutional-investor buy/sell
as PUBLIC (only the 分點/branch-level `get_chip_flows` is SPONSOR). A "外資連3買" tag is derived
purely from that same public institutional-flow dataset — a net-buy streak over the foreign/trust
category totals, never branch-level data — so it inherits the public classification of its input,
following the codebase convention that a derived read is classed by what it exposes.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import DataClass


class TagCategory(StrEnum):
    """The catalog's top-level grouping — the tap-tile sections a frontend renders. Technical,
    chip-streak, momentum (熱門), and smart-combo (智能) categories are all populated; fundamental
    and theme categories are later dispatches."""

    TECHNICAL = "技術面"
    CHIP = "籌碼面"
    HOT = "熱門"
    SMART = "智能"


class TagDefinition(BaseModel):
    """One catalog entry: a stable `key` (the query token and the flag-partition value), its
    bilingual labels, its category, a plain-prose description of what the tag means, and the
    entitlement class of the data it is derived from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    label_zh: str = Field(min_length=1)
    label_en: str = Field(min_length=1)
    category: TagCategory
    description: str = Field(min_length=1)
    data_class: DataClass = DataClass.PUBLIC


# The window for the cumulative foreign-net-buy tag. 5 trading days is the shortest window a user
# reads as "recent" 買超 without it collapsing into the strict 3-day streak tag beside it; it also
# matches the "近 5 日" phrasing users already see elsewhere in the product.
FOREIGN_CUMULATIVE_WINDOW = 5

# The streak length for the "連N買/賣" tags. 3 consecutive days is the shortest run the market
# treats as a deliberate directional stance rather than a one-off, and is the canonical "連3買"
# users ask for.
STREAK_DAYS = 3


CATALOG: tuple[TagDefinition, ...] = (
    # -- 技術面 (technical events) ------------------------------------------------------------
    TagDefinition(
        key="ma5_turning_up",
        label_zh="5日均線回升",
        label_en="5-day MA turned up",
        category=TagCategory.TECHNICAL,
        description="The 5-day moving average's slope flipped from flat-or-falling to rising as "
        "of today's close.",
    ),
    TagDefinition(
        key="ma5_turning_down",
        label_zh="5日均線轉跌",
        label_en="5-day MA turned down",
        category=TagCategory.TECHNICAL,
        description="The 5-day moving average's slope flipped from flat-or-rising to falling as "
        "of today's close.",
    ),
    TagDefinition(
        key="golden_cross_5_20",
        label_zh="5日均線黃金交叉20日",
        label_en="5/20-day golden cross",
        category=TagCategory.TECHNICAL,
        description="The 5-day moving average crossed above the 20-day moving average today, "
        "having been at or below it yesterday.",
    ),
    TagDefinition(
        key="death_cross_5_20",
        label_zh="5日均線死亡交叉20日",
        label_en="5/20-day death cross",
        category=TagCategory.TECHNICAL,
        description="The 5-day moving average crossed below the 20-day moving average today, "
        "having been at or above it yesterday.",
    ),
    TagDefinition(
        key="macd_histogram_turned_positive",
        label_zh="MACD柱翻紅",
        label_en="MACD histogram turned positive",
        category=TagCategory.TECHNICAL,
        description="The MACD histogram crossed from non-positive to positive today — the "
        "DIF/DEM momentum spread turning up through zero.",
    ),
    TagDefinition(
        key="macd_histogram_turned_negative",
        label_zh="MACD柱翻綠",
        label_en="MACD histogram turned negative",
        category=TagCategory.TECHNICAL,
        description="The MACD histogram crossed from non-negative to negative today.",
    ),
    TagDefinition(
        key="breakout_above_ma20",
        label_zh="站上20日均線",
        label_en="Closed above the 20-day MA",
        category=TagCategory.TECHNICAL,
        description="Today's close is above the 20-day moving average, having been at or below "
        "it at yesterday's close.",
    ),
    TagDefinition(
        key="kd_golden_cross",
        label_zh="日KD向上交叉",
        label_en="KD golden cross",
        category=TagCategory.TECHNICAL,
        description="The stochastic %K crossed above %D today (9/3/3), having been at or below it "
        "yesterday — momentum turning up.",
    ),
    TagDefinition(
        key="kd_death_cross",
        label_zh="日KD向下交叉",
        label_en="KD death cross",
        category=TagCategory.TECHNICAL,
        description="The stochastic %K crossed below %D today (9/3/3), having been at or above it "
        "yesterday — momentum turning down.",
    ),
    # -- 技術面 (candlestick patterns) --------------------------------------------------------
    TagDefinition(
        key="bullish_engulfing",
        label_zh="多頭吞噬",
        label_en="Bullish engulfing",
        category=TagCategory.TECHNICAL,
        description="Today's up candle's real body fully engulfs yesterday's down candle — a "
        "two-bar bullish reversal shape.",
    ),
    TagDefinition(
        key="bearish_engulfing",
        label_zh="空頭吞噬",
        label_en="Bearish engulfing",
        category=TagCategory.TECHNICAL,
        description="Today's down candle's real body fully engulfs yesterday's up candle — a "
        "two-bar bearish reversal shape.",
    ),
    TagDefinition(
        key="bullish_cannon",
        label_zh="多方炮",
        label_en="Bullish cannon (continuation)",
        category=TagCategory.TECHNICAL,
        description="Two strong up candles bracket a small-bodied middle candle, the third "
        "closing above the first — a three-bar bullish continuation shape.",
    ),
    TagDefinition(
        key="bearish_cannon",
        label_zh="空方炮",
        label_en="Bearish cannon (continuation)",
        category=TagCategory.TECHNICAL,
        description="Two strong down candles bracket a small-bodied middle candle, the third "
        "closing below the first — a three-bar bearish continuation shape.",
    ),
    TagDefinition(
        key="v_reversal",
        label_zh="V型反轉",
        label_en="V-shaped reversal",
        category=TagCategory.TECHNICAL,
        description="Over the last five bars, a declining then rising leg around a single pivot "
        "low — a V-shaped reversal off a local bottom.",
    ),
    TagDefinition(
        key="inverse_v_reversal",
        label_zh="倒V型反轉",
        label_en="Inverse-V reversal",
        category=TagCategory.TECHNICAL,
        description="Over the last five bars, a rising then declining leg around a single pivot "
        "high — an inverse-V reversal off a local top.",
    ),
    TagDefinition(
        key="three_rising",
        label_zh="紅三兵",
        label_en="Three rising candles",
        category=TagCategory.TECHNICAL,
        description="Three consecutive up candles, each with a real body and each closing higher "
        "than the last.",
    ),
    TagDefinition(
        key="three_falling",
        label_zh="黑三兵",
        label_en="Three falling candles",
        category=TagCategory.TECHNICAL,
        description="Three consecutive down candles, each with a real body and each closing lower "
        "than the last.",
    ),
    TagDefinition(
        key="doji",
        label_zh="十字線",
        label_en="Doji",
        category=TagCategory.TECHNICAL,
        description="Today's real body is negligible relative to its high-low range (open ≈ "
        "close) — a single-bar indecision shape.",
    ),
    # -- 籌碼面 (chip streaks) ----------------------------------------------------------------
    TagDefinition(
        key="foreign_net_buy_streak_3",
        label_zh="外資連3買",
        label_en="Foreign investors net-bought 3 days running",
        category=TagCategory.CHIP,
        description="Foreign-investor net buy (buy minus sell) was positive on each of the last "
        "3 consecutive trading days.",
    ),
    TagDefinition(
        key="foreign_net_sell_streak_3",
        label_zh="外資連3賣",
        label_en="Foreign investors net-sold 3 days running",
        category=TagCategory.CHIP,
        description="Foreign-investor net buy was negative on each of the last 3 consecutive "
        "trading days.",
    ),
    TagDefinition(
        key="trust_net_buy_streak_3",
        label_zh="投信連3買",
        label_en="Investment trusts net-bought 3 days running",
        category=TagCategory.CHIP,
        description="Investment-trust net buy was positive on each of the last 3 consecutive "
        "trading days.",
    ),
    TagDefinition(
        key="trust_net_sell_streak_3",
        label_zh="投信連3賣",
        label_en="Investment trusts net-sold 3 days running",
        category=TagCategory.CHIP,
        description="Investment-trust net buy was negative on each of the last 3 consecutive "
        "trading days.",
    ),
    TagDefinition(
        key="dealer_net_buy_streak_3",
        label_zh="自營商連3買",
        label_en="Dealers net-bought 3 days running",
        category=TagCategory.CHIP,
        description="Dealer (自營商自行) net buy was positive on each of the last 3 consecutive "
        "trading days.",
    ),
    TagDefinition(
        key="dealer_net_sell_streak_3",
        label_zh="自營商連3賣",
        label_en="Dealers net-sold 3 days running",
        category=TagCategory.CHIP,
        description="Dealer (自營商自行) net buy was negative on each of the last 3 consecutive "
        "trading days.",
    ),
    TagDefinition(
        key="foreign_net_buy_5d_cumulative",
        label_zh="近5日外資買超",
        label_en="Foreign investors net buyers over the last 5 days",
        category=TagCategory.CHIP,
        description="Foreign-investor net buy summed over the last 5 trading days is positive — "
        "a cumulative window, tolerating an individual down day inside it.",
    ),
    TagDefinition(
        key="margin_purchase_balance_streak_up_3",
        label_zh="融資餘額連3日增加",
        label_en="Margin-purchase balance rose 3 days running",
        category=TagCategory.CHIP,
        description="Today's 融資 (margin-purchase) balance was higher than the day before, on "
        "each of the last 3 consecutive trading days.",
    ),
    TagDefinition(
        key="short_sale_balance_streak_down_3",
        label_zh="融券餘額連3日減少",
        label_en="Short-sale balance fell 3 days running",
        category=TagCategory.CHIP,
        description="Today's 融券 (short-sale) balance was lower than the day before, on each of "
        "the last 3 consecutive trading days.",
    ),
    TagDefinition(
        key="small_holder_share_rising_3w",
        label_zh="持股100張以下比例連3週增加",
        label_en="Sub-100-lot holder share rose 3 weeks running",
        category=TagCategory.CHIP,
        description="The share of custody held by holders with fewer than 100 張 (100,000 "
        "shares) rose in each of the last 3 weekly 集保 snapshots — Sponsor-tier.",
        data_class=DataClass.SPONSOR,
    ),
    TagDefinition(
        key="large_holder_share_rising_3w",
        label_zh="持股1000張以上比例連3週增加",
        label_en="1,000+-lot holder share rose 3 weeks running",
        category=TagCategory.CHIP,
        description="The share of custody held by holders with 1,000 張 (1,000,000 shares) or "
        "more rose in each of the last 3 weekly 集保 snapshots — Sponsor-tier.",
        data_class=DataClass.SPONSOR,
    ),
    # -- 熱門 (momentum: volume surge, price extremes) ---------------------------------------
    TagDefinition(
        key="volume_surge",
        label_zh="成交量爆量",
        label_en="Volume surge",
        category=TagCategory.HOT,
        description="Today's trading volume is at least 2x the average of the prior 20 trading "
        "days — a conventional 爆量 threshold flagging a visible spike in activity.",
    ),
    TagDefinition(
        key="volume_contraction",
        label_zh="成交量量縮",
        label_en="Volume contraction",
        category=TagCategory.HOT,
        description="Today's trading volume is at most half the average of the prior 20 trading "
        "days — a conventional 量縮 threshold flagging visibly reduced activity.",
    ),
    TagDefinition(
        key="new_52w_high",
        label_zh="創52週新高",
        label_en="New 52-week high",
        category=TagCategory.HOT,
        description="Today's close is the highest close over the trailing 252 trading days "
        "(~52 weeks), including today.",
    ),
    TagDefinition(
        key="new_52w_low",
        label_zh="創52週新低",
        label_en="New 52-week low",
        category=TagCategory.HOT,
        description="Today's close is the lowest close over the trailing 252 trading days "
        "(~52 weeks), including today.",
    ),
    # -- 智能 (smart combos: pre-bundled AND-combinations of existing tags) -------------------
    TagDefinition(
        key="smart_breakout_volume",
        label_zh="量增突破",
        label_en="Volume-confirmed breakout",
        category=TagCategory.SMART,
        description="A shortcut for '站上20日均線' AND '成交量爆量' together — a breakout that is "
        "also backed by a visible volume spike on the same day, rather than a thin-volume move.",
    ),
    TagDefinition(
        key="smart_reversal_chip",
        label_zh="籌碼確認反轉",
        label_en="Chip-confirmed reversal",
        category=TagCategory.SMART,
        description="A shortcut for 'V型反轉' AND '近5日外資買超' together — a V-shaped bottom "
        "that coincides with foreign investors having been net buyers over the same recent window.",
    ),
    TagDefinition(
        key="smart_trend_continuation",
        label_zh="趨勢續強",
        label_en="Trend continuation",
        category=TagCategory.SMART,
        description="A shortcut for '紅三兵' AND '5日均線黃金交叉20日' together — three rising "
        "candles landing on the same day the 5-day MA crosses above the 20-day MA, a stronger "
        "continuation signal than either alone.",
    ),
    TagDefinition(
        key="smart_new_high_chip",
        label_zh="新高有法人",
        label_en="New high with institutional support",
        category=TagCategory.SMART,
        description="A shortcut for '創52週新高' AND '外資連3買' together — a fresh 52-week high "
        "made while foreign investors have been net-buying for 3 consecutive days, distinguishing "
        "an institutionally-backed high from an unsupported spike.",
    ),
)

# Pre-bundled 智能 combo tags: each maps to the set of already-catalogued tags that must ALL be
# present in a symbol's already-assembled flag list for the combo to fire. Evaluated as a pure
# set-intersection check AFTER the underlying tags for that symbol are computed (see
# `batch.py`'s `_smart_flags`), never by re-deriving the underlying booleans — this makes a smart
# tag only ever as correct as its already-tested ingredients.
SMART_COMBOS: dict[str, frozenset[str]] = {
    "smart_breakout_volume": frozenset({"breakout_above_ma20", "volume_surge"}),
    "smart_reversal_chip": frozenset({"v_reversal", "foreign_net_buy_5d_cumulative"}),
    "smart_trend_continuation": frozenset({"three_rising", "golden_cross_5_20"}),
    "smart_new_high_chip": frozenset({"new_52w_high", "foreign_net_buy_streak_3"}),
}

_BY_KEY: dict[str, TagDefinition] = {entry.key: entry for entry in CATALOG}


def all_tags() -> tuple[TagDefinition, ...]:
    """The full catalog, in declaration order (technical events first, then chip streaks)."""
    return CATALOG


def tag_keys() -> frozenset[str]:
    """Every known tag key — the batch job iterates this to know what to evaluate, and the read
    seam checks requested tokens against it to report unknown ones."""
    return frozenset(_BY_KEY)


__all__ = [
    "CATALOG",
    "FOREIGN_CUMULATIVE_WINDOW",
    "SMART_COMBOS",
    "STREAK_DAYS",
    "TagCategory",
    "TagDefinition",
    "all_tags",
    "tag_keys",
]
