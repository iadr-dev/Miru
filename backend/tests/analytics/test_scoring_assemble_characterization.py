"""Pins `_assemble_signals_and_universes` (pass 1 of the scoring batch) on synthetic inputs."""

from __future__ import annotations

from datetime import date

from analytics.indicators import PriceBar
from analytics.score import eps_trend_delta, price_target_upside
from analytics.scoring_batch.scoring import _assemble_signals_and_universes
from schemas.capabilities import AnalystConsensus, InstitutionalFlowRow


def _bars(n: int, start: float = 100.0) -> tuple[PriceBar, ...]:
    return tuple(
        PriceBar(high=start + i + 1, low=start + i - 1, close=start + i, volume=1000 + i)
        for i in range(n)
    )


def _flow(symbol: str, investor: str, buy: int, sell: int) -> InstitutionalFlowRow:
    return InstitutionalFlowRow(
        symbol=symbol,
        market="TW",
        trade_date=date(2024, 6, 3),
        investor=investor,
        buy=buy,
        sell=sell,
    )


def _consensus(symbol: str, **kw: float | None) -> AnalystConsensus:
    return AnalystConsensus(symbol=symbol, market="US", as_of=date(2024, 6, 3), **kw)  # type: ignore[arg-type]


def test_symbol_without_any_input_gets_empty_series_and_none_values() -> None:
    signals, universes = _assemble_signals_and_universes(
        ["AAA"], bars_by_symbol={}, flow_by_symbol={}, lines_by_symbol={}, market_value_by_symbol={}
    )
    assert signals.price_by_symbol == {"AAA": None}
    assert signals.ma50_by_symbol == {"AAA": None}
    assert all(v == () for v in signals.ma_series_by_symbol["AAA"].values())
    assert "ma50" in signals.ma_series_by_symbol["AAA"]
    assert signals.analyst_consensus_by_symbol == {}
    assert signals.flow_value_by_symbol == {"AAA": None}
    assert set(signals.metrics_by_symbol) == {"AAA"}
    assert universes.momentum == []
    assert universes.flow == []
    assert universes.eps_trend_delta == []
    assert universes.growth_estimate == []
    assert universes.price_target_upside == []
    assert universes.dollar_volume == []


def test_bars_drive_price_ma_momentum_and_dollar_volume() -> None:
    bars = {"AAA": _bars(300), "BBB": _bars(60, 50.0)}
    signals, universes = _assemble_signals_and_universes(
        ["AAA", "BBB", "CCC"],
        bars_by_symbol=bars,
        flow_by_symbol={},
        lines_by_symbol={},
        market_value_by_symbol={},
    )
    assert signals.bars_by_symbol is bars
    assert signals.price_by_symbol == {"AAA": 399.0, "BBB": 109.0, "CCC": None}
    assert signals.ma50_by_symbol["AAA"] is not None
    assert signals.ma50_by_symbol["CCC"] is None
    assert len(signals.ma_series_by_symbol["AAA"]["ma50"]) == 300
    assert len(universes.dollar_volume) == 2  # CCC has no bars -> dropped from the universe
    assert len(universes.momentum) <= 2


def test_flow_rows_are_filtered_to_the_scored_investors() -> None:
    rows = [
        _flow("AAA", "Foreign_Investor", 100, 10),
        _flow("AAA", "Some_Other_Party", 10**9, 0),
    ]
    kept = _assemble_signals_and_universes(
        ["AAA"],
        bars_by_symbol={},
        flow_by_symbol={"AAA": rows},
        lines_by_symbol={},
        market_value_by_symbol={},
    )[0]
    only = _assemble_signals_and_universes(
        ["AAA"],
        bars_by_symbol={},
        flow_by_symbol={"AAA": rows[:1]},
        lines_by_symbol={},
        market_value_by_symbol={},
    )[0]
    assert kept.flow_value_by_symbol == only.flow_value_by_symbol


def test_analyst_consensus_feeds_the_us_universes_only_when_present() -> None:
    con = _consensus(
        "AAA",
        eps_trend_current=2.0,
        eps_trend_90_days_ago=1.0,
        current_year_growth_estimate=0.15,
        mean_target=120.0,
    )
    signals, universes = _assemble_signals_and_universes(
        ["AAA", "BBB"],
        bars_by_symbol={"AAA": _bars(10)},
        flow_by_symbol={},
        lines_by_symbol={},
        market_value_by_symbol={},
        analyst_consensus_by_symbol={"AAA": con},
    )
    assert signals.analyst_consensus_by_symbol == {"AAA": con}
    assert universes.eps_trend_delta == [eps_trend_delta(con)] == [1.0]
    assert universes.growth_estimate == [0.15]
    assert universes.price_target_upside == [price_target_upside(con, 109.0)]


def test_consensus_without_price_has_no_price_target_upside() -> None:
    con = _consensus("AAA", mean_target=120.0)
    _, universes = _assemble_signals_and_universes(
        ["AAA"],
        bars_by_symbol={},
        flow_by_symbol={},
        lines_by_symbol={},
        market_value_by_symbol={},
        analyst_consensus_by_symbol={"AAA": con},
    )
    assert universes.price_target_upside == []
    assert universes.eps_trend_delta == []
    assert universes.growth_estimate == []
