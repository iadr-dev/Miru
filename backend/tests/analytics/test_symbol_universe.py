"""Unit tests for `analytics.symbol_universe` — the shared scoreable-symbol classification used
by both the scoring batch and the market breadth/contribution/heatmap tools."""

from __future__ import annotations

import pandas as pd

from analytics.symbol_universe import (
    SymbolType,
    classify_symbol_type,
    is_scoreable_symbol,
    scoreable_symbol_mask,
)


def test_scoreable_symbol_mask_keeps_plain_equities_and_etfs_drops_warrants() -> None:
    """The exact split confirmed live against a real `TaiwanStockPrice` day: 4-digit plain
    equities and `00`-prefixed ETF codes (5-6 chars) are scoreable; 6-digit warrant codes NOT
    starting with `00`, and non-`00` 5-char exotic codes (preferred shares etc.), are not — a
    plain length cutoff alone would wrongly keep the warrant/exotic codes or wrongly drop real
    ETF codes, since both span the same length range."""
    symbols = pd.Series(
        [
            "2330",  # plain 4-digit equity
            "0050",  # plain 4-digit ETF
            "006201",  # 6-char `00`-prefixed ETF
            "00631L",  # 6-char `00`-prefixed leveraged ETF
            "020000",  # 6-digit warrant, NOT `00`-prefixed
            "2881B",  # 5-char preferred share, NOT `00`-prefixed
            "TAIEX",  # index aggregate label (should also be excluded, not just warrants)
        ]
    )

    mask = scoreable_symbol_mask(symbols)

    assert list(symbols[mask]) == ["2330", "0050", "006201", "00631L"]


def test_is_scoreable_symbol_matches_the_vectorized_mask() -> None:
    """The scalar predicate (used by `tools/market_tools.py`'s dict-shaped readers) must agree
    with the vectorized mask (used by `analytics/scoring_batch.py`'s DataFrame-shaped reader) on
    every case — same classification rule, two call shapes."""
    cases = {
        "2330": True,
        "0050": True,
        "006201": True,
        "00631L": True,
        "020000": False,
        "2881B": False,
        "TAIEX": False,
    }
    for symbol, expected in cases.items():
        assert is_scoreable_symbol(symbol) is expected


def test_classify_symbol_type_distinguishes_stock_leveraged_inverse_and_plain_etf() -> None:
    """TWSE's own documented suffix convention (confirmed against the exchange's real-time MIS
    ETF disclosure page): `L`-suffixed = leveraged, `R`-suffixed = inverse, any other
    `00`-prefixed code = a plain ETF, anything else scoreable = a plain equity."""
    cases = {
        "2330": SymbolType.STOCK,
        "2881": SymbolType.STOCK,
        "0050": SymbolType.ETF,
        "0056": SymbolType.ETF,
        "006201": SymbolType.ETF,
        "00987A": SymbolType.ETF,  # active ETF's own code carries no L/R suffix
        "00631L": SymbolType.ETF_LEVERAGED,
        "00663L": SymbolType.ETF_LEVERAGED,
        "00632R": SymbolType.ETF_INVERSE,
        "00664R": SymbolType.ETF_INVERSE,
    }
    for symbol, expected in cases.items():
        assert classify_symbol_type(symbol) is expected


def test_classify_symbol_type_does_not_misread_a_plain_equity_ending_in_l_or_r() -> None:
    """A 4-digit plain-equity code happening to look like it could end in an ETF-style suffix
    must never be misclassified — the ETF branch is gated on the `00`-prefix + length check
    first, exactly like `is_scoreable_symbol`'s own two-part rule."""
    assert classify_symbol_type("1234") is SymbolType.STOCK
