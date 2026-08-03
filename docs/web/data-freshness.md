# 了然 · Miru — Cache Coherence & Data Freshness (web)

The **coherence contract** for the web client: the guarantee that backend caches and frontend
caches never *combine* to serve stale data past its intended freshness. Blueprint — the spec
engineers build to, no code yet.

> **The one invariant.** A new authoritative update must become visible within its **capability's
> freshness budget**. **No cache layer — backend Redis/DuckDB, RSC fetch cache, Next.js `fetch`
> memo/`revalidate`, TanStack `staleTime`/`gcTime`, browser HTTP cache, Zustand store, SSE-hydrated
> state — may extend that budget.** Freshness is a *ceiling that composes*, not a per-layer TTL that
> sums.
>
> **Second-class-to-nothing corollary.** Freshness is delivered **without jank** — no blank panels,
> no scroll/sort/selection reset, no refetch storms, no per-tick React re-render. Cache-bust means
> *background revalidate*, never hard reload (see [§6](#6-freshness-without-jank--performance--state-management-posture)).

This doc grounds every mechanic in the real backend model — the demand-driven live-quote poller +
SSE fan-out and the concurrency model ([../backend/modules/16-infrastructure.md](../backend/modules/16-infrastructure.md)
§2a, §10), the `(capability, market, symbol)` cache key + freshness classes
([../backend/modules/19-data-access-router.md](../backend/modules/19-data-access-router.md) §2, §5),
the DuckDB/Parquet + Redis derived store with idempotent upsert + gap-detect + next-morning reconcile
and the daily/weekly compute batches ([../backend/modules/22-data-ingest.md](../backend/modules/22-data-ingest.md)
§0, §2, §4), and the governance currentness caveat + live-poller carve-out
([../backend/governance.md](../backend/governance.md) §9b, currentness caveat). It does **not** invent
cache mechanics; where the backend is genuinely unspecified it is flagged **⚑** for the coordinator.

---

## 1. The problem, stated precisely

Staleness is **two-sided** — either side alone can serve a value past its intended freshness, and
they can *compound*:

| Side | Failure mode | Example |
|---|---|---|
| **(a) Backend over-caches** | a cached row's TTL outlives the ingest that should have replaced it | `compute-scores` writes a new 多空 band at ~23:50 ([22](../backend/modules/22-data-ingest.md) §2 derived-compute), but a Redis snapshot keyed `(score, TW, 2330)` with a stale TTL still serves yesterday's band |
| **(b) Frontend over-caches** | the client holds a value past the backend's own freshness | RSC fetch cache / TanStack `staleTime` / browser `Cache-Control` still serves this morning's EOD after the post-close ingest landed |
| **(a)+(b) compound** | layered TTLs *sum* into a stale window larger than any single budget | backend 60s + HTTP `max-age=60` + TanStack `staleTime=60s` → a "1-minute-fresh" value can be **~3 min** old |

The invariant (top of doc) forbids all three. The rest of this doc is *how* each class meets it —
preferring **event-driven invalidation + a single-source-of-truth TTL per class** over hoping
independent TTLs line up.

---

## 2. Freshness classes → cache policy

Each data class maps to an **authority** (who produces the fresh value + *when*), a **freshness
budget** (the ceiling), and a **matching frontend policy**. Backend cache key is always
`(capability, market, symbol[, date])` ([19](../backend/modules/19-data-access-router.md) §5,
[16](../backend/modules/16-infrastructure.md) §3); the frontend key **mirrors it** (see [§3](#3-cache-key--versioning-alignment)).

| Class (capability) | Authority · when it lands | Freshness budget | Backend cache / invalidation | Frontend policy |
|---|---|---|---|---|
| **`price_intraday`** live-ish | shared demand-driven poller, **~5s** snapshot, market-open only, auto-stops on last disconnect ([16](../backend/modules/16-infrastructure.md) §2a) | seconds (delayed snapshot, *not* real-time — governance caveat) | Redis snapshot, `timing_class=live-ish` → **seconds only** ([16](../backend/modules/16-infrastructure.md) §3) | **SSE-pushed**, never client-polled. `quote` event updates the **Zustand live layer / motion values**, *not* TanStack cache (§6). No `staleTime` — the stream *is* freshness |
| **`price_eod`** | ingest `ingest-daily-postclose`, EOD OHLCV **~17:40 TW** (FinMind lag ~3h behind official — [22](../backend/modules/22-data-ingest.md) §1) | until next post-close | DuckDB partition (whole-date rewrite, atomic swap [22](../backend/modules/22-data-ingest.md) §4); Redis point-read TTL **`official-eod` = until next close** | RSC `revalidate` to **next post-close boundary** (not a fixed 60s); TanStack `staleTime` = same boundary. One-shot `GET /quote` (public) |
| **derived `score`** (多空) | `compute-scores` **daily ~23:50**, after 市值/官股 land ([22](../backend/modules/22-data-ingest.md) §2, §3) | once/day, at a known time | `stock_scores` write; cache invalidated on batch completion | changes **at most once/day** → `revalidate` to the post-batch boundary; **needle never re-animates on a quote tick** ([interaction-spec §2.3](interaction-spec.md#23-多空-score-gauge-71)) |
| **derived `topN` / rankings** | `compute-topN` **daily**, right after `compute-scores` | once/day | ranked lists cached post-compute | `revalidate` to post-batch boundary; `DataFreshnessBadge` "最後執行 …" |
| **derived `summary` / SWOT** | `compute-ai-summary` **weekly (Sat/Sun)**; on-demand only if the row is empty ([22](../backend/modules/22-data-ingest.md) §2) | once/week (or first-compute) | `ai_summaries` upsert; serve-as-is if present | `revalidate` weekly; teaser if unentitled |
| **derived `themes`** | `discover-themes` **~7-day window** LLM pass | ~weekly | cached theme tags | `revalidate` ~weekly; Premium+ |
| **`financials` / `revenue` / `calendar_events`** | event-driven ingest — 月營收 poll days 1–12, 財報 deadline windows, `expected→released` flip ([22](../backend/modules/22-data-ingest.md) §2, §4) | event-driven (bounded by deadline window) | upsert on natural PK; gap-detect enqueues targeted backfill | `revalidate` daily during window / on `released` flip; otherwise long. Show `資料日期` where the backend exposes it |
| **`chip_broker` / 分點 / 官股 (sponsor)** | `ingest-daily-late` **21:00–23:40**, T+1 ([16](../backend/modules/16-infrastructure.md) §4, [22](../backend/modules/22-data-ingest.md) §2) | once/day (T+1) | cache per `(dataset, stock, date)`; repeat reads never re-hit FinMind | `revalidate` to post-late-ingest boundary; **馬賽克** if unentitled (entitlement-keyed, §5) |
| **reference / `profile` / `sector_class`** | rarely changes | `timing_class=reference` sec–min ([16](../backend/modules/16-infrastructure.md) §3) | short Redis TTL | long RSC `revalidate` (hours); TanStack `staleTime` long |

**The rule the table encodes:** for batch-produced classes (score/topN/summary/themes/EOD) the
freshness budget is a **known daily/weekly boundary**, not an arbitrary duration — so the frontend
revalidate target is *that boundary*, and the "TTL that outlives ingest" failure ([§1](#1-the-problem-stated-precisely)a)
cannot occur. For `price_intraday` the budget is the SSE cadence itself; there is no client TTL.

---

## 3. Cache-key & versioning alignment

**Frontend keys mirror the backend key** so one symbol is *one coherent cache entry* wherever it
appears — screener ↔ watchlist ↔ Stock Detail ↔ a chat `rich_card` all share it:

```
TanStack queryKey:  [capability, market, symbol]          // e.g. ['score','TW','2330']
                    [capability, market, symbol, date?]   // date-scoped series (chart TF)
```

- A **TW / US / ETF** symbol crossing surfaces resolves to the same key (market is part of the key,
  so `2330@TW` never collides with a same-id symbol on another market — the backend guarantee
  [16](../backend/modules/16-infrastructure.md) §3 mirrored client-side).
- The chart timeframe drives **one** refetch with the date-scoped key ([interaction-spec §2.2](interaction-spec.md#22-chartwithdatasheet-711--the-analytical-primitive));
  the sheet + chart share it (one source of truth, never two fetches).

**Validator = the server's own version stamp.** The client should detect "my copy is older than the
server's" *cheaply*, without re-transferring the payload, and must **never render older-than-server**:

- **Preferred:** an `as_of` timestamp (and/or `version` for user-scoped state) on each response, and
  an HTTP **`ETag` + `If-None-Match`** conditional GET so a revalidate that finds nothing new returns
  `304` (no payload, no re-render). The client compares incoming `as_of`/`version` to what it holds
  and **discards any response that is not newer** (monotonic) — a late/duplicate reply can never
  regress the UI.
- User-scoped multi-device state already carries `version` for optimistic CAS: watchlist rename /
  reorder use `If-Match: <version>` → **409 on stale** ([16](../backend/modules/16-infrastructure.md) §10;
  [api-reference `PATCH /watchlists/{id}`](../backend/api-reference.md)). The client reuses that as its
  coherence validator for those entities.
- Live layer ordering validator = **`event_seq`** — deltas append **by `node_id`**, resume by
  `Last-Event-ID` ([interaction-spec §3.2](interaction-spec.md#32-last-event-id-resume--visually-seamless)).

> **✓ Backend read-model freshness contract (now specified — [16](../backend/modules/16-infrastructure.md) §3a,
> [api-reference Conventions](../backend/api-reference.md)).** Every cacheable read (`/quote`,
> `/analytics/*`, `/market/*`) returns an **`as_of`** timestamp (value production time) + a strong
> **`ETag`**; `If-None-Match` → **`304`** so a revalidate that finds nothing new costs no payload and
> no re-render. `Cache-Control: private, max-age=0, stale-while-revalidate=<class boundary>`. The
> `quote` SSE payload carries per-tick **`as_of` + `market_open`** ([16](../backend/modules/16-infrastructure.md) §2a)
> — the frontend stamps the `DataFreshnessBadge` from `as_of` and, once `market_open=false`, shows
> last-close/EOD (static) rather than a frozen "live" value. `as_of`/`version` is the monotonic
> discard validator.

---

## 4. Invalidation & push, not just TTL

TTL alone is a *hope that layers line up*. Prefer **event-driven invalidation** so a new
authoritative value invalidates the client's copy the moment it lands, rather than waiting out a
timer:

| Class | Push / invalidation mechanism | On reconnect / miss |
|---|---|---|
| `price_intraday` | **SSE `quote` stream** ([16](../backend/modules/16-infrastructure.md) §2a) — the authoritative push; there is no polling TTL to expire | `Last-Event-ID` replays by `event_seq`; replayed events **materialize instantly** (entrance animation suppressed, [interaction-spec §3.2](interaction-spec.md#32-last-event-id-resume--visually-seamless)) so a reconnect **never resurrects a pre-update snapshot** |
| batch analytics (`score`/`topN`/`summary`/`themes`) | ideally a **lightweight "data updated" signal** when `compute-*` completes → the client revalidates the affected keys instead of waiting a TTL | absent a push, `revalidate` to the **known post-batch boundary** (the batches run at a fixed daily/weekly time — [22](../backend/modules/22-data-ingest.md) §2) so the stale window is bounded to the boundary, not open-ended |
| `price_eod` / sponsor chip | post-close / post-late-ingest boundary revalidate; gap-detect + **next-morning reconcile ~08:30** ([22](../backend/modules/22-data-ingest.md) §4) means a *revised* value can land the next morning → the morning revalidate must pick it up | same boundary |
| entitlement change | **per-tool-call** resolution ([16](../backend/modules/16-infrastructure.md) §10) → busts the cached masked cell (§5) | — |

> **✓ Batch-completion invalidation signal (now specified — [22](../backend/modules/22-data-ingest.md) §2,
> [16](../backend/modules/16-infrastructure.md) §3a).** When `compute-scores` / `compute-topN` /
> `compute-ai-summary` / `discover-themes` commit, the ingest leader publishes a **`data.updated`**
> event `{capability, market, as_of, scope}`, relayed on **`GET /updates/stream`**; the client maps it
> to a `revalidateTag` and refreshes exactly the affected keys the moment fresh analytics exist. The
> same commit bumps the read-model `as_of`/`ETag` (§3), so the **boundary-anchored `revalidate`**
> remains the correct floor if a client isn't subscribed to the invalidation feed.

**Reconnect safety (why a dropped connection can't un-freshen).** Because live deltas append by
`node_id` and resume by `event_seq`, a reconnect replays *forward* from the last seen id — it can
only advance state, never rewind it. Combined with the monotonic `as_of`/`version` discard rule
(§3), no cache path — replay, retry, or a late duplicate — can serve a value older than what the UI
already showed.

---

## 5. RSC vs client-cache specifics (Next.js App Router)

Clear ownership per read (aligns [README §1](README.md#1-stack--foundations): RSC for data-heavy
reads, Zustand thin store for live SSE / thread / theme, TanStack for client query cache):

| Read | Owner | Policy |
|---|---|---|
| Cold page data (Stock Detail EOD panels, Market breadth, financials/chip tables) | **RSC server fetch** | `next: { revalidate: <class boundary> }`; tag with `revalidateTag('<capability>:<market>:<symbol>')` so a batch/boundary can invalidate precisely |
| Analytical reads that recompute daily/weekly (`score`, `topN`, `summary`) | **RSC** | `revalidate` to the post-batch boundary (§2); **never** `cache: 'no-store'` — that would refetch every navigation for a value that changes once/day |
| Truly per-request-fresh reads | RSC | `cache: 'no-store'` **only** where a value has *no* meaningful cache window (rare — most classes have a boundary) |
| Client-interactive queries (screener re-probe, chart TF refetch, watchlist quotes) | **TanStack** | `staleTime`/`gcTime` per freshness class (§2); dedupe in-flight (§6) |
| Live ticks (`quote`), active thread stream, theme | **Zustand thin store** ([README §1](README.md#1-stack--foundations)) | motion values / refs, never `useState` per tick (§6) |

**The three rules that stop layers multiplying:**

1. **One place defines the TTL per class.** A single shared `FRESHNESS[class] = { revalidate,
   staleTime, gcTime, cacheControl }` map is the source of truth; RSC `revalidate`, TanStack
   `staleTime`, and the `Cache-Control` header all read from it. Layers **mirror** one budget — they
   never each pick their own (which is how [§1](#1-the-problem-stated-precisely) windows sum).
2. **No double-caching.** A given read is owned by **exactly one** cache tier (RSC *or* TanStack,
   never both fronting the same value with independent TTLs). HTTP `Cache-Control` uses
   **`stale-while-revalidate`** so the browser serves the last good value *and* kicks a background
   refresh — it does not add a *third* opaque TTL on top of the two app-level ones.
3. **Update in place, never remount.** A live update mutates the data without unmounting the
   component: the **marquee scroll never resets on a data update** ([interaction-spec §2.8](interaction-spec.md#28-marquee-75)),
   the **AssetHeader ticks in place** ([interaction-spec §2.15](interaction-spec.md#215-assetheader-stock-detail-sticky-header)).
   Cache-bust triggers a *background revalidate*, not a hard reload (§6).

**Recommended `Cache-Control` posture per class:**

| Class | `Cache-Control` |
|---|---|
| `price_intraday` | `no-store` on `/quote/stream` (it's a live SSE, not a cacheable body) |
| `price_eod` | `private, max-age=0, stale-while-revalidate=<to next close>` — serve last close instantly, revalidate in background |
| batch analytics | `private, max-age=0, stale-while-revalidate=<to next batch boundary>` |
| reference/profile | `private, max-age=<minutes>, stale-while-revalidate=<hours>` |

---

## 6. Freshness without jank — performance & state-management posture

Freshness is a **first-class UX goal**, not a correctness footnote: the fresh value must arrive
*smoothly*. Blunt "always refetch" is a bug — it causes jank, layout shift, refetch storms, and
flicker, and is explicitly disallowed.

**Cache-bust ≠ hard reload.** Every invalidation is a **background revalidate (stale-while-revalidate)**:

- Keep showing the **last good value**; when the fresh one arrives, update **in place** and flash the
  tick (`--up-weak` / `--down-weak`, `dur.tick` in / `dur.tick-fade` out — [interaction-spec §1.3](interaction-spec.md#13-animation-catalog)).
- **Never blank the panel, never reset scroll/sort/selection.** Skeleton is for **true cold load
  only** — the *plain-data* loading class ([interaction-spec §4.1](interaction-spec.md#41-loadingstate--skeleton-always-narration-only-for-generated-analysis));
  a background revalidate shows **no skeleton and no spinner**.
- Source lag degrades **honestly**, never silently: a stale snapshot surfaces via the
  `DataFreshnessBadge` + `<Caveat>` "資料來源延遲/部分不可用，以下為最後可得"
  ([interaction-spec §4.3](interaction-spec.md#43-errorstate--inline-retryable-honest)), never shown as fresh.

**Performance posture:**

- **Client single-flight.** Dedupe in-flight requests for the same key (TanStack does this natively)
  — the frontend mirror of the backend **stampede guard** ([16](../backend/modules/16-infrastructure.md)
  §10). N components asking for `2330@TW` = one request.
- **Coalesce the `quote` fan-out.** SSE ticks are **batched into a single `requestAnimationFrame`
  paint** — not one render per event; live values use **motion values / refs, never `useState` per
  tick** ([README §1](README.md#1-stack--foundations)). A watchlist of N rows ticking every ~5s must
  **not** re-render the React tree N times.
- **Lean RSC payloads**; avoid double-caching TTL stacking (§5). `stale-while-revalidate` keeps
  first paint instant while freshness catches up in the background.

**State-management ownership (one value = one owner):**

| Value | Owner | Never |
|---|---|---|
| Cold reads (EOD panels, tables, analytics) | RSC server cache / TanStack query cache | — |
| Client query cache (staleTime/gcTime per class) | TanStack | not duplicated in Zustand |
| Live `quote` tick, active thread SSE, theme | **Zustand thin store** ([README §1](README.md#1-stack--foundations)) | not written into TanStack cache |

**How a `quote` tick updates the UI without invalidating the query cache.** The tick writes to the
**Zustand live layer / motion-value layer** keyed by `(market, symbol)` — the presentation reads
`live ?? cachedSnapshot`. The **TanStack cached snapshot is left untouched**, so **scroll, sort,
selection, and the chart `activeIndex` are all preserved** across live updates; the SSE stream and
the query cache do not fight over the same slot. The 多空 gauge reads the *score* query cache, not
the live layer — so it **does not re-animate on a quote tick** ([interaction-spec §2.3](interaction-spec.md#23-多空-score-gauge-71)).

**Perceived performance for user actions (optimistic + reconcile):**

- Watchlist **star / reorder** apply **optimistically** (instant UI), then reconcile: lexorank
  fractional key for reorder, **`version` CAS → 409** on multi-device conflict → row snaps back +
  toast, no flicker ([interaction-spec §2.4](interaction-spec.md#24-stockrow--watchlist-row-78--incl-drag-reorder);
  [16](../backend/modules/16-infrastructure.md) §10). Star toggle is idempotent (`PUT … ON CONFLICT DO NOTHING`).
- Skeleton **only** on true cold load; background revalidate is invisible. **`prefers-reduced-motion`**
  respected throughout — the tick color-flash is kept (Tier 1) but transforms/loops drop
  ([interaction-spec §1.2](interaction-spec.md#12-reduced-motion-tiers-what-collapses)).

---

## 7. "Old cache blocks new data" — failure scenarios + the guard

Each scenario is the invariant being violated by a specific cache path, with its detection +
invalidation guard:

| # | Scenario (old cache blocks new data) | Guard |
|---|---|---|
| 1 | **Post-close EOD landed (~17:40) but client shows yesterday's close** | RSC `revalidate` anchored to the **post-close boundary**, not a fixed duration (§2); the `as_of`/`ETag` conditional GET (§3) makes it a cheap `304`-or-new check; `stale-while-revalidate` background-refreshes and updates in place — no blank, no scroll reset (§6) |
| 2 | **`compute-scores` recomputed the band (~23:50) but Stock Detail shows the old band** | the **`data.updated` push** (§4) invalidates the `score` key the moment the batch commits; boundary `revalidate` is the floor if unsubscribed; gauge needle eases **once** to the new value on the new score fetch — never on a tick ([interaction-spec §2.3](interaction-spec.md#23-多空-score-gauge-71)) |
| 3 | **User upgrades tier but the masked 馬賽克 value stays cached blurred** | entitlement resolved **per tool call** ([16](../backend/modules/16-infrastructure.md) §10) → the next call returns the unmasked value and **busts the cached masked cell**; blur lifts (fade, no celebration — [interaction-spec §2.7](interaction-spec.md#27-馬賽克-teaser-74-module-07-4)). Client must key entitlement-gated reads so a tier change invalidates them, not serve the cached masked payload |
| 4 | **Multi-device watchlist reorder — device B still shows the old order** | `version` **CAS → 409** on the stale writer; lexorank fractional key avoids neighbor rewrite; loser jitter+retries and re-reads ([16](../backend/modules/16-infrastructure.md) §10; [interaction-spec §2.4](interaction-spec.md#24-stockrow--watchlist-row-78--incl-drag-reorder)) |
| 5 | **Reconnect resurrects a pre-update snapshot** | `Last-Event-ID` replay is **forward-only** by `event_seq`; monotonic `as_of`/`version` **discard** rule (§3) drops any reply older than what's shown |
| 6 | **Next-morning reconcile (~08:30) revised a value but client cached last night's** | morning `revalidate` boundary picks up the reconciled partition ([22](../backend/modules/22-data-ingest.md) §4) |
| 7 | **Layered TTLs sum into a stale window** | single-source `FRESHNESS[class]` map + one owner per read + `stale-while-revalidate` (not a stacked third TTL) — §5 rules 1–2 |

---

## 8. Freshness UX

Every cached-and-possibly-stale surface tells the truth about its currency — reusing the **existing**
components, no new visual primitive:

- **`DataFreshnessBadge`** on every quote/analytical/market surface — EOD vs 即時 vs "資料延遲"
  ([design-system §7.11](design-system.md#711-chartwithdatasheet--the-analytical-primitive)); screener results stamp
  "最後執行 14:32" ([interaction-spec §2.12](interaction-spec.md#212-screener-funnel--builder-gates-run-results)).
  Free = EOD, Premium = ~5s delayed — the badge states which ([README §6](README.md#6-usability-a11y--tw-conventions-non-negotiable),
  marquee note).
- **`<Caveat>` footer** — the **currentness** caveat on quote/market surfaces + the **non-trading**
  caveat on analytical ones, **required and non-removable** ([README §8](README.md#8-cross-cutting-governance-in-the-ui);
  [design-system §7.11](design-system.md#711-chartwithdatasheet--the-analytical-primitive)). Official OpenAPI = latest-EOD, no
  real-time guarantee ([governance](../backend/governance.md)) — the caveat says so; `/quote/stream`
  keeps the **delayed-snapshot** caveat ([16](../backend/modules/16-infrastructure.md) §2a), never
  claims true real-time.
- **Honest degrade** when a source lags: `tool.result.source_health` / a stale snapshot →
  "資料來源延遲/部分不可用，以下為最後可得" ([interaction-spec §4.3](interaction-spec.md#43-errorstate--inline-retryable-honest)) —
  **never silently show stale as fresh.**
- **Off-hours:** the live layer stops (poller idle — [16](../backend/modules/16-infrastructure.md) §2a);
  the marquee/quote surfaces show **last close, static** ([interaction-spec §2.8](interaction-spec.md#28-marquee-75)),
  badge reads EOD — not a frozen "live" value implying the market is still ticking.

---

## 9. Rules for engineers (do / don't)

**Do**
- Define **one** `FRESHNESS[class]` map; RSC `revalidate`, TanStack `staleTime`/`gcTime`, and
  `Cache-Control` all read from it.
- Anchor batch-produced classes (EOD, score, topN, summary, themes) to their **known daily/weekly
  boundary**, not an arbitrary duration.
- Mirror the backend key `(capability, market, symbol[, date])` as the TanStack `queryKey`.
- Treat every invalidation as a **background revalidate** — keep the last good value, update in place,
  flash the tick.
- Push live via SSE; dedupe in-flight (client single-flight); batch `quote` ticks into one rAF paint
  via motion values/refs.
- Discard any response whose `as_of`/`version` is **not newer** than what's shown (monotonic).
- Optimistic update for user actions; reconcile with `version` CAS 409.
- Stamp `DataFreshnessBadge` + `<Caveat>` on every quote/analytical surface; degrade honestly.

**Don't**
- Don't `cache: 'no-store'` a value that changes once/day (refetch storm).
- Don't front the same value with **two** cache tiers each holding an independent TTL.
- Don't write live `quote` ticks into the TanStack query cache (it would reset scroll/sort/selection).
- Don't `useState` per tick or re-render the tree per tick.
- Don't show a skeleton/spinner on a **background** revalidate — skeleton is cold-load only.
- Don't reset marquee scroll / remount a panel on a data update.
- Don't serve a cached **masked** cell after an entitlement upgrade.
- Don't render a value older than the server's (`as_of`/`event_seq`/`version` is the guard).
- Don't present a delayed/EOD value as 即時 — the badge + caveat must be truthful.

---

## 10. Cross-references

**Backend (grounding):**
- Live-quote poller + SSE fan-out; concurrency model (single-flight, `event_seq`, per-call
  entitlement, `version` CAS): [../backend/modules/16-infrastructure.md](../backend/modules/16-infrastructure.md) §2a, §3, §10.
- Cache key `(capability, market, symbol)`; freshness classes / `timing_class`:
  [../backend/modules/19-data-access-router.md](../backend/modules/19-data-access-router.md) §2, §4, §5.
- Derived store, idempotent upsert + gap-detect + next-morning reconcile, daily/weekly compute
  batches: [../backend/modules/22-data-ingest.md](../backend/modules/22-data-ingest.md) §0, §2, §4.
- Currentness caveat, live-poller carve-out §9b, latest-EOD-no-realtime:
  [../backend/governance.md](../backend/governance.md).
- REST + SSE surface (`/quote`, `/quote/stream`, `/analytics/*`, `/market/*`):
  [../backend/api-reference.md](../backend/api-reference.md).

**Web (behaviour / tokens this doc composes with):**
- Stack + `DataFreshnessBadge` + currentness caveat: [README.md](README.md) §1, §6, §8.
- Live layer, `Last-Event-ID` resume, marquee/AssetHeader in-place tick, loading classes,
  source-health degrade: [interaction-spec.md](interaction-spec.md) §2.2, §2.3, §2.4, §2.8, §2.15, §3, §4.
- `DataFreshnessBadge` / `<Caveat>` / `ChartWithDataSheet` freshness footer, tokens:
  [design-system.md](design-system.md) §7.11.

**Backend read-model freshness contract (specified — the three gaps this doc raised are now folded
into the backend spec):** (1) response-level **`as_of` + `ETag`/`If-None-Match`** conditional GET on
the read surface — [16](../backend/modules/16-infrastructure.md) §3a, [api-reference Conventions](../backend/api-reference.md);
(2) **`data.updated`** batch-completion push on **`GET /updates/stream`** — [22](../backend/modules/22-data-ingest.md) §2,
[16](../backend/modules/16-infrastructure.md) §3a; (3) `quote` SSE payload carries per-tick **`as_of`
+ `market_open`** — [16](../backend/modules/16-infrastructure.md) §2a.
