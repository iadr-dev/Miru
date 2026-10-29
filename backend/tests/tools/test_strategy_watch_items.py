"""Characterization of `GetStrategyWatchTool._items`: how a watchlist row is joined with its hits
(latest slot, first alert, close confirmation) and masked for an unentitled caller."""

from __future__ import annotations

import json
from pathlib import Path

from ingest.store import ParquetStore
from tools.market.strategy_watch import GetStrategyWatchTool, StrategyWatchItem


def _tool(tmp_path: Path) -> GetStrategyWatchTool:
    return GetStrategyWatchTool(ParquetStore(root=tmp_path))


def _row(symbol: str = "2330", strategy: str = "volatility_squeeze", **extra: object):
    row: dict[str, object] = {"symbol": symbol, "strategy": strategy, "close": 868.0}
    row.update(extra)
    return row


def _conditions(observed: float | None = 872.0, required: float | None = 870.0) -> str:
    return json.dumps(
        [
            {
                "key": "price_above_upper_band",
                "kind": "trigger",
                "met": True,
                "observed": observed,
                "required": required,
            }
        ]
    )


def _hit(  # noqa: PLR0913
    slot: str,
    *,
    symbol: str = "2330",
    strategy: str = "volatility_squeeze",
    alertable: object = False,
    all_met: object = False,
    confirmed: object = False,
    price: object = 872.0,
    conditions: object | None = None,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "strategy": strategy,
        "slot": slot,
        "price": price,
        "all_met": all_met,
        "confirmed": confirmed,
        "alertable": alertable,
        "conditions": _conditions() if conditions is None else conditions,
    }


def _items(
    tmp_path: Path,
    watch: list[dict[str, object]],
    hits: list[dict[str, object]],
    *,
    masked: bool = False,
) -> list[StrategyWatchItem]:
    return _tool(tmp_path)._items(watch, hits, masked=masked)


def test_a_row_without_hits_gets_the_defaults(tmp_path: Path) -> None:
    [item] = _items(tmp_path, [_row()], [])
    assert item.symbol == "2330"
    assert item.close == 868.0
    assert item.latest_slot is None
    assert item.price is None
    assert item.conditions == ()
    assert item.all_met is False
    assert item.confirmed is False
    assert item.alerted_at is None
    assert item.close_confirmed is False


def test_rows_with_an_unknown_strategy_are_dropped_and_order_is_kept(tmp_path: Path) -> None:
    watch = [
        _row("2409", "chip_accumulation"),
        _row("1111", "not_a_strategy"),
        {"symbol": "2222", "close": 1.0},  # no strategy at all
        _row("2330", "momentum_flag"),
    ]
    items = _items(tmp_path, watch, [])
    assert [(i.symbol, i.strategy) for i in items] == [
        ("2409", "chip_accumulation"),
        ("2330", "momentum_flag"),
    ]


def test_only_known_fields_with_values_reach_the_item(tmp_path: Path) -> None:
    row = _row(upper_band=870.0, lower_band=None, bogus="x", days_on_list=4, name="台積電")
    [item] = _items(tmp_path, [row], [])
    assert item.upper_band == 870.0
    assert item.lower_band is None
    assert item.days_on_list == 4
    assert item.name == "台積電"
    assert not hasattr(item, "bogus")


def test_the_latest_slot_wins_by_string_order_and_an_equal_slot_keeps_the_first(
    tmp_path: Path,
) -> None:
    hits = [
        _hit("10:00", price=1.0, all_met=True),
        _hit("09:40", price=2.0),
        _hit("10:00", price=3.0),  # same slot again: the first one stays
        _hit("09:50", price=4.0),
    ]
    [item] = _items(tmp_path, [_row()], hits)
    assert item.latest_slot == "10:00"
    assert item.price == 1.0
    assert item.all_met is True


def test_all_met_needs_an_actual_true(tmp_path: Path) -> None:
    [truthy] = _items(tmp_path, [_row()], [_hit("09:40", all_met=1)])
    [yes] = _items(tmp_path, [_row()], [_hit("09:40", all_met=True)])
    assert truthy.all_met is False
    assert yes.all_met is True


def test_confirmed_follows_the_latest_check_and_needs_an_actual_true(tmp_path: Path) -> None:
    hits = [
        _hit("09:40", all_met=True, confirmed=True),
        _hit("09:50", all_met=True, confirmed=False),
    ]
    [latest_unconfirmed] = _items(tmp_path, [_row()], hits)
    [latest_confirmed] = _items(tmp_path, [_row()], [_hit("10:00", all_met=True, confirmed=True)])
    [truthy] = _items(tmp_path, [_row()], [_hit("10:00", all_met=True, confirmed=1)])
    assert latest_unconfirmed.confirmed is False
    assert latest_confirmed.confirmed is True
    assert truthy.confirmed is False


def test_confirmed_survives_masking_and_a_close_slot_never_sets_it(tmp_path: Path) -> None:
    [masked] = _items(
        tmp_path, [_row()], [_hit("09:40", all_met=True, confirmed=True)], masked=True
    )
    [close_only] = _items(tmp_path, [_row()], [_hit("close", all_met=True, confirmed=True)])
    assert masked.confirmed is True
    assert close_only.confirmed is False


def test_alerted_at_is_the_earliest_alertable_intraday_slot(tmp_path: Path) -> None:
    hits = [
        _hit("10:10", alertable=True),
        _hit("09:50", alertable=True),
        _hit("09:40", alertable=False),
        _hit("10:00", alertable=True),
        _hit("09:30", alertable="yes"),  # only a literal True counts
    ]
    [item] = _items(tmp_path, [_row()], hits)
    assert item.alerted_at == "09:50"
    assert item.latest_slot == "10:10"


def test_the_close_slot_confirms_only_when_alertable_and_is_never_the_latest(
    tmp_path: Path,
) -> None:
    confirmed = _items(tmp_path, [_row()], [_hit("09:40"), _hit("close", alertable=True)])
    assert confirmed[0].close_confirmed is True
    assert confirmed[0].latest_slot == "09:40"
    assert confirmed[0].alerted_at is None  # a close alert is not an intraday alert

    not_alertable = _items(tmp_path, [_row()], [_hit("close", alertable=False)])
    assert not_alertable[0].close_confirmed is False
    assert not_alertable[0].latest_slot is None

    only_close = _items(tmp_path, [_row()], [_hit("close", alertable=True)])
    assert only_close[0].close_confirmed is True
    assert only_close[0].latest_slot is None
    assert only_close[0].conditions == ()


def test_hits_join_on_symbol_and_strategy(tmp_path: Path) -> None:
    watch = [_row("2330", "volatility_squeeze"), _row("2330", "momentum_flag"), _row("2409")]
    hits = [
        _hit("09:40", symbol="2330", strategy="momentum_flag", alertable=True),
        _hit("09:50", symbol="2409", strategy="chip_accumulation"),  # other strategy
    ]
    squeeze, flag, auo = _items(tmp_path, watch, hits)
    assert (squeeze.latest_slot, squeeze.alerted_at) == (None, None)
    assert (flag.latest_slot, flag.alerted_at) == ("09:40", "09:40")
    assert auo.latest_slot is None


def test_conditions_are_parsed_and_bad_payloads_become_empty(tmp_path: Path) -> None:
    [item] = _items(tmp_path, [_row()], [_hit("09:40")])
    assert len(item.conditions) == 1
    assert item.conditions[0].key == "price_above_upper_band"
    assert item.conditions[0].observed == 872.0
    assert item.conditions[0].required == 870.0
    for bad in ("not json", "{}", "null", ""):
        [other] = _items(tmp_path, [_row()], [_hit("09:40", conditions=bad)])
        assert other.latest_slot == "09:40"
        assert other.conditions == ()


def test_masking_nulls_sponsor_values_but_keeps_which_conditions_held(tmp_path: Path) -> None:
    row = _row(large_holder_pct=51.7, large_holder_change_4w=1.2, holders_change_4w_pct=-0.3)
    row["days_on_list"] = 3
    [open_item] = _items(tmp_path, [row], [_hit("09:40", all_met=True)], masked=False)
    assert open_item.large_holder_pct == 51.7
    assert open_item.large_holder_change_4w == 1.2
    assert open_item.holders_change_4w_pct == -0.3
    assert open_item.price == 872.0
    assert open_item.conditions[0].observed == 872.0

    [masked] = _items(tmp_path, [row], [_hit("09:40", all_met=True)], masked=True)
    assert masked.large_holder_pct is None
    assert masked.large_holder_change_4w is None
    assert masked.holders_change_4w_pct is None
    assert masked.days_on_list == 3  # non-Sponsor context is untouched
    assert masked.price is None
    assert masked.latest_slot == "09:40"
    assert masked.all_met is True
    assert masked.conditions[0].met is True
    assert masked.conditions[0].observed is None
    assert masked.conditions[0].required is None


def test_a_non_numeric_price_is_none_when_unmasked(tmp_path: Path) -> None:
    [item] = _items(tmp_path, [_row()], [_hit("09:40", price="n/a")])
    assert item.latest_slot == "09:40"
    assert item.price is None
