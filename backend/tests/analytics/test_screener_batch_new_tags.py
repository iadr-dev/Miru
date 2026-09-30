"""Unit tests for the new chip/margin/shareholding tags added to `analytics/screener/batch.py`'s
`compute_flag_rows` — proving each is derived from real per-symbol row data, never a fabricated
default, and that the streak/tier semantics are correct in isolation from the store."""

from __future__ import annotations

from analytics.screener.batch import compute_flag_rows


def _flow_row(symbol: str, day: str, investor: str, buy: int, sell: int) -> dict[str, object]:
    return {"symbol": symbol, "trade_date": day, "investor": investor, "buy": buy, "sell": sell}


def test_trust_net_sell_streak_and_dealer_streaks() -> None:
    days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    flow_rows = [
        *[_flow_row("2330", d, "Investment_Trust", 0, 100) for d in days],  # 投信連3賣
        *[_flow_row("2330", d, "Dealer_self", 100, 0) for d in days],  # 自營商連3買
        *[_flow_row("1101", d, "Dealer_self", 0, 100) for d in days],  # 自營商連3賣
    ]
    rows = compute_flag_rows([], flow_rows)
    tags_2330 = {r["tag"] for r in rows if r["symbol"] == "2330"}
    tags_1101 = {r["tag"] for r in rows if r["symbol"] == "1101"}
    assert "trust_net_sell_streak_3" in tags_2330
    assert "dealer_net_buy_streak_3" in tags_2330
    assert "dealer_net_sell_streak_3" in tags_1101


def _margin_row(
    symbol: str, day: str, margin_balance: int, short_balance: int
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": day,
        "margin_purchase_today_balance": margin_balance,
        "short_sale_today_balance": short_balance,
    }


def test_margin_purchase_rising_and_short_sale_falling_streaks() -> None:
    days = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    margin_rows = [
        _margin_row("2330", days[0], 1000, 500),
        _margin_row("2330", days[1], 1100, 400),
        _margin_row("2330", days[2], 1200, 300),
        _margin_row("2330", days[3], 1300, 200),
    ]
    rows = compute_flag_rows([], [], margin_rows)
    tags = {r["tag"] for r in rows if r["symbol"] == "2330"}
    assert "margin_purchase_balance_streak_up_3" in tags
    assert "short_sale_balance_streak_down_3" in tags


def test_margin_balance_flat_day_breaks_the_streak() -> None:
    days = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    margin_rows = [
        _margin_row("2330", days[0], 1000, 500),
        _margin_row("2330", days[1], 1000, 500),  # flat, breaks the rising streak
        _margin_row("2330", days[2], 1200, 300),
        _margin_row("2330", days[3], 1300, 200),
    ]
    rows = compute_flag_rows([], [], margin_rows)
    tags = {r["tag"] for r in rows if r["symbol"] == "2330"}
    assert "margin_purchase_balance_streak_up_3" not in tags


def _shareholding_row(symbol: str, level: str, percent: float) -> dict[str, object]:
    return {"symbol": symbol, "holding_shares_level": level, "percent": percent, "unit": 0}


def test_small_and_large_holder_share_rising_3w() -> None:
    # 4 snapshots, oldest first, each week's small-holder (<=100張) share strictly rising and
    # large-holder (>=1000張) share strictly rising.
    snapshots = [
        [
            _shareholding_row("2330", "1-999", 10.0 + week),
            _shareholding_row("2330", "1,000,001以上", 5.0 + week),
        ]
        for week in range(4)
    ]
    rows = compute_flag_rows([], [], [], snapshots)
    tags = {r["tag"] for r in rows if r["symbol"] == "2330"}
    assert "small_holder_share_rising_3w" in tags
    assert "large_holder_share_rising_3w" in tags


def test_holder_share_rising_needs_all_four_snapshots() -> None:
    # Only 3 snapshots (2 deltas) — not enough to prove a 3-week streak, must not fire.
    snapshots = [[_shareholding_row("2330", "1-999", 10.0 + week)] for week in range(3)]
    rows = compute_flag_rows([], [], [], snapshots)
    tags = {r["tag"] for r in rows if r["symbol"] == "2330"}
    assert "small_holder_share_rising_3w" not in tags


def test_holder_share_flat_week_breaks_the_streak() -> None:
    percents = [10.0, 10.0, 12.0, 13.0]  # first delta is flat, not rising
    snapshots = [[_shareholding_row("2330", "1-999", p)] for p in percents]
    rows = compute_flag_rows([], [], [], snapshots)
    tags = {r["tag"] for r in rows if r["symbol"] == "2330"}
    assert "small_holder_share_rising_3w" not in tags
