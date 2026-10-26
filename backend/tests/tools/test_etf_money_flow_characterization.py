"""Characterization of `GetEtfMoneyFlowTool.run_sync` across the branches the main tests in
`test_market_tools.py` do not reach: no holdings snapshot at all, per-ETF netting, holdings
without a close / shares-outstanding, and `top_n` truncation. Synthetic store data only."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ingest.store import ParquetStore
from schemas.enums import Market, Tier
from tools.base import ToolContext
from tools.market import GetEtfMoneyFlowInput, GetEtfMoneyFlowTool
from tools.market.etf_money_flow import GetEtfMoneyFlowOutput

DAY = date(2025, 5, 7)


def _change(symbol: str, stock: str, buy: int, sell: int) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "trade_date": DAY.isoformat(),
        "component_stock_id": stock,
        "component_stock_name": f"name-{stock}",
        "buy": buy,
        "sell": sell,
    }


def _holding(symbol: str, stock: str, shares: int) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "trade_date": DAY.isoformat(),
        "component_stock_id": stock,
        "component_stock_name": f"name-{stock}",
        "asset_type": "stock",
        "shares": shares,
        "weight": 1.0,
        "market_value": 0.0,
        "currency": "TWD",
    }


async def _run(store: ParquetStore, *, top_n: int = 20, tier: Tier = Tier.PREMIUM) -> object:
    return await GetEtfMoneyFlowTool(store).run(
        GetEtfMoneyFlowInput(market=Market.TW, start_date=DAY, end_date=DAY, top_n=top_n),
        ToolContext(tier=tier),
    )


async def test_changes_without_any_holdings_partition_leave_holding_fields_empty(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        DAY,
        [
            _change("00980A", "2330", 100, 100),  # nets to zero inside one ETF
            _change("00981A", "2330", 0, 40),
            _change("00980A", "2317", 70, 0),
        ],
    )

    out = await _run(store)

    assert isinstance(out, GetEtfMoneyFlowOutput)
    assert out.data_available is True
    assert out.holding_as_of is None
    assert out.requested_end_date is None
    assert out.total_stock_count == 2
    assert out.free_row_count == 2
    inflow = {r.component_stock_id: r for r in out.inflow_ranking}
    assert [r.component_stock_id for r in out.inflow_ranking] == ["2317", "2330"]
    assert [r.rank for r in out.inflow_ranking] == [1, 2]
    assert [r.component_stock_id for r in out.outflow_ranking] == ["2330", "2317"]
    assert inflow["2330"].net_shares_flow == -40
    # 00980A nets 0 on 2330 (neither buying nor selling); 00981A is selling.
    assert (inflow["2330"].buying_etf_count, inflow["2330"].selling_etf_count) == (0, 1)
    assert (inflow["2317"].buying_etf_count, inflow["2317"].selling_etf_count) == (1, 0)
    for row in out.inflow_ranking:
        assert row.component_stock_name == f"name-{row.component_stock_id}"
        assert row.active_etf_holder_count is None
        assert row.held_shares is None
        assert row.held_market_value is None
        assert row.stock_shares_held_pct is None
        assert row.locked is False


async def test_holdings_without_close_or_shares_outstanding_only_fill_what_exists(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        DAY,
        [_change("00980A", "2330", 10, 0), _change("00980A", "2317", 5, 0)],
    )
    store.write_partition(
        "TaiwanStockActiveETFHolding",
        "TW",
        DAY,
        [
            _holding("00980A", "2330", 1000),
            _holding("00981A", "2330", 500),  # a second ETF holds it: count 2, shares summed
            _holding("00980A", "2317", 300),
        ],
    )
    # 2330 has a close but no market-value row (no outstanding shares -> pct None);
    # 2317 has neither a close nor a market-value row.
    store.write_partition(
        "TaiwanStockPrice",
        "TW",
        DAY,
        [{"symbol": "2330", "trade_date": DAY.isoformat(), "close": 10.0}],
    )

    out = await _run(store)

    assert isinstance(out, GetEtfMoneyFlowOutput)
    assert out.holding_as_of == DAY
    rows = {r.component_stock_id: r for r in out.inflow_ranking}
    assert rows["2330"].active_etf_holder_count == 2
    assert rows["2330"].held_shares == 1500
    assert rows["2330"].held_market_value == pytest.approx(15000.0)
    assert rows["2330"].stock_shares_held_pct is None
    assert rows["2317"].active_etf_holder_count == 1
    assert rows["2317"].held_shares == 300
    assert rows["2317"].held_market_value is None
    assert rows["2317"].stock_shares_held_pct is None


async def test_top_n_truncates_each_ranking_after_sorting(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        DAY,
        [
            _change("00980A", "1101", 30, 0),
            _change("00980A", "1102", 0, 20),
            _change("00980A", "1103", 10, 0),
        ],
    )

    out = await _run(store, top_n=1)

    assert isinstance(out, GetEtfMoneyFlowOutput)
    assert out.total_stock_count == 3  # counts every aggregated stock, not just top_n
    assert [r.component_stock_id for r in out.inflow_ranking] == ["1101"]
    assert [r.component_stock_id for r in out.outflow_ranking] == ["1102"]
    assert out.inflow_ranking[0].rank == 1


async def test_unentitled_tier_masks_only_rows_beyond_the_free_count(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    store.write_partition(
        "TaiwanStockActiveETFHoldingChange",
        "TW",
        DAY,
        [_change("00980A", f"20{i:02d}", 100 - i, 0) for i in range(4)],
    )

    out = await _run(store, tier=Tier.FREE)

    assert isinstance(out, GetEtfMoneyFlowOutput)
    ranking = out.inflow_ranking
    assert [r.locked for r in ranking] == [False, False, True, True]
    assert [r.rank for r in ranking] == [1, 2, 3, 4]
    assert ranking[2].component_stock_id is None
    assert ranking[2].net_shares_flow is None
