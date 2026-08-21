"""Unit tests for `analytics.scoring_batch`'s pure helpers — no store, no session, no daemon.
See `test_scoring_batch_e2e.py` for the real end-to-end proofs (daemon pass -> stock_scores rows
-> tool reads)."""

from __future__ import annotations

import pandas as pd

from analytics.scoring_batch import _scoreable_symbol_mask


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

    mask = _scoreable_symbol_mask(symbols)

    assert list(symbols[mask]) == ["2330", "0050", "006201", "00631L"]
