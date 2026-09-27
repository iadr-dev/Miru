"""`fetch_index_quote` tests — recorded fixtures via `patch_run_sync`, no live network."""

from __future__ import annotations

import pytest

from sources.yfinance.index_quote import fetch_index_quote

from ._helpers import patch_run_sync


async def test_fetch_index_quote_returns_last_price_and_previous_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_run_sync(
        monkeypatch,
        _fetch_fast_info_blocking=lambda _symbol: {
            "lastPrice": 7743.41015625,
            "previousClose": 7704.13,
        },
    )

    last_price, previous_close = await fetch_index_quote("^GSPC")

    assert last_price == pytest.approx(7743.41015625)
    assert previous_close == pytest.approx(7704.13)


async def test_fetch_index_quote_degrades_honestly_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_error(_symbol: str) -> dict[str, object]:
        raise RuntimeError("upstream unavailable")

    patch_run_sync(monkeypatch, _fetch_fast_info_blocking=raise_error)

    last_price, previous_close = await fetch_index_quote("^GSPC")

    assert last_price is None
    assert previous_close is None


async def test_fetch_index_quote_handles_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_run_sync(monkeypatch, _fetch_fast_info_blocking=lambda _symbol: {})

    last_price, previous_close = await fetch_index_quote("^GSPC")

    assert last_price is None
    assert previous_close is None
