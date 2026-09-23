"""The lean multi-factor descriptive score — pure computation, no LLM.

**Governance framing (load-bearing for this whole module):** the composite and its band are a
*descriptive read of what the factors currently show*, never a trading instruction. No function
here may return, or be renamed to imply, a buy/sell/hold word, a price target, or a position
size. `ScoreResult`/`ScoreBand`/`FactorScore` (analytics/models.py) are the only shapes this
module produces, and none of their fields can hold that kind of value.

**Two factor sets, branched by `SymbolType`** (a plain equity vs. any ETF need genuinely
different factors, not the same five metrics applied uniformly and hoping the inapplicable ones
degrade to unavailable — see `analytics/models.py`'s `StockScoreBreakdown`/`EtfScoreBreakdown`
docstrings for the full reasoning):

**Stock factor coverage** (five-factor design):

- **Momentum / RS** — computable; `price_eod` is ingested daily. Measured as the verified 12-1
  convention: the trailing twelve-month total return excluding the most recent month, so a
  short-term reversal in the latest weeks does not contaminate the intermediate-term signal.
- **Trend quality** — computable; a Minervini-style trend-template count over `price_eod`.
- **Value guard** — computable when the financial-statement + market-cap datasets are ingested
  for the symbol: EV/EBITDA and FCF yield (primary), P/E and P/B (secondary). Each sub-metric
  degrades independently; the factor is unavailable only if none can be assembled.
- **Quality** — computable when the financial-statement datasets are ingested: ROE, debt/equity
  (a penalty direction), earnings variability (a penalty direction), plus ROIC and gross
  profitability where the more data-hungry line items are present. Sub-metric-by-sub-metric
  graceful degradation, same convention.
- **Revenue / flow (TW)** — partially available. The 三大法人 sub-signal is computable from
  `institutional_flow`; the 月營收 YoY sub-signal is unavailable (monthly revenue isn't ingested
  yet). The factor is reported from the chip sub-signal alone, with that gap disclosed in its
  description — never silently backfilled.

**ETF factor coverage** (four-factor design — see `compute_etf_factor_breakdown`):

- **Momentum** — same 12-1 calculation as the stock breakdown, meaningful as fund performance.
- **Premium/discount** — TWSE's own published 預估折溢價幅度 (`sources/twse_etf_nav.py`), the
  textbook ETF-quality metric with no stock equivalent.
- **Cost** — SITCA-disclosed expense ratio percentile (lower cost ranks higher).
- **Management activity** — holdings-turnover-derived, active ETFs only; genuinely unavailable
  for passive/leveraged/inverse ETFs, which have no holdings data ingested.
- **Liquidity/flow** — 三大法人 net-buy trend, the same chip sub-signal the stock breakdown's
  `revenue_flow` uses.

**Composite construction.** Momentum and the chip sub-signal rank cross-sectionally as winsorized
percentiles; value and quality (stock-only) are built the way real factor products build
composites — winsorize each raw metric, z-score it against a comparison universe (sector peers
where an ingested sector classification exists, else the whole universe with that disclosed),
equal-weight the available sub-metric z-scores within the factor, and map the combined z-score
onto the same 0-100 axis the blend already speaks.

An unavailable factor is `FactorScore(percentile=None, unavailable_reason=...)` — it is dropped
from the composite blend (weights renormalized over the factors that *are* available) and its
name is listed in `ScoreResult.partial_factors`, never defaulted to a fake neutral value that
would silently bias the blend one way or the other.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date as date_

from analytics.fundamentals import FundamentalMetrics
from analytics.models import (
    EtfScoreBreakdown,
    FactorScore,
    ScoreBand,
    ScoreProfile,
    ScoreResult,
    StockScoreBreakdown,
)
from analytics.normalization import combine_zscores, zscore, zscore_to_percentile
from analytics.symbol_universe import SymbolType
from schemas.capabilities import EODBar, InstitutionalFlowRow
from schemas.enums import Market

_NON_TRADING_CAVEAT = (
    "僅為描述性因子分析——非買進、賣出、持有或價格建議；以最近一次收盤後計算，可能未反映盤中變動。"
)
_REGIME_CAVEAT_NO_MARKET_DATA = (
    "本次計算未評估大盤情境（如TAIEX與其200日均線的關係）——"
    "任何受動能驅動的因子，請以未經評估市場情境的同等反轉風險視之。"
)

# Horizon-aware weight profiles — weights sum to 1.0 within each profile before any
# renormalization for missing factors.
_STOCK_WEIGHTS: dict[ScoreProfile, dict[str, float]] = {
    ScoreProfile.SWING: {
        "momentum": 0.35,
        "trend_quality": 0.25,
        "value_guard": 0.10,
        "quality": 0.10,
        "revenue_flow": 0.20,
    },
    ScoreProfile.POSITION: {
        "momentum": 0.15,
        "trend_quality": 0.15,
        "value_guard": 0.25,
        "quality": 0.30,
        "revenue_flow": 0.15,
    },
}

# ETF weight profiles — no value/quality/trend-template (see the module docstring's ETF section
# for why those stock factors are dropped rather than left to degrade to unavailable). Swing
# leans on momentum/liquidity (the fastest-moving signals for a fund); position leans on
# cost/premium-discount (structural cost drag and mispricing matter more over a longer hold).
_ETF_WEIGHTS: dict[ScoreProfile, dict[str, float]] = {
    ScoreProfile.SWING: {
        "momentum": 0.40,
        "premium_discount": 0.20,
        "cost": 0.10,
        "management_activity": 0.10,
        "liquidity_flow": 0.20,
    },
    ScoreProfile.POSITION: {
        "momentum": 0.20,
        "premium_discount": 0.25,
        "cost": 0.25,
        "management_activity": 0.15,
        "liquidity_flow": 0.15,
    },
}

# 12-1 momentum: the trailing twelve-month total return measured up to one month ago, excluding
# the most recent month. Both windows are in trading days (~21/month, ~252/year) — the verified
# academic convention that avoids the short-term reversal contaminating the most recent weeks.
_MOMENTUM_LOOKBACK_DAYS = 252  # ~12 months — the far anchor
_MOMENTUM_SKIP_MONTH_DAYS = 21  # ~1 trading month excluded — the near anchor
_TREND_TEMPLATE_LOOKBACK = 252  # ~52 weeks, needed for the 52w-high/low + MA200-slope conditions
_TREND_TEMPLATE_MONTH_LOOKBACK = 22  # ~1 trading month, for the "MA200 rising" condition
_OVER_EXTENSION_THRESHOLD = 0.20  # > ~20% above the 50-day MA
_OVER_EXTENSION_CAP = 70.0  # momentum percentile is capped, not zeroed, when over-extended
_WINSORIZE_SIGMA = 3.0
_MIN_CROSS_SECTION_SIZE = 2  # smallest universe size a percentile rank is meaningful over
_FIFTY_TWO_WEEK_LOW_MULTIPLE = 1.30  # >= 30% above the 52-week low
_FIFTY_TWO_WEEK_HIGH_MULTIPLE = 0.75  # <= 25% below the 52-week high
_RS_PERCENTILE_THRESHOLD = 70.0  # trend-template condition #8: RS percentile >= 70
_TREND_TEMPLATE_CONDITION_COUNT = 8.0
_BULLISH_LEANING_THRESHOLD = 60.0
_BEARISH_LEANING_THRESHOLD = 40.0


def twelve_minus_one_momentum(closes: Sequence[float]) -> float | None:
    """The 12-1 momentum: total return from ~12 months ago to ~1 month ago (the most recent
    month excluded to avoid short-term-reversal contamination). `None` if there isn't a full
    twelve months of history — never a partial-window estimate silently swapped in."""
    required = _MOMENTUM_LOOKBACK_DAYS + 1
    if len(closes) < required:
        return None
    near = closes[-1 - _MOMENTUM_SKIP_MONTH_DAYS]
    far = closes[-1 - _MOMENTUM_LOOKBACK_DAYS]
    if far == 0:
        return None
    return (near - far) / far


def trend_template_score(
    closes: Sequence[float],
    ma_series: Mapping[str, Sequence[float | None]],
    *,
    rs_percentile: float | None = None,
) -> int | None:
    """Count of the Minervini trend-template conditions met (0-8). `ma_series` must contain
    `"ma50"`, `"ma150"`, `"ma200"` aligned index-for-index with `closes`. `None` if there isn't
    enough history for the 52-week conditions. The RS condition (#8) is counted as *not met*
    (not "unavailable") when `rs_percentile` isn't supplied — a conservative, disclosed choice,
    never an inflated count."""
    if len(closes) < _TREND_TEMPLATE_LOOKBACK:
        return None
    ma50 = ma_series["ma50"][-1]
    ma150 = ma_series["ma150"][-1]
    ma200 = ma_series["ma200"][-1]
    ma200_prior = (
        ma_series["ma200"][-_TREND_TEMPLATE_MONTH_LOOKBACK]
        if len(ma_series["ma200"]) >= _TREND_TEMPLATE_MONTH_LOOKBACK
        else None
    )
    if ma50 is None or ma150 is None or ma200 is None or ma200_prior is None:
        return None

    price = closes[-1]
    window = closes[-_TREND_TEMPLATE_LOOKBACK:]
    fifty_two_week_low = min(window)
    fifty_two_week_high = max(window)

    conditions = [
        price > ma150 and price > ma200,
        ma150 > ma200,
        ma200 > ma200_prior,
        ma50 > ma150 and ma50 > ma200,
        price > ma50,
        fifty_two_week_low > 0 and price >= _FIFTY_TWO_WEEK_LOW_MULTIPLE * fifty_two_week_low,
        fifty_two_week_high > 0 and price >= _FIFTY_TWO_WEEK_HIGH_MULTIPLE * fifty_two_week_high,
        rs_percentile is not None and rs_percentile >= _RS_PERCENTILE_THRESHOLD,
    ]
    return sum(1 for met in conditions if met)


def net_buy_20d_trend(
    flow_rows: Sequence[InstitutionalFlowRow],
    *,
    investors: tuple[str, ...] = ("Foreign_Investor", "Investment_Trust"),
) -> float | None:
    """Sum of (buy - sell) for the given investor categories over whatever trading days are
    present in `flow_rows` (callers pass the trailing-20-trading-day slice). `None` if no rows
    at all are present for this symbol/window — genuinely no data, not a zero."""
    matching = [row for row in flow_rows if row.investor in investors]
    if not matching:
        return None
    return float(sum(row.buy - row.sell for row in matching))


def winsorized_percentile(value: float, universe: Sequence[float]) -> float | None:
    """Cross-sectional percentile rank of `value` within `universe`, winsorizing the universe
    first (clip to +/- 3 standard deviations) so a single fat-tailed outlier can't compress
    everyone else's rank. `None` if the universe is too small for a meaningful cross-section."""
    if len(universe) < _MIN_CROSS_SECTION_SIZE:
        return None
    mean = statistics.fmean(universe)
    stdev = statistics.pstdev(universe)
    if stdev == 0:
        return 50.0
    low, high = mean - _WINSORIZE_SIGMA * stdev, mean + _WINSORIZE_SIGMA * stdev
    clipped = [min(max(v, low), high) for v in universe]
    clipped_value = min(max(value, low), high)
    rank = sum(1 for v in clipped if v <= clipped_value)
    return 100.0 * rank / len(clipped)


def apply_over_extension_cap(
    momentum_percentile: float | None,
    *,
    price: float | None,
    ma50: float | None,
) -> tuple[float | None, bool]:
    """A stock trading far above its 50-day MA has its momentum contribution capped rather than
    left to rank at the very top — a naive momentum score ranks the most stretched names
    highest, right before mean-reversion. Returns `(possibly-capped percentile, was_capped)`."""
    if momentum_percentile is None or price is None or ma50 is None or ma50 <= 0:
        return momentum_percentile, False
    extension = (price - ma50) / ma50
    if extension > _OVER_EXTENSION_THRESHOLD and momentum_percentile > _OVER_EXTENSION_CAP:
        return _OVER_EXTENSION_CAP, True
    return momentum_percentile, False


def band_from_composite(composite: float | None) -> ScoreBand:
    """Map the composite to a descriptive band (bullish-leaning / neutral / bearish-leaning) —
    never to an action word."""
    if composite is None:
        return ScoreBand.NEUTRAL
    if composite >= _BULLISH_LEANING_THRESHOLD:
        return ScoreBand.BULLISH_LEANING
    if composite <= _BEARISH_LEANING_THRESHOLD:
        return ScoreBand.BEARISH_LEANING
    return ScoreBand.NEUTRAL


def _factors_by_name(
    breakdown: StockScoreBreakdown | EtfScoreBreakdown,
) -> dict[str, FactorScore]:
    """Every named factor on `breakdown`, keyed by its own field name — the one place that maps
    a breakdown model's fields to a plain dict, so `blend_composite`/`build_score_result` work
    identically over either shape instead of hand-listing five attribute names twice."""
    return dict(breakdown)


def blend_composite(
    breakdown: StockScoreBreakdown | EtfScoreBreakdown, profile: ScoreProfile
) -> tuple[float | None, tuple[str, ...]]:
    """Weighted blend over *available* factors only, renormalized so the available weights sum
    to 1.0 — computed over whichever factors are present, with the gap always disclosed rather
    than silently degraded. Returns `(composite_or_None, names_of_unavailable_factors)`."""
    weight_profiles = _ETF_WEIGHTS if isinstance(breakdown, EtfScoreBreakdown) else _STOCK_WEIGHTS
    weights = weight_profiles[profile]
    factors = _factors_by_name(breakdown)
    available = {name: f for name, f in factors.items() if f.percentile is not None}
    partial = tuple(name for name in factors if name not in available)
    if not available:
        return None, partial

    total_weight = sum(weights[name] for name in available)
    if total_weight <= 0:
        return None, partial

    weighted_total = 0.0
    for name, factor in available.items():
        percentile = factor.percentile
        if percentile is None:  # pragma: no cover — excluded by the `available` filter above
            continue
        weighted_total += weights[name] * percentile
    composite = weighted_total / total_weight
    # A weighted average of values each already clamped to [0, 100] cannot mathematically exceed
    # that range, but summing several float weight*percentile terms can land a hair outside it
    # (e.g. 100.00000000000001) from ordinary floating-point representation error — clip rather
    # than let that render as a validation failure on `ScoreResult.composite`'s `le=100` bound.
    composite = min(max(composite, 0.0), 100.0)
    return composite, partial


def normalization_caveat(
    *, sector_relative: bool, sector_label: str | None, has_fundamentals: bool
) -> str:
    """Disclose exactly how the fundamental factors were standardized — sector-relative when an
    ingested classification placed the symbol among peers, whole-universe (explicitly coarser)
    when none was available, and a plain no-comparison note when no fundamental universe was
    supplied at all. Never silently presents a whole-universe z-score as sector-relative."""
    if not has_fundamentals:
        return _NO_FUNDAMENTAL_NORMALIZATION_CAVEAT
    if sector_relative and sector_label:
        return _SECTOR_RELATIVE_CAVEAT.format(sector=sector_label)
    return _WHOLE_UNIVERSE_CAVEAT


@dataclass(frozen=True)
class SectorContext:
    """How the fundamental factors were normalized for this run — whether the comparison universe
    was sector peers and, if so, which sector. Defaults describe the whole-universe fallback."""

    sector_relative: bool = False
    sector_label: str | None = None


def build_score_result(  # noqa: PLR0913 — one keyword-only field per result dimension it stitches
    *,
    symbol: str,
    market: Market,
    as_of: date_,
    profile: ScoreProfile,
    symbol_type: SymbolType,
    breakdown: StockScoreBreakdown | EtfScoreBreakdown,
    sector_context: SectorContext | None = None,
) -> ScoreResult:
    """Assemble the final, fully-caveated `ScoreResult` — the one place composite + band +
    caveats are stitched together, so no call site can accidentally ship one without the
    others."""
    composite, partial_factors = blend_composite(breakdown, profile)
    band = band_from_composite(composite)
    # Sector-relative z-scoring only ever applies to the stock breakdown's value/quality factors
    # — every ETF factor is a winsorized percentile (like momentum), never a sector-normalized
    # z-score, so an ETF result always reports "no fundamental comparison universe" rather than
    # a sector/whole-universe caveat that would misleadingly imply one was attempted.
    has_fundamentals = isinstance(breakdown, StockScoreBreakdown) and (
        breakdown.value_guard.percentile is not None or breakdown.quality.percentile is not None
    )
    sector = sector_context or SectorContext()
    return ScoreResult(
        symbol=symbol,
        market=market,
        as_of=as_of,
        profile=profile,
        symbol_type=symbol_type,
        composite=composite,
        band=band,
        breakdown=breakdown,
        partial_factors=partial_factors,
        regime_caveat=_REGIME_CAVEAT_NO_MARKET_DATA,
        non_trading_caveat=_NON_TRADING_CAVEAT,
        normalization_caveat=normalization_caveat(
            sector_relative=sector.sector_relative,
            sector_label=sector.sector_label,
            has_fundamentals=has_fundamentals,
        ),
    )


def unavailable_factor(name: str, reason: str) -> FactorScore:
    """A factor that is genuinely not computable — explicit and reasoned, never a fabricated
    neutral placeholder."""
    return FactorScore(
        name=name,
        percentile=None,
        description=f"此標的/日期尚無「{name}」資料。",
        unavailable_reason=reason,
    )


_VALUE_GUARD_UNAVAILABLE_REASON = (
    "無法組成任何價值防護子指標（EV/EBITDA、自由現金流殖利率、本益比、股價淨值比）——"
    "此標的所需的財報及/或市值資料尚未收錄；不會以虛構數值替代。"
)
_QUALITY_UNAVAILABLE_REASON = (
    "無法組成任何品質子指標（ROE、負債權益比、盈餘穩定性、ROIC、毛利率）——"
    "此標的的財報資料尚未收錄；不會以虛構數值替代。"
)


@dataclass(frozen=True)
class _SubMetric:
    """One fundamental sub-metric's contribution to a z-scored factor: the attribute on
    `FundamentalMetrics` to read, whether a higher raw value is stronger (penalty-direction
    metrics like debt/equity, EV/EBITDA, and earnings variability set this `False`), and the
    label surfaced in the factor description."""

    attr: str
    higher_is_better: bool
    label: str


# Equal-weight within each factor (the standard within-factor combination). Sign encodes
# direction so a lower-is-better metric still pushes a strong factor score upward.
_QUALITY_SUB_METRICS: tuple[_SubMetric, ...] = (
    _SubMetric("roe", higher_is_better=True, label="ROE"),
    _SubMetric("debt_to_equity", higher_is_better=False, label="負債權益比"),
    _SubMetric("earnings_variability", higher_is_better=False, label="盈餘穩定性"),
    _SubMetric("roic", higher_is_better=True, label="ROIC"),
    _SubMetric("gross_profitability", higher_is_better=True, label="毛利率"),
)
_VALUE_SUB_METRICS: tuple[_SubMetric, ...] = (
    _SubMetric("ev_ebitda", higher_is_better=False, label="EV/EBITDA"),
    _SubMetric("fcf_yield", higher_is_better=True, label="自由現金流殖利率"),
    _SubMetric("pe", higher_is_better=False, label="本益比"),
    _SubMetric("pb", higher_is_better=False, label="股價淨值比"),
)

_SECTOR_RELATIVE_CAVEAT = "價值防護與品質因子已對同業同儕（{sector}）進行 z-score 標準化。"
_WHOLE_UNIVERSE_CAVEAT = (
    "尚無可用的產業分類資料，因此價值防護與品質因子改以全市場為比較基準進行 z-score 標準化，"
    "而非同業同儕——此為較粗略的標準化方式，特此揭露而非以同業比較呈現。"
)
_NO_FUNDAMENTAL_NORMALIZATION_CAVEAT = (
    "價值防護與品質因子本次未進行橫斷面標準化——本次執行未提供基本面比較基準。"
)


@dataclass(frozen=True)
class FactorInputs:
    """The raw, already-computed per-symbol series + cross-sectional universes
    `compute_factor_breakdown` needs — bundled into one value so the function itself stays a
    single-parameter call, no I/O, no store dependency. A caller (typically an async
    orchestrator that read the derived Parquet store) is responsible for assembling this.

    `fundamentals` is this symbol's raw fundamental metrics; `quality_universe`/`value_universe`
    map each sub-metric name to the comparison universe's raw values for that sub-metric (sector
    peers where `sector_relative`, else the whole universe). All default empty/None so a
    price-only caller degrades value/quality to unavailable exactly as before."""

    closes: Sequence[float]
    ma_series: Mapping[str, Sequence[float | None]]
    momentum_universe: Sequence[float]
    flow_value: float | None
    flow_universe: Sequence[float]
    price: float | None
    ma50: float | None
    fundamentals: FundamentalMetrics | None = None
    quality_universe: Mapping[str, Sequence[float]] = field(default_factory=lambda: {})
    value_universe: Mapping[str, Sequence[float]] = field(default_factory=lambda: {})
    sector_relative: bool = False
    sector_label: str | None = None


def _fundamental_factor(
    name: str,
    metrics: FundamentalMetrics | None,
    sub_metrics: Sequence[_SubMetric],
    universe: Mapping[str, Sequence[float]],
    unavailable_reason: str,
) -> FactorScore:
    """Build one z-scored fundamental factor: winsorize + z-score each present sub-metric against
    its comparison universe, equal-weight-combine the available ones, map to 0-100. Each
    sub-metric that this symbol lacks (or that has too small a universe) is simply left out — the
    factor degrades sub-metric-by-sub-metric, and is unavailable only if none survive."""
    if metrics is None:
        return unavailable_factor(name, unavailable_reason)
    sub_scores: list[float | None] = []
    used: list[str] = []
    for sub in sub_metrics:
        raw = getattr(metrics, sub.attr)
        if raw is None:
            continue
        z = zscore(raw, universe.get(sub.attr, ()), higher_is_better=sub.higher_is_better)
        if z is None:
            continue
        sub_scores.append(z)
        used.append(sub.label)
    combined = combine_zscores(sub_scores)
    if combined is None:
        return unavailable_factor(name, unavailable_reason)
    annualized_note = (
        "（近期數據為單季年化估算，完整TTM尚未可得）" if metrics.ttm_is_annualized else ""
    )
    return FactorScore(
        name=name,
        percentile=zscore_to_percentile(combined),
        description=(
            f"{'、'.join(used)} 之 z-score 綜合指標，對應至 0-100 橫斷面排名{annualized_note}"
        ),
    )


def _momentum_factor(inputs: FactorInputs) -> tuple[FactorScore, float | None]:
    """The 12-1 momentum factor — identical calculation for both the stock and ETF breakdowns
    (see the module docstring's ETF section: momentum is meaningful as fund performance too).
    Returns `(factor, momentum_percentile)` since the stock breakdown's trend-template also
    needs the raw percentile for its own RS condition (#8)."""
    raw_momentum = twelve_minus_one_momentum(inputs.closes)
    momentum_percentile = (
        winsorized_percentile(raw_momentum, inputs.momentum_universe)
        if raw_momentum is not None
        else None
    )
    momentum_percentile, was_capped = apply_over_extension_cap(
        momentum_percentile, price=inputs.price, ma50=inputs.ma50
    )
    momentum = (
        FactorScore(
            name="momentum",
            percentile=momentum_percentile,
            description=(
                "12-1 動能（排除最近一個月的trailing 12個月報酬），對當日全市場排名"
                + ("（因與50日均線乖離過大而調降上限）" if was_capped else "")
            ),
        )
        if momentum_percentile is not None
        else unavailable_factor("momentum", "此標的已收錄的價格歷史不足12個月")
    )
    return momentum, momentum_percentile


def _liquidity_flow_factor(inputs: FactorInputs) -> FactorScore:
    """三大法人 net-buy trend, the same chip sub-signal the stock breakdown's `revenue_flow` uses
    — shared verbatim by the ETF breakdown's `liquidity_flow`, since it's the same real,
    ETF-applicable signal either way (see the module docstring's ETF section)."""
    flow_percentile = (
        winsorized_percentile(inputs.flow_value, inputs.flow_universe)
        if inputs.flow_value is not None
        else None
    )
    return (
        FactorScore(
            name="liquidity_flow",
            percentile=flow_percentile,
            description="三大法人 20日淨買超趨勢百分位",
        )
        if flow_percentile is not None
        else unavailable_factor("liquidity_flow", "此標的/期間尚無三大法人買賣超資料")
    )


def compute_stock_factor_breakdown(inputs: FactorInputs) -> StockScoreBreakdown:
    """Build the five-factor stock breakdown from `inputs`. Pure computation — no I/O, no
    dependency on the ingest store."""
    closes = inputs.closes
    ma_series = inputs.ma_series
    momentum, momentum_percentile = _momentum_factor(inputs)

    raw_trend = trend_template_score(closes, ma_series, rs_percentile=momentum_percentile)
    trend_quality = (
        FactorScore(
            name="trend_quality",
            percentile=(raw_trend / _TREND_TEMPLATE_CONDITION_COUNT) * 100.0,
            description=f"{raw_trend}/8 項 Minervini 趨勢模板條件達標",
        )
        if raw_trend is not None
        else unavailable_factor("trend_quality", "此標的已收錄的價格歷史不足252個交易日")
    )

    value_guard = _fundamental_factor(
        "value_guard",
        inputs.fundamentals,
        _VALUE_SUB_METRICS,
        inputs.value_universe,
        _VALUE_GUARD_UNAVAILABLE_REASON,
    )
    quality = _fundamental_factor(
        "quality",
        inputs.fundamentals,
        _QUALITY_SUB_METRICS,
        inputs.quality_universe,
        _QUALITY_UNAVAILABLE_REASON,
    )

    flow_percentile = (
        winsorized_percentile(inputs.flow_value, inputs.flow_universe)
        if inputs.flow_value is not None
        else None
    )
    revenue_flow = (
        FactorScore(
            name="revenue_flow",
            percentile=flow_percentile,
            description=(
                "僅反映三大法人 20日淨買超趨勢百分位 — 月營收 YoY 子訊號尚未納入計算，"
                "因子目前僅涵蓋籌碼面設計的一半，特此揭露"
            ),
        )
        if flow_percentile is not None
        else unavailable_factor("revenue_flow", "此標的/期間尚無三大法人買賣超資料")
    )

    return StockScoreBreakdown(
        momentum=momentum,
        trend_quality=trend_quality,
        value_guard=value_guard,
        quality=quality,
        revenue_flow=revenue_flow,
    )


# Premium/discount is not cross-sectionally ranked the way momentum/flow are — TWSE's own
# published 預估折溢價幅度 is already a bounded, directly-comparable percentage, so mapping it to
# a 0-100 factor score is a fixed linear scale (closer to 0% deviation = higher score) rather
# than a percentile-against-the-day's-universe rank. `_PREMIUM_DISCOUNT_SCALE_PCT` bounds the
# scale: a deviation at or beyond this magnitude (either direction) maps to the worst score (0);
# exact NAV parity (0% deviation) maps to the best score (100). 5% comfortably covers ordinary
# TW ETF premium/discount ranges (most trade within ~1-2%) while still discriminating a genuinely
# mispriced fund, without needing a cross-sectional universe (a single ETF's own deviation is a
# self-contained, absolute measure — nothing about it depends on ranking against other ETFs).
_PREMIUM_DISCOUNT_SCALE_PCT = 5.0


def _premium_discount_factor(premium_discount_pct: float | None) -> FactorScore:
    """Score TWSE's own published premium/discount % on a fixed [0, 5]% deviation scale — see
    `_PREMIUM_DISCOUNT_SCALE_PCT`'s own comment for why this is a fixed scale, not a
    cross-sectional percentile."""
    if premium_discount_pct is None:
        return unavailable_factor("premium_discount", "此標的/日期尚無ETF淨值折溢價資料")
    deviation = abs(premium_discount_pct)
    score = max(0.0, 100.0 * (1.0 - deviation / _PREMIUM_DISCOUNT_SCALE_PCT))
    if premium_discount_pct > 0:
        direction = "溢價"
    elif premium_discount_pct < 0:
        direction = "折價"
    else:
        direction = "平價"
    return FactorScore(
        name="premium_discount",
        percentile=score,
        description=(
            f"對淨值{direction} {abs(premium_discount_pct):.2f}%（TWSE公告估算值），"
            f"採固定 0-{_PREMIUM_DISCOUNT_SCALE_PCT:.0f}% 偏離度計分——越接近淨值平價分數越高；"
            "不與其他ETF比較排名"
        ),
    )


def _cost_factor(expense_ratio_pct: float | None, universe: Sequence[float]) -> FactorScore:
    """Lower expense ratio scores higher — a cross-sectional percentile like momentum/flow,
    since "cheap relative to other ETFs" is exactly the comparison an investor makes."""
    if expense_ratio_pct is None:
        return unavailable_factor("cost", "此標的尚無 SITCA 費用率揭露資料")
    # Lower is better: winsorized_percentile ranks ascending, so negate to rank descending.
    percentile = winsorized_percentile(-expense_ratio_pct, [-v for v in universe])
    if percentile is None:
        return unavailable_factor("cost", "今日揭露費用率的ETF數量不足2檔")
    return FactorScore(
        name="cost",
        percentile=percentile,
        description=(
            f"費用率 {expense_ratio_pct:.2f}%，於今日ETF全體中由低到高排名（費用越低分數越高）"
        ),
    )


def _management_activity_factor(
    turnover_value: float | None, universe: Sequence[float], *, is_active: bool
) -> FactorScore:
    """Holdings-turnover-derived — ACTIVE ETFs only. A passive/leveraged/inverse ETF has no
    holdings data ingested (`ActiveEtfHoldingChangeRow` is an active-ETF-only FinMind capability),
    so this is genuinely unavailable for them, never fabricated from a proxy."""
    if not is_active:
        return unavailable_factor(
            "management_activity", "非主動式ETF——被動/槓桿/反向ETF尚無持股變動資料收錄"
        )
    if turnover_value is None:
        return unavailable_factor("management_activity", "此標的尚無主動式ETF持股變動資料")
    percentile = winsorized_percentile(turnover_value, universe)
    if percentile is None:
        return unavailable_factor("management_activity", "今日有持股變動資料的主動式ETF數量不足2檔")
    return FactorScore(
        name="management_activity",
        percentile=percentile,
        description="於今日主動式ETF全體中的持股周轉率百分位",
    )


@dataclass(frozen=True)
class EtfFactorInputs:
    """The raw, already-computed per-symbol series + cross-sectional universes
    `compute_etf_factor_breakdown` needs — the ETF-breakdown counterpart to `FactorInputs`.
    `closes`/`momentum_universe`/`price`/`ma50`/`flow_value`/`flow_universe` are the same shape
    momentum/liquidity-flow already use for stocks; the remaining fields are ETF-specific."""

    closes: Sequence[float]
    momentum_universe: Sequence[float]
    flow_value: float | None
    flow_universe: Sequence[float]
    price: float | None
    ma50: float | None
    premium_discount_pct: float | None = None
    expense_ratio_pct: float | None = None
    expense_ratio_universe: Sequence[float] = field(default_factory=tuple)
    is_active_etf: bool = False
    holdings_turnover: float | None = None
    holdings_turnover_universe: Sequence[float] = field(default_factory=tuple)


def compute_etf_factor_breakdown(inputs: EtfFactorInputs) -> EtfScoreBreakdown:
    """Build the four-factor ETF breakdown from `inputs`. Pure computation — no I/O, no
    dependency on the ingest store. See the module docstring's ETF section for why these four
    factors (not the stock breakdown's five) are the right set for a fund rather than an
    operating company."""
    momentum, _momentum_percentile = _momentum_factor(
        FactorInputs(
            closes=inputs.closes,
            ma_series={},
            momentum_universe=inputs.momentum_universe,
            flow_value=inputs.flow_value,
            flow_universe=inputs.flow_universe,
            price=inputs.price,
            ma50=inputs.ma50,
        )
    )
    liquidity_flow = _liquidity_flow_factor(
        FactorInputs(
            closes=inputs.closes,
            ma_series={},
            momentum_universe=inputs.momentum_universe,
            flow_value=inputs.flow_value,
            flow_universe=inputs.flow_universe,
            price=inputs.price,
            ma50=inputs.ma50,
        )
    )
    premium_discount = _premium_discount_factor(inputs.premium_discount_pct)
    cost = _cost_factor(inputs.expense_ratio_pct, inputs.expense_ratio_universe)
    management_activity = _management_activity_factor(
        inputs.holdings_turnover, inputs.holdings_turnover_universe, is_active=inputs.is_active_etf
    )
    return EtfScoreBreakdown(
        momentum=momentum,
        premium_discount=premium_discount,
        cost=cost,
        management_activity=management_activity,
        liquidity_flow=liquidity_flow,
    )


def eod_bars_to_closes(bars: Sequence[EODBar]) -> list[float]:
    """Bars must already be sorted ascending by `trade_date` — this is a pure reshape, not a
    sort, matching `analytics.indicators`'s "caller is responsible for ordering" contract."""
    return [bar.close for bar in bars]


__all__ = [
    "EtfFactorInputs",
    "FactorInputs",
    "SectorContext",
    "apply_over_extension_cap",
    "band_from_composite",
    "blend_composite",
    "build_score_result",
    "compute_etf_factor_breakdown",
    "compute_stock_factor_breakdown",
    "eod_bars_to_closes",
    "net_buy_20d_trend",
    "normalization_caveat",
    "trend_template_score",
    "twelve_minus_one_momentum",
    "unavailable_factor",
    "winsorized_percentile",
]
