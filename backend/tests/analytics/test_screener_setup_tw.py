"""Characterization of `analytics.screener.engine._setup_tw` — stage ⓑ for TW, one branch per
condition source (flag tag / valuation / fundamentals / technical / score percentile / unknown),
with every whole-market read replaced by a counting stub."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from analytics.screener import engine
from analytics.screener.engine import ScreenerConditionInput, _setup_tw
from schemas.screener import ScreenerOperator

_DAY = date(2026, 10, 16)
_GATED = {"1101", "1102", "1103"}


class _Calls:
    def __init__(self) -> None:
        self.valuation = 0
        self.fundamentals = 0
        self.technical = 0
        self.flags = 0
        self.sessions = 0
        self.scores = 0


class _Session:
    def __init__(self, calls: _Calls) -> None:
        self._calls = calls

    async def __aenter__(self) -> object:
        self._calls.sessions += 1
        return object()

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    c = _Calls()
    engine._BULK_CACHE.clear()

    def valuation(store: object, market: str) -> dict[str, dict[str, float]]:
        c.valuation += 1
        return {"1101": {"per": 10.0, "pbr": 3.0}, "1102": {"per": 20.0, "pbr": 1.0}}

    def fundamentals(store: object, market: str) -> dict[str, dict[str, float]]:
        c.fundamentals += 1
        return {"1101": {"roe_ttm": 15.0}, "1103": {"roe_ttm": 5.0}, "9999": {"roe_ttm": 99.0}}

    def technical(store: object, market: str) -> dict[str, dict[str, float]]:
        c.technical += 1
        return {"1102": {"rsi_14": 70.0}, "1103": {"rsi_14": 30.0}}

    async def scores(session: object, *, market: object, profile: object) -> tuple[object, ...]:
        c.scores += 1
        return tuple(
            SimpleNamespace(
                symbol=symbol,
                breakdown=SimpleNamespace(
                    momentum=SimpleNamespace(percentile=pct),
                    quality=None,
                    chip_broker_net_buy=SimpleNamespace(percentile=pct / 2),
                ),
            )
            for symbol, pct in (("1101", 90.0), ("1102", 40.0), ("5555", 99.0))
        )

    monkeypatch.setattr(engine, "_latest_valuation_ratios_by_symbol", valuation)
    monkeypatch.setattr(engine, "_latest_fundamentals_by_symbol", fundamentals)
    monkeypatch.setattr(engine, "_fundamentals_as_of", lambda store, market: _DAY)
    monkeypatch.setattr(engine, "_latest_technical_indicators_by_symbol", technical)
    monkeypatch.setattr(engine, "_latest_price_partition_date", lambda store, market: _DAY)
    monkeypatch.setattr(engine, "get_all_scores_for_date", scores)
    return c


class _Reader:
    def __init__(self, calls: _Calls, rows: list[dict[str, object]]) -> None:
        self._calls = calls
        self._rows = rows

    def read_partition(self, dataset: str, market: str, day: date) -> list[dict[str, object]]:
        self._calls.flags += 1
        return self._rows


def _cond(factor: str, op: ScreenerOperator, value: float | None) -> ScreenerConditionInput:
    return ScreenerConditionInput(factor=factor, op=op, value=value)


async def _run(  # noqa: PLR0913, PLR0917 — test helper, every knob is per-test
    calls: _Calls,
    conditions: list[ScreenerConditionInput],
    match: str = "AND",
    flag_rows: list[dict[str, object]] | None = None,
    flag_day: date | None = _DAY,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[dict[str, list[str]], list[str]]:
    reader: Any = _Reader(calls, flag_rows or [])
    assert monkeypatch is not None
    monkeypatch.setattr(engine, "_latest_tw_flag_date", lambda r: flag_day)
    return await _setup_tw(
        conditions=tuple(conditions),
        match=match,  # type: ignore[arg-type]
        gated_universe=set(_GATED),
        tag_reader=reader,
        session_factory=lambda: _Session(calls),  # type: ignore[arg-type,return-value]
        parquet_store=object(),  # type: ignore[arg-type]
    )


_FLAGS: list[dict[str, object]] = [
    {"symbol": "1101", "tag": "bullish_engulfing"},
    {"symbol": "1102", "tag": "bullish_engulfing"},
    {"symbol": "7777", "tag": "bullish_engulfing"},  # outside the gated universe
    {"symbol": "1102", "tag": "bearish_engulfing"},
]


async def test_no_conditions_gives_nothing(calls: _Calls, monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _run(calls, [], monkeypatch=monkeypatch) == ({}, [])


async def test_tag_conditions_intersect_with_the_gated_universe_under_and(
    calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    matches, degraded = await _run(
        calls,
        [
            _cond("bullish_engulfing", ScreenerOperator.EQ, None),
            _cond("bearish_engulfing", ScreenerOperator.EQ, None),
        ],
        flag_rows=_FLAGS,
        monkeypatch=monkeypatch,
    )
    assert matches == {"1102": ["bullish_engulfing", "bearish_engulfing"]}
    assert degraded == []
    assert calls.flags == 2  # the flag partition is read per tag condition


async def test_tag_condition_without_a_flag_partition_matches_nothing_but_keeps_its_label(
    calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    conds = [
        _cond("bullish_engulfing", ScreenerOperator.EQ, None),
        _cond("pe_ratio", ScreenerOperator.LT, 100),
    ]
    and_matches, _ = await _run(
        calls, conds, flag_rows=_FLAGS, flag_day=None, monkeypatch=monkeypatch
    )
    assert and_matches == {}
    or_matches, _ = await _run(
        calls, conds, match="OR", flag_rows=_FLAGS, flag_day=None, monkeypatch=monkeypatch
    )
    assert or_matches == {"1101": ["pe_ratio"], "1102": ["pe_ratio"]}
    assert calls.flags == 0


async def test_valuation_conditions_share_one_read_and_respect_operator_edges(
    calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    matches, degraded = await _run(
        calls,
        [
            _cond("pe_ratio", ScreenerOperator.LTE, 20.0),
            _cond("pb_ratio", ScreenerOperator.GT, 1.0),
        ],
        monkeypatch=monkeypatch,
    )
    assert matches == {"1101": ["pe_ratio", "pb_ratio"]}
    assert degraded == []
    assert calls.valuation == 1


async def test_fundamentals_and_technical_read_once_each_and_ignore_other_symbols(
    calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    matches, _ = await _run(
        calls,
        [
            _cond("roe_ttm", ScreenerOperator.GTE, 5.0),
            _cond("rsi_14", ScreenerOperator.LT, 50.0),
        ],
        monkeypatch=monkeypatch,
    )
    assert matches == {"1103": ["roe_ttm", "rsi_14"]}  # 9999 is not in the gated universe
    assert (calls.fundamentals, calls.technical) == (1, 1)
    again, _ = await _run(
        calls, [_cond("roe_ttm", ScreenerOperator.GT, 10.0)], monkeypatch=monkeypatch
    )
    assert again == {"1101": ["roe_ttm"]}
    assert calls.fundamentals == 1  # cached for the same data day


async def test_score_conditions_open_one_session_and_read_percentiles(
    calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    matches, _ = await _run(
        calls,
        [
            _cond("momentum_percentile", ScreenerOperator.GTE, 40.0),
            _cond("broker_branch_net_buy", ScreenerOperator.GT, 20.0),
            _cond("quality_percentile", ScreenerOperator.GT, 0.0),
        ],
        match="OR",
        monkeypatch=monkeypatch,
    )
    assert matches == {
        "1101": ["momentum_percentile", "broker_branch_net_buy"],
        "1102": ["momentum_percentile"],
    }  # 5555 is gated out; `quality` is None so it never matches
    assert (calls.sessions, calls.scores) == (1, 1)


async def test_unknown_factor_is_degraded_without_a_label_or_a_match_set(
    calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    only_unknown = await _run(
        calls, [_cond("nope", ScreenerOperator.GT, 1.0)], monkeypatch=monkeypatch
    )
    assert only_unknown == ({}, ["unknown TW factor key: nope"])

    matches, degraded = await _run(
        calls,
        [_cond("nope", ScreenerOperator.GT, 1.0), _cond("pe_ratio", ScreenerOperator.LT, 15.0)],
        monkeypatch=monkeypatch,
    )
    assert matches == {"1101": ["pe_ratio"]}  # an unknown key does not empty an AND
    assert degraded == ["unknown TW factor key: nope"]


async def test_or_labels_list_only_the_conditions_each_symbol_satisfied(
    calls: _Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    matches, _ = await _run(
        calls,
        [
            _cond("bullish_engulfing", ScreenerOperator.EQ, None),
            _cond("pe_ratio", ScreenerOperator.GT, 15.0),
            _cond("rsi_14", ScreenerOperator.LT, 50.0),
        ],
        match="OR",
        flag_rows=_FLAGS,
        monkeypatch=monkeypatch,
    )
    assert matches == {
        "1101": ["bullish_engulfing"],
        "1102": ["bullish_engulfing", "pe_ratio"],
        "1103": ["rsi_14"],
    }
