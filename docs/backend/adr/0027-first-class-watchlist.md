# ADR 0027 — First-class watchlist (relational store; memory becomes a projection)

## Status
Accepted. Reverses the `database-schema.md` §3 note ("a separate `watchlist_items` table is not
shipped unless a reverse query is needed"). Adds `watchlists` + `watchlist_items`
([database-schema.md](../database-schema.md) §3a), `/watchlists/*`
([api-reference.md](../api-reference.md) §5a), three §10 concurrency rows
([modules/16](../modules/16-infrastructure.md)), a tier cap
([modules/12](../modules/12-multi-user-tiers.md)); updates
[modules/09](../modules/09-memory.md). Drives the web watchlist rail.

## Context
The web client's right-side watchlist rail (adapted from the Kestrel reference) needs **named
groups, drag-to-reorder, TW/US/ETF market tabs, per-item notes, an idempotent star toggle, and a
reverse query** ("does this user watch 2330" → the star's lit/unlit state everywhere). The current
model — a single `memories(type='watchlist')` JSON blob with generic `/memory` CRUD — can't index
`sort_order`, can't do the reverse query, and can't partial-unique the star. The schema doc's
"don't ship the table" premise ("no reverse query needed") is now invalid.

## Decision
1. **Relational tables are the source of truth.** `watchlists` (groups) + `watchlist_items`, per
   our conventions (text UUID, CHECK enums, `version` lock, partial-unique, CASCADE, per-user
   isolation). `memories(type='watchlist')` is **retired as a writer** (CHECK value kept for legacy
   rows) and replaced by a **read-through projection** — `WatchlistStore.snapshot(user_id)` renders
   the same compact `name → [symbols]` block agent recall used. **One store, no dual-write**, so
   agent and UI never diverge.
2. **Ordering = lexorank (fractional `text` key)**, not integer shift — insert *between* neighbors,
   so two devices reordering different items never rewrite the same row (integer-shift rewrites O(n)
   and clobbers; rejected). Equal-midpoint collision → jitter+retry. Bulk reshuffle uses a full-array
   `POST /watchlists/reorder` + parent `version` CAS. Group rename uses optimistic `version` (as `threads`).
3. **Star toggle is idempotent** — `PUT`=`INSERT … ON CONFLICT (watchlist_id, symbol) DO NOTHING`,
   `DELETE` returns 204 regardless. Partial-unique `(watchlist_id, symbol)` is the guarantee; no
   `Idempotency-Key` needed.
4. **Batch quotes reuse `/quote(/stream)?symbols=`** — no watchlist-specific quote endpoint; price
   tiering (EOD free / live-ish Premium) stays in one place.
5. **Public, non-trading user data — ungated CRUD for all tiers.** Soft caps (Free ~5×50,
   Premium/Pro ~20×200) via `accounts/entitlements.watchlist_limit_for` are a storage guard, not a
   paywall (over-cap → a real 4xx, not a teaser). Only the live-quote *column* is Premium+.
6. **Agent gets a scoped write, MCP stays read-only.** `get_watchlist` is exposed over MCP
   (`readOnlyHint:true`, preserving the §9 "MCP is read-only" invariant); `add_to_watchlist` /
   remove are the **internal agent tool path only**, gated behind an explicit user utterance
   ("加入觀察清單"), writing product state (not market data → no ADR-0002 conflict) with `source_ref`
   provenance. Default presets = our `config.default_watchlist_presets` (our chosen seed groups —
   半導體/權值股/US Tech/US Index/台股 ETF/債券海外 ETF; verify contents suit our universe),
   **server-seeded on register** so the rail is never cold-empty.

## Consequences
- The rail's rich behaviors are all backed; the reverse-query star state is O(1) via `(user_id, symbol)`.
- Migration is expand-contract: backfill existing `memories` blobs → tables, dual-read during
  rollout, then stop writing the blob (dual-dialect Alembic).
- Adds one write capability to an otherwise read-only agent tool set — bounded to product state,
  non-trading, and kept off the public MCP surface.
- **A1 resolved:** `market` (CHECK `TW|US`, the true [modules/19](../modules/19-data-access-router.md)
  `Market`) is now separate from `asset_class` (CHECK `stock|etf`). The rail's tabs map TW=`(TW,stock)`,
  ETF=`(TW,etf)`, US=`(US,*)` — ETF no longer pollutes the `Market` enum.
- **Open items pending product sign-off:** exact tier-cap numbers (A5); `include=quote`
  one-round-trip convenience param (A3).
