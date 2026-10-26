"""`evaluate_condition` edge cases: unevaluable inputs, threshold boundaries and message text."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ingest.jobs import ATTENTION_STOCKS_DATASET, PRICE_EOD_JOB
from scheduler.evaluator import Evaluation, SnapshotView, evaluate_condition
from scheduler.templates import AttentionListEntry, ClosePrice, DailyChange, VolumeSurge
from schemas.enums import Market

PRICE = PRICE_EOD_JOB.dataset
TODAY = datetime.now(ZoneInfo("Asia/Taipei")).date()


def day(n: int) -> date:
    return TODAY + timedelta(days=n)


class Reader:
    def __init__(self) -> None:
        self.parts: dict[tuple[str, str, date], list[dict[str, object]]] = {}

    def put(self, dataset: str, when: date, rows: list[dict[str, object]]) -> None:
        self.parts[(dataset, "TW", when)] = rows

    def read_partition(self, dataset: str, market: str, run_date: date) -> list[dict[str, object]]:
        return list(self.parts.get((dataset, market, run_date), []))

    def has_partition(self, dataset: str, market: str, run_date: date) -> bool:
        return (dataset, market, run_date) in self.parts


def evaluate(condition, reader: Reader, when: date, symbol: str = "2330") -> Evaluation | None:  # type: ignore[no-untyped-def]
    return evaluate_condition(
        condition, symbol=symbol, market=Market.TW, as_of=when, view=SnapshotView(reader)
    )


def test_close_price_with_a_non_numeric_close_is_not_evaluable() -> None:
    r = Reader()
    r.put(PRICE, day(0), [{"symbol": "2330", "close": "n/a", "volume": 1}])
    assert evaluate(ClosePrice(comparator="above", value=1), r, day(0)) is None


def test_close_price_boolean_close_is_not_a_number() -> None:
    r = Reader()
    r.put(PRICE, day(0), [{"symbol": "2330", "close": True, "volume": 1}])
    assert evaluate(ClosePrice(comparator="above", value=1), r, day(0)) is None


def test_close_price_equal_to_the_value_is_neither_above_nor_below() -> None:
    r = Reader()
    r.put(PRICE, day(0), [{"symbol": "2330", "close": 600.0, "volume": 1}])
    above = evaluate(ClosePrice(comparator="above", value=600), r, day(0))
    below = evaluate(ClosePrice(comparator="below", value=600), r, day(0))
    assert above is not None
    assert above.met is False
    assert "高於" in above.message
    assert below is not None
    assert below.met is False
    assert "低於" in below.message


def test_daily_change_threshold_is_inclusive_in_both_directions() -> None:
    r = Reader()
    r.put(PRICE, day(0), [{"symbol": "2330", "close": 100.0, "volume": 1}])
    r.put(PRICE, day(1), [{"symbol": "2330", "close": 105.0, "volume": 1}])
    r.put(PRICE, day(2), [{"symbol": "2330", "close": 99.75, "volume": 1}])
    up = evaluate(DailyChange(direction="up", percent=5), r, day(1))
    assert up is not None
    assert up.met is True
    assert "單日變動 +5.00%（前一交易日收盤 100）" in up.message
    down = evaluate(DailyChange(direction="down", percent=5), r, day(1))
    assert down is not None
    assert down.met is False
    r.put(PRICE, day(2), [{"symbol": "2330", "close": 95.0, "volume": 1}])
    r.put(PRICE, day(1), [{"symbol": "2330", "close": 100.0, "volume": 1}])
    r.put(PRICE, day(0), [{"symbol": "2330", "close": 100.0, "volume": 1}])
    down_hit = evaluate(DailyChange(direction="down", percent=5), r, day(2))
    assert down_hit is not None
    assert down_hit.met is True
    assert "-5.00%" in down_hit.message


def test_daily_change_with_a_zero_or_missing_prior_close_is_not_evaluable() -> None:
    r = Reader()
    r.put(PRICE, day(0), [{"symbol": "2330", "close": 0, "volume": 1}])
    r.put(PRICE, day(1), [{"symbol": "2330", "close": 10.0, "volume": 1}])
    assert evaluate(DailyChange(direction="up", percent=1), r, day(1)) is None
    r.put(PRICE, day(0), [{"symbol": "2317", "close": 10.0, "volume": 1}])
    assert evaluate(DailyChange(direction="up", percent=1), r, day(1)) is None


def test_daily_change_with_a_non_numeric_close_today_is_not_evaluable() -> None:
    r = Reader()
    r.put(PRICE, day(0), [{"symbol": "2330", "close": 10.0, "volume": 1}])
    r.put(PRICE, day(1), [{"symbol": "2330", "close": None, "volume": 1}])
    assert evaluate(DailyChange(direction="up", percent=1), r, day(1)) is None


def test_volume_surge_needs_at_least_half_the_window_of_history() -> None:
    r = Reader()
    for n in (-2, -1):
        r.put(PRICE, day(n), [{"symbol": "2330", "close": 1.0, "volume": 1000}])
    r.put(PRICE, day(0), [{"symbol": "2330", "close": 1.0, "volume": 5000}])
    # window 5 -> needs >= 2 history days: two are present, so it evaluates ...
    enough = evaluate(VolumeSurge(multiple=2, window=5), r, day(0))
    assert enough is not None
    assert enough.met is True
    assert "近 2 日均量的 5.0 倍" in enough.message
    # ... window 6 -> needs >= 3, only two exist.
    assert evaluate(VolumeSurge(multiple=2, window=6), r, day(0)) is None


def test_volume_surge_with_zero_history_or_missing_volume_is_not_evaluable() -> None:
    r = Reader()
    for n in (-2, -1):
        r.put(PRICE, day(n), [{"symbol": "2330", "close": 1.0, "volume": 0}])
    r.put(PRICE, day(0), [{"symbol": "2330", "close": 1.0, "volume": 5000}])
    assert evaluate(VolumeSurge(multiple=2, window=5), r, day(0)) is None
    r.put(PRICE, day(-1), [{"symbol": "2330", "close": 1.0, "volume": 1000}])
    r.put(PRICE, day(0), [{"symbol": "2330", "close": 1.0}])
    assert evaluate(VolumeSurge(multiple=2, window=5), r, day(0)) is None


def test_volume_surge_multiple_is_inclusive() -> None:
    r = Reader()
    for n in (-2, -1):
        r.put(PRICE, day(n), [{"symbol": "2330", "close": 1.0, "volume": 1000}])
    r.put(PRICE, day(0), [{"symbol": "2330", "close": 1.0, "volume": 2000}])
    result = evaluate(VolumeSurge(multiple=2, window=5), r, day(0))
    assert result is not None
    assert result.met is True


def test_attention_entry_message_and_unevaluable_without_a_partition() -> None:
    r = Reader()
    assert evaluate(AttentionListEntry(), r, day(0)) is None
    r.put(ATTENTION_STOCKS_DATASET, day(0), [{"symbol": "2330"}])
    hit = evaluate(AttentionListEntry(), r, day(0))
    assert hit is not None
    assert hit.met is True
    assert hit.message.startswith("2330 列入注意股名單（你設定的條件：")
    miss = evaluate(AttentionListEntry(), r, day(0), symbol="2317")
    assert miss is not None
    assert miss.met is False
