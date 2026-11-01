"""The stored price partitions carry the index rows `TAIEX` and `TPEx` next to the securities.
`TPEx` is 4 characters long, the length of a plain equity code, so every reader of the partition
must leave both indexes out of anything that counts, scores or ranks stocks."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from analytics.screener.engine import GateDefaults, _passes_tw_stock_gates
from analytics.symbol_universe import scoreable_symbol_sql
from ingest.store import ParquetStore
from schemas.enums import Market
from tools.base import ToolContext
from tools.market import GetAdvanceDeclineInput, GetAdvanceDeclineTool
from tools.market._common import closes_by_symbol

_PRIOR = date(2025, 6, 1)
_TODAY = date(2025, 6, 2)


def _bar(symbol: str, day: date, close: float, volume: int) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "trade_date": day.isoformat(),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "turnover": 0,
    }


@pytest.fixture
def store(tmp_path: Path) -> ParquetStore:
    store = ParquetStore(root=tmp_path)
    for day, closes in ((_PRIOR, (100.0, 20000.0, 200.0)), (_TODAY, (110.0, 21000.0, 210.0))):
        rows = [
            _bar("2330", day, closes[0], 5_000_000),
            _bar("TAIEX", day, closes[1], 9_000_000_000),
            _bar("TPEx", day, closes[2], 0),
        ]
        store.write_partition("TaiwanStockPrice", "TW", day, rows)
    return store


def test_the_close_map_behind_the_breadth_and_ranking_tools_has_no_index(
    store: ParquetStore,
) -> None:
    assert closes_by_symbol(store, "TW", _TODAY) == {"2330": 110.0}


async def test_the_index_rows_are_not_counted_as_advancing_stocks(store: ParquetStore) -> None:
    output = await GetAdvanceDeclineTool(store).run(
        GetAdvanceDeclineInput(market=Market.TW), ToolContext()
    )

    assert output.advancing_count == 1  # 2330 alone: both indexes rose too


def test_the_columnar_read_behind_the_scores_and_the_screener_has_no_index(
    store: ParquetStore,
) -> None:
    frame = store.read_range_frame(
        "TaiwanStockPrice",
        "TW",
        [_PRIOR, _TODAY],
        columns=["symbol", "close"],
        where=scoreable_symbol_sql(Market.TW),
    )

    assert set(frame["symbol"]) == {"2330"}


def test_the_trend_batch_keeps_taiex_for_its_reference_and_still_drops_tpex(
    store: ParquetStore,
) -> None:
    frame = store.read_range_frame(
        "TaiwanStockPrice",
        "TW",
        [_PRIOR, _TODAY],
        columns=["symbol"],
        where=scoreable_symbol_sql(Market.TW, keep=("TAIEX",)),
    )

    assert set(frame["symbol"]) == {"2330", "TAIEX"}


def test_the_screener_stock_gate_does_not_pass_the_index_rows() -> None:
    gates = GateDefaults()
    index_row = _bar("TPEx", _TODAY, 381.69, 0)

    assert _passes_tw_stock_gates(_bar("2330", _TODAY, 110.0, 5_000_000), gates) == "2330"
    assert _passes_tw_stock_gates(index_row, gates) is None
    assert _passes_tw_stock_gates({**index_row, "symbol": "TAIEX"}, gates) is None
