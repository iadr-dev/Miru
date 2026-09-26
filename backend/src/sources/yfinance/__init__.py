"""yfinance adapter package.

Two distinct roles by market:

- **TW**: narrow gap-filler, unchanged from the original scope — `analyst_consensus` (the
  licensed target-price/ratings gap FinMind doesn't have), `esg`, and `sector_class` (English
  cross-market label; FinMind's Chinese 產業別/產業鏈 stays the TW authority). Prices/financials
  stay off yfinance for TW — FinMind/TWSE own those.
- **US/GB/JP/EU** (non-TW): yfinance is the **primary** source. It serves the same shared
  capabilities other adapters serve for TW (`price_eod`, `financials`, `balance_sheet`,
  `cash_flow_statement`, `dividends`, `split_price`, `analyst_consensus`, `esg`,
  `sector_class`) plus several capabilities with no FinMind/gov equivalent at all
  (`options_chain`, `institutional_holders`, `insider_activity`, `symbol_search`,
  `symbol_lookup`, `earnings_calendar`/`ipo_calendar`/`economic_calendar`, `sector_overview`,
  `industry_overview`, `market_status`, `fund_holdings`). Which source wins for which
  `(market, capability)` is config, never hardcoded here — this adapter only declares what it
  *can* serve via `supports()`.

Every blocking `yfinance` SDK call runs through `anyio.to_thread.run_sync` — the underlying
library talks to Yahoo over its own `requests`-based session, not through this codebase's
shared `httpx`-based TLS-client seam; that `httpx` client is simply never constructed anywhere
in this package. Nothing here ever calls yfinance synchronously on a request hot path outside
this isolated-thread pattern.

Coverage gating is uniform across every capability in this package: an empty/None upstream
result degrades via `HealthStatus.STALE` with a real detail message — never fabricated, never
rendered as empty scaffolding.

Internal layout: `_shared.py` (constants + coercion helpers + envelope constructor),
`_blocking.py` (every isolated synchronous SDK call), `_adapter.py` (the `YFinanceAdapter`
dispatch skeleton), and one module per capability domain — `analyst.py`,
`price_financials.py`, `holders.py`, `discovery.py` — plus the two non-`SourceAdapter`
carve-outs `batch.py` (ingest entry point) and `screener.py` (query-shaped screener, see its
own module docstring for why it lives outside `SourceAdapter`).

This module re-exports `YFinanceAdapter`, `batch_download`, `run_screener`, and
`run_predefined_screener` — the public seam every caller (`dataaccess/`, `ingest/`, tests)
imports as `from sources.yfinance import ...`.
"""

from __future__ import annotations

from ._adapter import YFinanceAdapter
from .batch import batch_download
from .screener import run_predefined_screener, run_screener

__all__ = ["YFinanceAdapter", "batch_download", "run_predefined_screener", "run_screener"]
