# ADR 0021 — Server-side demand-driven live-quote feed over SSE (governance §9b)

## Status
Accepted. Adds governance §9b; [modules/16](../modules/16-infrastructure.md) §2a; amends the
client-poll stance in [modules/12](../modules/12-multi-user-tiers.md) / [data-catalog.md](../data-catalog.md) §2.5.

## Context
Premium/Pro intraday quotes were originally specced as **client-side 5s polling**. That doesn't
scale: N users watching the same symbol = N polls against FinMind's shared **6000/hr Sponsor
pool** — the C3 ceiling from the backend review. Governance §1.3 forbade server-side
auto-polling/streaming, so a server feed needs an explicit, bounded carve-out.

## Decision
Adopt a **shared, demand-driven server poller + SSE fan-out**, gated by **governance §9b**:
- **Demand-driven:** the poller touches a symbol **only while ≥1 signed-in client is subscribed**
  (via an open `/quote/stream`); demand set = union of live subscriptions (Redis refcount).
  Not a whole-market scan, not a standing job.
- **Bounded:** 5s cadence, **market-hours only**, auto-stops on last disconnect; per-connection
  and global symbol caps; FinMind Sponsor pool budget applies (over-budget → slower/cached).
- **Fan-out:** one poll → Redis `quote:{market}:{symbol}` → all watching clients. **Lower** quota
  use than per-client polling, not higher.
- **Transport = SSE + Redis** (reuse the agent-chat fan-out, [16](../modules/16-infrastructure.md)
  §2) — a quote feed is server→client only; subscribe/unsubscribe is the stream's `symbols`
  param. **No WebSocket** (no client→server chatter for a read-only, non-trading display).
- **Tiering:** `price_intraday` = `sponsor` (Premium/Pro); Free = EOD. Payload keeps the
  delayed-snapshot caveat — a live-ish display, **not** true real-time, non-trading.

Scenarios served by one mechanism: detail page (1 symbol), watchlist (N), agent `rich_card`
quote (piggybacks the same SSE).

## Consequences
- Scales to many users on hot symbols within the Sponsor pool (1 poll → N clients).
- Amends §1.3: a bounded server poller now exists, but only as §9b (demand-driven, market-hours,
  auto-stopping) — distinct from the unattended scanning §1.3 forbids.
- No new transport: reuses SSE+Redis; one feature (the poller worker) + one endpoint
  (`/quote/stream`) + a subscription refcount in Redis.
- WebSocket reconsidered only if a future bidirectional/high-frequency client surface appears.
