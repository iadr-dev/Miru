"""Score tests: missing-factor data must be explicit null/unavailable (never fabricated), the
band vocabulary must contain no trading-signal language, and the regime + non-trading caveats
must always be present."""

from __future__ import annotations

from datetime import date

import pytest

from analytics.fundamentals import FundamentalMetrics
from analytics.models import FactorScore, ScoreBand, ScoreProfile, StockScoreBreakdown
from analytics.score import (
    _QUALITY_SUB_METRICS,
    _VALUE_SUB_METRICS,
    FactorInputs,
    _fundamental_factor,
    apply_over_extension_cap,
    band_from_composite,
    blend_composite,
    build_score_result,
    compute_stock_factor_breakdown,
    net_buy_20d_trend,
    normalization_caveat,
    trend_template_score,
    twelve_minus_one_momentum,
    unavailable_factor,
    winsorized_percentile,
)
from analytics.symbol_universe import SymbolType
from schemas.capabilities import InstitutionalFlowRow
from schemas.enums import Market

_TRADING_LANGUAGE = ("buy", "sell", "hold", "進場", "停損", "加碼", "減碼", "推薦", "target price")


def _flow_row(*, buy: int, sell: int, investor: str = "Foreign_Investor") -> InstitutionalFlowRow:
    return InstitutionalFlowRow(
        symbol="2330",
        market=Market.TW,
        trade_date=date(2024, 1, 2),
        investor=investor,
        buy=buy,
        sell=sell,
    )


def test_twelve_minus_one_momentum_needs_full_12m_history() -> None:
    too_short = [100.0] * 200
    assert twelve_minus_one_momentum(too_short) is None


def test_twelve_minus_one_momentum_flat_series_is_zero() -> None:
    closes = [100.0] * 260
    assert twelve_minus_one_momentum(closes) == 0.0


def test_twelve_minus_one_momentum_excludes_most_recent_month() -> None:
    # A steady ramp for 253 points, then a sharp spike in only the final ~month. The 12-1 window
    # is anchored ~21 days before the end, so the last-month spike must NOT lift the figure — the
    # whole point of the skip-a-month convention.
    ramp = [100.0 + i for i in range(253)]  # index -22 (near anchor) = 100 + 231 = 331
    spiked = [*ramp, *([1000.0] * 21)]  # 21 fresh points far above the ramp
    near = spiked[-1 - 21]  # ~one month back — still on the ramp, unaffected by the spike
    far = spiked[-1 - 252]
    expected = (near - far) / far
    assert twelve_minus_one_momentum(spiked) == pytest.approx(expected)
    # And the spike genuinely would have mattered had we not skipped it:
    assert near < 1000.0


def test_winsorized_percentile_needs_at_least_two_in_universe() -> None:
    assert winsorized_percentile(5.0, [5.0]) is None


def test_winsorized_percentile_rank_within_universe() -> None:
    universe = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert winsorized_percentile(5.0, universe) == 100.0
    assert winsorized_percentile(1.0, universe) == 20.0


def test_trend_template_score_needs_252_days() -> None:
    closes = [100.0] * 100
    ma_series = {"ma50": [100.0] * 100, "ma150": [100.0] * 100, "ma200": [100.0] * 100}
    assert trend_template_score(closes, ma_series) is None


def test_trend_template_score_counts_conditions() -> None:
    n = 260
    closes = [100.0 + i * 0.3 for i in range(n)]  # steadily rising, comfortably clears the
    # >=30%-above-52w-low condition (100 -> ~178, +78%)
    ma50 = [90.0] * n
    ma150 = [80.0] * n
    ma200 = [70.0] * n
    ma200[-22] = 69.0  # rising over the last month
    ma_series = {"ma50": ma50, "ma150": ma150, "ma200": ma200}

    score = trend_template_score(closes, ma_series, rs_percentile=80.0)

    assert score is not None
    assert score == 8  # every condition should be met in this constructed uptrend


def test_net_buy_20d_trend_none_when_no_rows() -> None:
    assert net_buy_20d_trend([]) is None


def test_net_buy_20d_trend_sums_matching_investors() -> None:
    rows = [
        _flow_row(buy=100, sell=40, investor="Foreign_Investor"),
        _flow_row(buy=50, sell=10, investor="Investment_Trust"),
        _flow_row(buy=999, sell=0, investor="Dealer_self"),  # excluded by default filter
    ]
    assert net_buy_20d_trend(rows) == 100.0  # (100-40) + (50-10)


def test_apply_over_extension_cap_caps_when_stretched() -> None:
    capped, was_capped = apply_over_extension_cap(95.0, price=130.0, ma50=100.0)
    assert was_capped is True
    assert capped == pytest.approx(70.0)


def test_apply_over_extension_cap_no_op_when_not_stretched() -> None:
    capped, was_capped = apply_over_extension_cap(95.0, price=105.0, ma50=100.0)
    assert was_capped is False
    assert capped == 95.0


def test_apply_over_extension_cap_passthrough_when_inputs_missing() -> None:
    capped, was_capped = apply_over_extension_cap(None, price=None, ma50=None)
    assert capped is None
    assert was_capped is False


def test_unavailable_factor_is_null_not_fabricated() -> None:
    factor = unavailable_factor("quality", "no financials ingested")
    assert factor.percentile is None
    assert factor.unavailable_reason == "no financials ingested"


def test_blend_composite_renormalizes_over_available_factors_only() -> None:
    breakdown = StockScoreBreakdown(
        momentum=FactorScore(name="momentum", percentile=80.0, description="d"),
        trend_quality=FactorScore(name="trend_quality", percentile=60.0, description="d"),
        value_guard=unavailable_factor("value_guard", "no data"),
        quality=unavailable_factor("quality", "no data"),
        revenue_flow=unavailable_factor("revenue_flow", "no data"),
    )
    composite, partial = blend_composite(breakdown, ScoreProfile.SWING)

    assert partial == ("value_guard", "quality", "revenue_flow")
    # renormalized over momentum(.35)+trend(.25)=.60 -> weighted avg of 80 and 60
    assert composite == pytest.approx((0.35 * 80.0 + 0.25 * 60.0) / 0.60)


def test_blend_composite_none_when_all_factors_unavailable() -> None:
    breakdown = StockScoreBreakdown(
        momentum=unavailable_factor("momentum", "no data"),
        trend_quality=unavailable_factor("trend_quality", "no data"),
        value_guard=unavailable_factor("value_guard", "no data"),
        quality=unavailable_factor("quality", "no data"),
        revenue_flow=unavailable_factor("revenue_flow", "no data"),
    )
    composite, partial = blend_composite(breakdown, ScoreProfile.SWING)

    assert composite is None
    assert len(partial) == 5


@pytest.mark.parametrize(
    ("composite", "expected_band"),
    [
        (80.0, ScoreBand.BULLISH_LEANING),
        (50.0, ScoreBand.NEUTRAL),
        (20.0, ScoreBand.BEARISH_LEANING),
    ],
)
def test_band_from_composite(composite: float, expected_band: ScoreBand) -> None:
    assert band_from_composite(composite) is expected_band


def test_band_from_composite_none_composite_is_neutral() -> None:
    assert band_from_composite(None) is ScoreBand.NEUTRAL


def test_no_score_band_value_contains_trading_language() -> None:
    for band in ScoreBand:
        lowered = band.value.lower()
        for banned in _TRADING_LANGUAGE:
            assert banned not in lowered


def test_score_result_always_carries_both_caveats() -> None:
    breakdown = compute_stock_factor_breakdown(
        FactorInputs(
            closes=[100.0] * 5,
            ma_series={"ma50": [None] * 5, "ma150": [None] * 5, "ma200": [None] * 5},
            momentum_universe=[1.0, 2.0],
            flow_value=None,
            flow_universe=[],
            price=100.0,
            ma50=None,
        )
    )
    result = build_score_result(
        symbol="2330",
        market=Market.TW,
        as_of=date(2024, 1, 2),
        profile=ScoreProfile.SWING,
        symbol_type=SymbolType.STOCK,
        breakdown=breakdown,
    )

    assert result.regime_caveat
    assert result.non_trading_caveat
    # The non-trading caveat legitimately *names* buy/sell/hold in order to disclaim them
    # ("not a buy, sell, hold... recommendation") — the governance-relevant check is that the
    # *band enum itself* never carries that vocabulary (test_no_score_band_value_contains_
    # trading_language, above), not that the disclaimer text avoids the words it's negating.


def test_score_result_discloses_all_unavailable_factors_when_no_data() -> None:
    breakdown = compute_stock_factor_breakdown(
        FactorInputs(
            closes=[100.0] * 5,
            ma_series={"ma50": [None] * 5, "ma150": [None] * 5, "ma200": [None] * 5},
            momentum_universe=[1.0, 2.0],
            flow_value=None,
            flow_universe=[],
            price=100.0,
            ma50=None,
        )
    )
    result = build_score_result(
        symbol="2330",
        market=Market.TW,
        as_of=date(2024, 1, 2),
        profile=ScoreProfile.SWING,
        symbol_type=SymbolType.STOCK,
        breakdown=breakdown,
    )

    assert result.composite is None
    assert result.band is ScoreBand.NEUTRAL
    assert set(result.partial_factors) == {
        "momentum",
        "trend_quality",
        "value_guard",
        "quality",
        "revenue_flow",
    }


# --- fundamental (quality/value) factor computation ----------------------------------------


def _quality_universe() -> dict[str, list[float]]:
    return {
        "roe": [0.05, 0.10, 0.15, 0.20, 0.25],
        "debt_to_equity": [0.2, 0.4, 0.6, 0.8, 1.0],
        "earnings_variability": [0.1, 0.2, 0.3, 0.4, 0.5],
        "roic": [0.03, 0.06, 0.09, 0.12, 0.15],
        "gross_profitability": [0.1, 0.15, 0.2, 0.25, 0.3],
    }


def _value_universe() -> dict[str, list[float]]:
    return {
        "ev_ebitda": [5.0, 8.0, 11.0, 14.0, 17.0],
        "fcf_yield": [0.01, 0.03, 0.05, 0.07, 0.09],
        "pe": [8.0, 12.0, 16.0, 20.0, 24.0],
        "pb": [0.8, 1.2, 1.6, 2.0, 2.4],
    }


def test_quality_factor_computes_real_percentile_from_metrics() -> None:
    metrics = FundamentalMetrics(
        roe=0.25,  # top of the universe
        debt_to_equity=0.2,  # lowest debt -> best (penalty direction)
        earnings_variability=0.1,  # steadiest -> best
        roic=0.15,
        gross_profitability=0.3,
    )
    factor = _fundamental_factor(
        "quality",
        metrics,
        _QUALITY_SUB_METRICS,
        _quality_universe(),
        "unavailable",
    )
    assert factor.percentile is not None
    assert factor.unavailable_reason is None
    assert factor.percentile > 50.0  # a strong-on-every-sub-metric symbol ranks high


def test_value_factor_computes_real_percentile_from_metrics() -> None:
    metrics = FundamentalMetrics(
        ev_ebitda=5.0,  # cheapest (penalty direction) -> best
        fcf_yield=0.09,  # highest -> best
        pe=8.0,
        pb=0.8,
    )
    factor = _fundamental_factor(
        "value_guard",
        metrics,
        _VALUE_SUB_METRICS,
        _value_universe(),
        "unavailable",
    )
    assert factor.percentile is not None
    assert factor.percentile > 50.0


def test_fundamental_factor_degrades_submetric_by_submetric() -> None:
    # Only ROE present; the rest of the quality sub-metrics are absent for this symbol.
    metrics = FundamentalMetrics(roe=0.25)
    factor = _fundamental_factor(
        "quality",
        metrics,
        _QUALITY_SUB_METRICS,
        _quality_universe(),
        "unavailable",
    )
    assert factor.percentile is not None  # still computable off the one available sub-metric
    assert "ROE" in factor.description


def test_fundamental_factor_unavailable_when_no_metrics_survive() -> None:
    metrics = FundamentalMetrics()  # every sub-metric None
    factor = _fundamental_factor(
        "quality",
        metrics,
        _QUALITY_SUB_METRICS,
        _quality_universe(),
        "no financials ingested",
    )
    assert factor.percentile is None
    assert factor.unavailable_reason == "no financials ingested"


def test_fundamental_factor_unavailable_when_metrics_is_none() -> None:
    factor = _fundamental_factor(
        "value_guard", None, _VALUE_SUB_METRICS, {}, "no financials ingested"
    )
    assert factor.percentile is None


def test_normalization_caveat_flavors() -> None:
    sector = normalization_caveat(
        sector_relative=True, sector_label="半導體", has_fundamentals=True
    )
    assert "半導體" in sector
    whole = normalization_caveat(sector_relative=False, sector_label=None, has_fundamentals=True)
    assert "全市場" in whole
    none = normalization_caveat(sector_relative=False, sector_label=None, has_fundamentals=False)
    assert "未進行橫斷面標準化" in none


def test_annualized_ttm_is_disclosed_in_factor_description() -> None:
    metrics = FundamentalMetrics(roe=0.25, ttm_is_annualized=True)
    factor = _fundamental_factor(
        "quality", metrics, _QUALITY_SUB_METRICS, _quality_universe(), "unavailable"
    )
    assert factor.percentile is not None
    assert "TTM" in factor.description
