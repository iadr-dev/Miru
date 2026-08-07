"""Shared helpers for the yfinance adapter test package — recorded-fixture blocking-call
mocking glue reused across every capability-domain test module.

Patches `anyio.to_thread.run_sync` directly (rather than a per-module re-export) since every
domain module in `sources.yfinance` imports the same `anyio.to_thread` module object — one
patch point covers `_adapter.py`, `holders.py`, `discovery.py`, `batch.py`, and
`screener.py` alike.
"""

from __future__ import annotations

import anyio.to_thread
import pandas as pd
import pytest


def patch_info(monkeypatch: pytest.MonkeyPatch, body: dict[str, object]) -> None:
    async def fake_run_sync(fn: object, *args: object) -> dict[str, object]:
        return body

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)


def patch_run_sync(monkeypatch: pytest.MonkeyPatch, **by_name: object) -> None:
    """Route `anyio.to_thread.run_sync(fn, *args)` to a canned return value keyed by `fn`'s
    module-level function name — lets one test stub exactly the blocking call(s) `fetch()`
    happens to make for a given capability, without mocking `yfinance` itself."""

    async def fake_run_sync(fn: object, *args: object) -> object:
        name = getattr(fn, "__name__", "")
        if name not in by_name:
            raise AssertionError(f"unexpected blocking call: {name}")
        value = by_name[name]
        return value(*args) if callable(value) else value

    monkeypatch.setattr(anyio.to_thread, "run_sync", fake_run_sync)


def empty_frames() -> dict[str, object]:
    """The full non-TW `Ticker` frame bundle, defaulted to "nothing available" — individual
    tests override just the frame(s) they exercise, so an unrelated frame being empty never
    causes an unrelated capability's test to fail."""
    return {
        "info": {},
        "history": pd.DataFrame(),
        "financials": pd.DataFrame(),
        "balance_sheet": pd.DataFrame(),
        "cash_flow": pd.DataFrame(),
        "dividends": pd.Series(dtype="float64"),
        "splits": pd.Series(dtype="float64"),
        "recommendations_summary": pd.DataFrame(),
        "upgrades_downgrades": pd.DataFrame(),
        "growth_estimates": pd.DataFrame(),
        "earnings_estimate": pd.DataFrame(),
        "revenue_estimate": pd.DataFrame(),
        "options": (),
        "option_chain": lambda *_a, **_k: None,
        "institutional_holders": pd.DataFrame(),
        "mutualfund_holders": pd.DataFrame(),
        "insider_transactions": pd.DataFrame(),
        "funds_data": None,
    }
