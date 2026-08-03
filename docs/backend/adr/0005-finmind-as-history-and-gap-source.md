# ADR 0005 — FinMind as the history/backfill engine and gap-filler

## Status
Accepted.

## Context
The government OpenAPIs (TWSE/TPEX/TAIFEX) are authoritative but **latest-only, no
parameters** — every endpoint dumps the current snapshot. Building historical time series
from them would require daily polling + storage, which governance forbids as auto-polling.
Separately, a review against professional platforms (CMoney, Goodinfo, 財報狗) showed the
highest-value 籌碼 signals — 分點券商進出, 八大官股, 集保股權分級/大戶, 產業鏈資金流,
tick/minute-K, real-time snapshots — are not cleanly published by the gov OpenAPIs.

FinMind (open-source, token-auth REST) serves 75+ TW datasets — largely the same
underlying gov data, pre-collected — queryable by `data_id` + `start_date`/`end_date`,
plus Sponsor-only feeds that cover exactly those gaps. See [../data-catalog.md](../data-catalog.md).

## Decision
Add FinMind as a first-class source adapter (`backend/src/sources/finmind.py`) alongside the
gov adapters:
- It is the **history/backfill engine** for time series (avoids poll-and-store).
- It is the **gap-filler** for 分點/官股/股權分級/產業鏈/tick/minute-K.
- Per-dataset **timing class** and **`data_class`** are assigned: Free/Backer datasets =
  `public`; **FinMind Sponsor datasets = `sponsor`** (分點/官股/股權分級/產業鏈/tick/minute-K/
  real-time), entitlement-masked for Free users (see [0010](0010-remove-safety-tiers.md)).
- It is the **only token-bearing adapter**; `FINMIND_TOKEN` comes from env and is never
  stored. Membership-tier/rate-limit availability is surfaced through M5Q, not thrown.

## Consequences
- We get deep backfill and the premium 籌碼 signals without maintaining scrapers.
- One dependency on a third-party aggregator → mitigated by tagging FinMind EOD data with
  a "T+1 aggregator" caveat and keeping gov OpenAPIs as the authoritative same-day source.
- Real-time/paid snapshots are `sponsor` (entitlement-masked), and remain user-driven &
  non-streaming per governance §1.
- Data-quirk validators required (e.g. `TaiwanStockPrice` 0-on-no-trade; emerging-board
  `open` semantics) — documented in the data catalog.
- Genuinely absent/paid data (analyst target prices, earnings consensus, order-book depth,
  curated supply-chain graphs) remains out of scope and is surfaced as an explicit caveat.
