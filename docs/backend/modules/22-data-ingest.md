# Module: Data ingest / ETL (`backend/src/ingest/`)

The layer that **populates our own derived market-data store** (DuckDB/Parquet + Redis,
[modules/16](16-infrastructure.md) §3) on a schedule, so the platform owns a time series the
government OpenAPIs don't keep. Everything downstream — the context pipeline (module 02/04),
analytics/scoring/screener (module 20), the calendar (module 18) — reads this store instead of
hitting sources live. Ingest is the **only writer** of the derived store.

## 0. Three distinct scheduled activities — do not conflate

The doc set has **three** kinds of scheduled network activity. They must not share a scheduler
lane or a quota bucket:

| | What it is | Governance | Trigger | Quota model |
|-|-----------|-----------|---------|-------------|
| **(A) Platform ingest** (this module) | Bulk-building the shared derived store; unattended, market-wide | **§9c carve-out** (new) | trading-calendar cron | whole-market **by-date** (≈1 call/dataset/day) |
| **(B) Live-quote poller** ([16](16-infrastructure.md) §2a) | 5s `price_intraday` snapshots, only for symbols with a live subscriber | §9b | user opens `/quote/stream` | per-subscribed-symbol, refcounted |
| **(C) User jobs / alerts** ([14](14-scheduler-alerts.md)) | A Pro user's own scheduled alert/watchlist refresh | §9a | user creates a rule | per-user sub-quota |

Ingest (A) writes the EOD store and never touches the live snapshot path; the live poller (B)
reads snapshots and never writes the EOD store; user jobs (C) read the store per-user. **Three
separate quota buckets** so a backfill can never starve live Pro users (infra §4 fair-share).

## 1. The core reason ingest exists

- **Gov OpenAPIs (TWSE/TPEX/TAIFEX) are latest-snapshot-only** — no params, no date range, no
  backfill. To have *history* we must poll daily and store.
- **FinMind is the only date-range/backfill engine.** So ingest's primary writer is **FinMind
  whole-market-by-date** (omit `data_id` → one call returns the whole market for a date); gov
  OpenAPI is used **only** where it's same-day-authoritative and FinMind lags (處置/注意股, ETF
  NAV/折溢價, sub-indices).
- **Timing anchor = FinMind's documented `Update:` times, not a gov clock.** The official
  TWSE/TPEX/TAIFEX/TDCC pages publish a *data basis* (e.g. TDCC = "last business day of the week
  close"), **not** a contractual clock-time release SLA. Since ingest consumes FinMind (the
  history engine), every cron anchors on FinMind's `Update:` time (+~10 min) with a reconcile
  pass — not on an assumed exchange clock. **Lag chain:** official publish → FinMind (T+hours) →
  our cron. E.g. price is on TWSE by ~14:30 but FinMind at 17:30, so our EOD is ~3h behind
  official — acceptable for a non-trading EOD product; if a same-day-by-15:00 price is ever
  needed, pull the TWSE OpenAPI snapshot directly (the reserved same-day-authoritative path).

## 1a. Write path — DataFrame → Parquet, no CSV, bulk COPY (never row-INSERT)

The fetch→store path is fixed for speed and correctness:

1. **Fetch → pandas DataFrame.** The FinMind SDK already returns a DataFrame; yfinance
   `download` too. No intermediate file.
2. **Register the DataFrame in DuckDB zero-copy** (Arrow) and **bulk-write Parquet**:
   `COPY (SELECT * FROM df) TO '<temp>/part.parquet' (FORMAT parquet)` — *not* row-by-row
   `INSERT` (DuckDB is columnar; per-row insert is the anti-pattern, orders of magnitude slower).
3. **Atomic swap** (§4): write to a temp dir → `os.rename` into the `market=…/date=…` partition
   tree → flip the manifest pointer.

- **No CSV staging.** CSV = serialize-to-text → write disk → re-read → parse → type-guess.
  **Parquet is the store format** (already columnar/typed/compressed), so the write target *is*
  the query format — one file, no conversion. CSV is only ever a manual debug export, never the
  pipeline. (DuckDB `read_csv` is fine for ad-hoc imports; it is not the ingest path.)
- **Readers** then `read_parquet(glob, hive_partitioning=true)` read-only (§7) — they never touch
  the writer's process or a `.duckdb` file.

## 2. Cadence table (dataset → cron → source → inc/full → store)

> **Implementation status — the real schedule is smaller and differently architected than this
> section's cadence table.** There is no cron-job-group scheduler; the real architecture
> (`ingest/daemon.py`'s `IngestDaemon`) is a **leader-gated, wall-clock poll loop**: one pass
> (`run_due_jobs`) checks a fixed `SCHEDULED_JOBS` tuple of `(name, fire_time, run_fn, cadence)`
> entries against the current Taipei-local time and fires whatever has newly become due, marking
> it handled *before* running it (so a failed run is never retried in a tight loop — the next
> window's pass, or an explicit backfill, retries). `ingest/__main__.py` (`python -m ingest`) wraps
> this in a real sleep/wake loop with graceful SIGTERM/SIGINT handling; there is no separate
> "cron job group" concept in code.
>
> **Real job count today: 17 scheduled jobs, not the ~25–35/day this table's whole-market bundle
> implies.** 10 are whole-market FinMind pulls, from `ingest.jobs.ALL_WHOLE_MARKET_JOBS` (the same
> canonical list §6b's backfill CLI walks): `price_eod` (17:40), `institutional_flow` (20:10),
> `financials`/`balance_sheet`/`cash_flow`/`dividends`/`month_revenue` (all 21:30), `market_value`
> (23:40), `active_etf_holdings`/`active_etf_holding_changes` (both 21:15). 2 are daily derived-compute
> passes over the store, not FinMind pulls (module 20): `screener_flags` (20:30, after price +
> institutional-flow land) and `scoring_batch` (23:55, after every other daily job — it depends on
> market-cap and financial-statement families, which don't land until 21:30/23:40). 2 are monthly
> reference-data jobs (also module 20): `theme_industry_chain` and `theme_tiers`, both fired
> 00:30/18:30–18:35 on a rolling ~4-week cadence, sequenced so tiers run after the membership seed.
> 3 more are the US-market ingest jobs (§2c, below): `us_symbol_universe` (05:30, monthly),
> `us_price_eod` (06:00, daily), and `us_fundamentals` (07:00, weekly). Every other dataset this
> cadence table below documents (市場總法人/借券, 報酬指數, PER/PBR/漲跌停, 處置/注意股,
> VIX, 期貨/選擇權regular pulls, ETF NAV/PCF scrape, 融資融券 family, 分點/八大官股/產業鏈資金流, 集保股權
> 分散, all of §"Monthly" and §"Event-driven") **is still design intent — not one of the 17 real
> scheduled jobs.** Chain-order dependencies (screener_flags/scoring_batch after their upstream
> data jobs) are real and enforced by fire-time ordering, exactly as designed. Where this section's
> narrative cadence/cron framing conflicts with the 17-job list above, the 17-job list is the real
> one — treat everything else in §2/§3 below as the still-relevant design target for datasets not
> yet wired, not as a description of what fires today.

All crons **Asia/Taipei**, gated on `TaiwanStockTradingDate` (skip weekends/holidays) + the
NCDR typhoon overlay (§6). FinMind whole-market pulls omit `data_id` (⇒ **≥Backer token**,
§5). "Land" = DuckDB/Parquet.

### Daily — post-close (staggered to each dataset's FinMind update time)
Times = FinMind's documented `Update:` (`finmind/llm.md`, the authoritative anchor — see note).
**Cron fires ≥ FinMind time + ~10 min buffer.** Gov exchanges publish a *data basis*, not a clock
SLA, so we anchor on FinMind, not on a gov clock time.

| Dataset | Source | FinMind update | **Cron (TW)** | Inc/Full |
|---------|--------|---------------|---------------|----------|
| 市場總法人 / 借券成交 | FinMind | 15:00 | 15:10 | append |
| 報酬指數 `TaiwanStockTotalReturnIndex` | FinMind | 16:50 | 17:00 | append |
| EOD OHLCV `TaiwanStockPrice` (+`PriceAdj`) | FinMind whole-market | **17:30** | **17:40** | append day |
| PER/PBR/殖利率, 漲跌停 | FinMind | 18:00 | 18:10 | append |
| 處置/注意股 | FinMind + **gov OpenAPI (authoritative same-day)** | — | 18:00 (+ AM reconcile) | append + snapshot-diff |
| 臺指選擇權 VIX `TaiwanOptionVix` | FinMind | 18:00 (Mon–Sat) | 18:10 | append |
| 個股三大法人 (Wide) `...InstitutionalInvestorsBuySell` | FinMind whole-market | **20:00** | **20:10** *(20:00 slot, NOT the ≤18:30 bundle)* | append |
| 期貨/選擇權日成交 + 三大法人 (regular) | FinMind whole-market | *no documented Update* † | 20:10 | append |
| ETF NAV/IOPV/折溢價, units, passive PCF 成分股/權重 (`etf_holdings`) | `etf_pcf` scrape | — | 15:30 (+ 20:00 recheck) | full snapshot/day |
| 主動ETF 成分/權重/張數 `TaiwanStockActiveETFHolding(Change)` (feeds `etf_holdings`, **sponsor**) | FinMind Sponsor | 21:00 | 21:15 | append snapshot/day |

† `TaiwanFuturesDaily`/`TaiwanOptionDaily`/regular 期選法人 have **no documented FinMind `Update:`**
time (TAIFEX itself is ~15:00). 20:10 is a safe assumption; the AM reconcile covers any slippage.

### Daily — late (21:00+, late-arriving; own sub-job)
融資融券 (+Total), 外資持股 `TaiwanStockShareholding`, 信用額度總量管制, 暫停融券, 大盤融資維持率 (all
FinMind whole-market, **21:00** → cron **21:05**) · 當沖 values `TaiwanStockDayTrading` (~21:30 →
21:40) · **Sponsor chip:** 分點 `TaiwanStockTradingDailyReport` (21:00 → 21:15), 八大官股 (**23:30**
→ 23:40), 產業鏈資金流 (after price, 23:40), 主動ETF holdings/change (21:15+) — FinMind **Sponsor**,
via **`storage_objects` whole-day parquet** where available (分點 = 1 bulk parquet/day) · 市值
`TaiwanStockMarketValue` (+Weight) (**23:30** → 23:40).

> **⚠ Next-morning only — must go in the reconcile pass (§4), NOT the 20:00 slot:** 期/選 **盤後
> (夜盤)三大法人** (`...InstitutionalInvestorsAfterHours`) — the night session runs 15:00–05:00 and
> 法人 figures are only final the following business-day morning. A same-day 20:00 pull under-reports it.

### Weekly
集保股權分散/分級 `TaiwanStockHoldingSharesPer` (大戶/散戶/股東人數) — TDCC basis = **Fri close**, but
the file commonly **lands Sun/Mon** (FinMind has **no documented `Update:` time** for it). Cron
**Sun 06:00 + Mon reconcile** (a Sat-only pull is materially stale — verify empirically). FinMind
whole-market (Backer ⇒ `public`). ETF 受益人數 (if weekly) — `etf_pcf`/SITCA scrape.
**Do not build a second TDCC adapter** — 千張大戶比率 is derived from level-15 of this dataset
(data-catalog §2.10).

### Monthly
- **月營收** `TaiwanStockMonthRevenue` — statutory deadline **10th**; **poll daily days 1–12**,
  upsert by `(stock, revenue_year, revenue_month)`, flip `expected→released` (module 18 state).
- **財報** `FinancialStatements`/`BalanceSheet`/`CashFlows` — poll daily in **±window** around
  deadlines (5/15, 8/14, 8/31 金融, 11/14, 3/31); early/late filing = its own signal.
- 景氣對策信號 (month-end); 股利/除權息 (daily in 旺季 Jul–Sep, else weekly).

### Event-driven (worker-queue pollers, coarse cadence — not cron, not high-frequency)
| Group | Cadence | Source |
|-------|---------|--------|
| MOPS 重大訊息/董事會/庫藏股/內部人/法說會/自結 | list refresh **15–30 min** trading / hourly off-hours | `mops` scrape (backoff + circuit breaker, infra §6/§7) |
| News RSS / GDELT / Finnhub | 10–15 min | `news` |
| PTT Stock/Tech_Job (+off-topic segregated) | 15–30 min | `ptt` scrape |
| Index rebalance (TIP / MSCI / FTSE) | weekly scrape (forward-dated) | `calendar_ext` |
| Macro release calendar | scrape ~once/year per agency | `calendar_ext` |
| IPO 承銷/抽籤 | daily list poll | `calendar_ext` |
| yfinance analyst consensus/目標價/ESG | **weekly** (slow-moving, coverage-gated) | `yfinance` |
| Corporate actions (減資/分割/面額/下市) | daily (cheap) | FinMind → drives 還原股價 |

### Derived-compute jobs (analytics — module 20; **consume the store, don't hit FinMind**)

> **Implementation status.** Only the first two rows below are real — `screener_flags` and
> `scoring_batch`, both in the real 17-job `SCHEDULED_JOBS` tuple (§2 top callout). Their real
> names/times differ from this table's original `compute-*` naming: the real job is called
> `screener_flags` (fires **20:30**, not a `compute-topN` ranking pass — module 20 §5a's tag
> screener has no "top-N ranking of survivors" step at all, since there is no gates→setup→rank
> funnel), and `scoring_batch` (fires **23:55**, matching "after 市值/官股 land" as designed, but
> writing the real five-factor `stock_scores` cache, not a Stage-C top-N rank of a funnel that
> doesn't exist). `compute-ai-summary` and `discover-themes` are **not real** — there is no
> `ai_summaries` table, no SWOT feature, and no LLM theme-discovery job (module 20 §3/§5b already
> covers what shipped instead: the theme layer's real jobs are `theme_industry_chain`/`theme_tiers`,
> monthly, and Layer 3 concept research is an unscheduled operator CLI, `python -m
> analytics.themes` — never a cron job).

| Job (real name) | Cadence | Depends on | Cost | Notes |
|-----|---------|-----------|------|-------|
| **`screener_flags`** (tag screener, module 20 §5a) | **daily**, 20:30, after `price_eod` (17:40) + `institutional_flow` (20:10) | `price_eod`, `institutional_flow` | pure compute (no LLM) | evaluates the 29-tag catalog over price/flow history; writes only true `(symbol, tag)` rows to the `screener_flags` Parquet dataset |
| **`scoring_batch`** (多空 5-factor, module 20 §2) | **daily**, 23:55, after every other daily job (needs `market_value`/financial-statement families, which land at 21:30/23:40) | `price_eod`, `institutional_flow`, `financials`, `balance_sheet`, `cash_flow`, `market_value` | pure compute (no LLM) | winsorize/z-score → percentile → blend both profiles for every symbol in one pass; upserts the relational `stock_scores` cache |
| **`theme_industry_chain`** (module 20 §5b Layer 1) | **monthly**, 00:30, rolling ~4-week gap | FinMind whole-market industry-chain classification (a live FinMind call, not the Parquet store) | pure compute (no LLM) | deterministic industry-theme + membership seed |
| **`theme_tiers`** (module 20 §5b Layer 2) | **monthly**, 00:30, immediately after `theme_industry_chain` in the same pass | `theme_industry_chain`'s memberships + `config/theme_tiers.yaml` | pure compute (no LLM) | curated map + keyword-heuristic tier backfill |
| `compute-ai-summary` (智能摘要 + SWOT) | — | — | — | **not real** — design intent, module 20 §3 |
| `discover-themes` + figure events | — | — | — | **not real** — superseded by the real three-layer theme design, module 20 §5b |

Layer 3 concept-theme research + in-theme supply-chain edges (module 20 §5b) is real but
**deliberately unscheduled** — an operator runs `python -m analytics.themes` by hand, since it is
the only analytics job that spends on LLM calls and does live web research.

**Dependency chain (daily, real):** `price_eod` + `institutional_flow` → `screener_flags`;
`price_eod` + `institutional_flow` + `financials`/`balance_sheet`/`cash_flow` + `market_value` →
`scoring_batch`. Both jobs are calendar- and leader-gated exactly like the whole-market pulls: a
non-trading day is a journaled skip, a non-leader pass writes nothing, and a run that finds no
upstream history in its read window writes a real, journaled empty result rather than an error —
never scores/flags stale or partial data.

**On completion → cache-invalidation signal.** The seam for this is real
(`ingest/data_updates.py`'s `DataUpdatePublisher` Protocol + `NoOpDataUpdatePublisher` default,
wired onto `IngestContext.data_update_publisher`), and the **whole-market FinMind jobs in
`ingest/jobs.py` do call `publish_data_updated({capability, market, as_of, scope})`** on landing —
only the leader publishes (same `is_leader()` gate as the write itself), and with no concrete
transport wired the default is a no-op, so an ingest run still lands its partition either way and
clients fall back to boundary-anchored revalidation. **`scoring_batch` and `screener_flags`
(module 20's two real derived-compute jobs) do not call this publisher** — confirmed by reading
both modules directly; they write their result and journal the run, nothing more. So the
"`compute-scores`→`{score,…}`" style per-job event-name mapping this section originally described
is design intent for those two jobs specifically, not yet wired, even though the underlying
publish mechanism itself is real and already exercised by the whole-market pulls (and, per §2c
above, by the US-market price/fundamentals jobs too). **API workers relaying this onto
`/updates/stream` is real and confirmed wired end-to-end**, not just the publish side:
`api/routers/context.py`'s `GET /updates/stream` opens `state.data_update_broker.reader_factory
.updates()` and relays every frame as an SSE event to the client, no polling or boundary-anchored
fallback involved. That broker is built by `events/live_quote_factory.py::build_data_update_broker`,
which wires **both sides to the same backend** per `event_bus_backend`: the in-memory backend
shares one `InMemoryDataUpdateBroker` object as both publisher and reader (a same-process publish
reaches the reader directly), and the Redis backend pairs a `RedisDataUpdatePublisher` (leader
side) with a `RedisDataUpdateReader` (API-worker side) over the same pub/sub channel — a real
cross-process relay. So clients **do** revalidate exactly the affected keys
([web data-freshness.md](../../web/data-freshness.md) §4) the moment a whole-market partition
lands, for every job that calls `publish_data_updated` (the whole-market FinMind pulls and the
US-market jobs); `scoring_batch`/`screener_flags` still don't call the publisher (above), so their
completions are not yet relayed this way.

### 2c. US-market ingest jobs (real — `ingest/us_jobs.py`, `ingest/us_universe.py`)

The three US-market counterparts to the TW whole-market jobs above, all real, scheduled, and
wired into `SCHEDULED_JOBS` (§2 top callout) alongside them:

- **`job_us_symbol_universe`** (`ingest/us_universe.py`, monthly, fires 05:30 — before the price/
  fundamentals jobs below so they have a roster to read) — discovers the tracked US symbol
  universe via `sources.yfinance.screener.run_screener`, paginated by market-cap band
  (`_MARKET_CAP_BANDS`) since Yahoo hard-caps one custom screen at 250 results with no `offset`
  param. Five disjoint `intradaymarketcap` bands, each independently queried and (where a band's
  real match count exceeds 250) truncated to its own top-250 by market cap — an honest, disclosed
  approximation (~1,000–1,500 symbols, roughly large-and-mid-cap) rather than every US-listed
  symbol. Writes the combined roster to its own `us_symbol_universe` Parquet partition; not
  calendar-gated (reference data, not a trading-day pull), leader-gated like every other write.
- **`job_us_price_eod`** (`ingest/us_jobs.py`, daily, fires 06:00 — after US markets' ~16:00 ET
  close has cleared yfinance's own upstream landing buffer) — one `batch_download` (`yf.download`)
  call for the whole tracked universe read from `job_us_symbol_universe`'s latest partition, the
  yfinance equivalent of `job_price_eod`'s single FinMind whole-market call. Calendar-gated the
  same Mon-Fri way any market's calendar degrades to; an empty universe (the discovery job has
  never run) is a graceful skipped run, not a crash.
- **`job_us_fundamentals`** (`ingest/us_jobs.py`, weekly, fires 07:00) — financials/balance-sheet/
  cash-flow/dividends/splits have no bulk endpoint in yfinance, so this is the one accepted
  per-symbol-fan-out exception in this module: it calls the existing per-symbol
  `YFinanceAdapter.fetch` once per symbol per capability, paced (`_YFINANCE_THROTTLE_SECONDS`) to
  avoid the rate-limiting confirmed empirically on an unthrottled first run. One symbol's failure
  never aborts the batch; the run journals `PARTIAL` if any symbol failed but other symbols still
  landed, `FAILED` only if every symbol failed.

All three publish `data.updated` on a successful leader write — `job_us_price_eod` and
`job_us_fundamentals` call `ctx.data_update_publisher.publish_data_updated({capability, market:
US, as_of, scope})` per dataset written, the same signal the TW whole-market jobs publish (see
"On completion" above); `job_us_symbol_universe` does not, since nothing downstream revalidates
against the roster itself the way it does against price/fundamentals partitions.

### Backfill (one-time / on-demand ops job — checkpointed, resumable)
FinMind **date-range paginated** by year chunks, whole-market by `start_date`, per dataset;
tick/minute-K/分點 via `storage_objects` whole-day parquet loop. Runs on **Sponsor**, throttled
~1.5/s, single-flight, resumable checkpoint per `(dataset, date-cursor)`, **off-peak, own bucket**.

**Backfill depth is per-dataset, not a global start** (from `finmind/llm.md` data-ranges):
price/PriceAdj **1994-10**, 損益表 **1990-03**, 月營收 2002-02, 融資融券 2001, 三大法人(Wide) 2005,
期貨 daily 1998-07 · **shallow / Sponsor-only:** 分點 **2021-06**, 八大官股 2021-06, 主動ETF **2025-05**,
集保股權分級 2010-01, 盤後法人 2021-10, 市值比重 2024-10. **No historical backfill** for tick /
minute-K (`storage_objects` exist only from FinMind's feature-launch date) — set each dataset's
`backfill_start` from its documented range; don't assume a uniform history.

### Missing datasets to add as scope allows (from the verification pass)
`TaiwanOptionVix` (added above); and per data-catalog §2.2/§2.9: convertible-bond dailies
(`TaiwanStockConvertibleBondDaily` + 法人), 期/選 大額交易人 OI (`Taiwan*OpenInterestLargeTraders`),
鉅額交易 (`TaiwanStockBlockTrade`), 借貸擔保品餘額 (`TaiwanStockLoanCollateralBalance`).

## 3. Job groups (the cron bundles)

> **Implementation status.** These groups are, and always were, a documentation grouping of the
> cadence table above — there is no group concept in code. The real `IngestDaemon` has one flat
> `SCHEDULED_JOBS` tuple of 17 jobs with no grouping (§2's top callout has the full real list).
> Below, each group's real jobs are called out inline; everything else in that group is still
> design intent.

1. **`ingest-daily-postclose`** (15:10–20:10 staggered per §2 cron column) — real: **EOD prices
   (`price_eod`, 17:40)** and **個股三大法人 (`institutional_flow`, 20:10)**. Still design intent:
   市場總法人/借券 (15:10), 報酬指數 (17:00), PER/漲跌停 (18:10), 處置/注意 + VIX (18:00–18:10),
   期/選 daily + regular 法人 (20:10), ETF NAV/PCF.
2. **`ingest-daily-late`** (21:05 + 23:40 sub-job) — real: **`financials`/`balance_sheet`/
   `cash_flow`/`dividends`/`month_revenue`** (all 21:30), **`market_value`** (23:40),
   **`active_etf_holdings`/`active_etf_holding_changes`** (both 21:15). Still design intent:
   融資融券, 外資持股, 信用總量, 大盤維持率, 當沖 values, 分點, 八大官股, 產業鏈資金流.
2b. **`ingest-reconcile`** (next morning ~08:30) — still design intent; no reconcile pass exists
   in the real 17-job schedule today.
3. **`ingest-weekly`** — still design intent, for TW; the real weekly slot in the schedule is the
   US-market `us_fundamentals` job (below), not any TW dataset.
4. **`ingest-monthly`** — real: **`theme_industry_chain`/`theme_tiers`** (fired 18:30/18:35,
   rolling ~4-week cadence, module 20 §5b Layers 1–2) and **`us_symbol_universe`** (05:30, same
   rolling cadence, §2c). Still design intent: 月營收 (daily days 1–12, though `month_revenue`'s
   real cadence is the fixed 21:30 whole-market slot above, not this per-company deadline-window
   design), 財報 (deadline windows), 景氣信號, 股利.
5. **`ingest-event`** — still design intent; no worker-queue/coarse-poller mechanism exists yet
   (MOPS/news/PTT/rebalance/macro/yfinance-weekly are all unbuilt).
5b. **derived analytics** (module 20 — consume the store, not FinMind) — real: **`screener_flags`**
   (daily, 20:30, after `price_eod` + `institutional_flow`) and **`scoring_batch`** (daily, 23:55,
   after every other daily job). Not real: any `compute-topN`/`compute-ai-summary`/
   `discover-themes` job — see module 20 §3/§5 for what's design intent vs. what shipped instead.
5c. **`ingest-us-market`** — real, the three US-market jobs (§2c above): **`us_symbol_universe`**
   (monthly, 05:30), **`us_price_eod`** (daily, 06:00), **`us_fundamentals`** (weekly, 07:00).
6. **`ingest-backfill`** — real, but as a **one-shot operator CLI**
   (`python -m ingest.backfill_cli`, §6b), not a scheduled cron bundle: checkpointed/resumable via
   the same `backfill_range(only_missing=True)` gap-diff the daily jobs' own catch-up uses.

Runs on the **leader-elected scheduler workers** (infra §5 — real today only in the single-process
`AlwaysLeader` sense; see [§6c](#6c-leader-election--still-a-single-process-stub-not-real-cross-process-election)
for exactly what "leader-elected" means right now). The real daemon
has **no separate task-broker/queue and no thread/process pool for scrapers yet** —
`IngestDaemon.run_due_jobs()` simply awaits each due job in-process within one pass. The
broker/queue + thread-pool-scraper architecture below remains design intent for the event-driven
scrapers (group 5) once they exist.

## 4. Idempotency, reconciliation, gap-fill

- **Idempotent re-runs** — the natural grain is a **whole date partition** (we always pull
  whole-market-by-date, so we have the full day). **Rewrite the whole date partition** from the
  fresh pull — *not* a row-level `MERGE` (DuckDB-over-Parquet has no in-place MERGE into Parquet;
  see [§7](#7-store--concurrency--duckdbparquet-and-when-to-reconsider)). Natural PKs for
  validation/dedup within a partition: price/法人/融資券 `(stock_id, date)`; 月營收
  `(stock_id, revenue_year, revenue_month)`; 財報 `(stock_id, date, type)`; 分點
  `(stock_id, securities_trader_id, date)`.
- **Atomic partition swap (readers must never see a half-written partition).** A partitioned
  write emits **multiple files per partition** and `OVERWRITE` **deletes-then-writes** — both
  non-atomic, so a reader globbing mid-write sees empty/partial data. Ingest therefore writes each
  date partition to a **temp dir → `os.rename` into place** (atomic on one filesystem), with
  `FILENAME_PATTERN '{uuid}'`, and publishes a **manifest/version pointer** readers resolve first
  so a swap is a single pointer flip. On leader startup, **sweep orphaned temp dirs** from a
  crashed mid-`COPY` write. (Reader/writer isolation: [§7](#7-store--concurrency--duckdbparquet-and-when-to-reconsider), infra §3/§10.)
- **Detect missed days** — diff expected trading days (`TaiwanStockTradingDate`) vs distinct
  dates present per dataset; any gap (crash/outage/FinMind lag) → enqueue a **targeted date-range
  backfill** (same code path as first-run, scoped to the gap). The journal makes gaps visible.
- **Late-arriving data** — stagger crons to each dataset's update time (融資融券 21:00, 官股/市值
  23:30, 當沖 values ~21:30); add a **next-morning (~08:30) reconcile pass** that re-pulls yesterday
  and upserts (via the atomic partition swap above), catching values that landed after the primary
  run — **and the datasets that are only final the next morning**: 期/選 **盤後(夜盤)三大法人**
  (`...InstitutionalInvestorsAfterHours`) and the weekly 集保股權分散 (see §2, both land late).
- **Derived change columns are diffed on write, not stored twice** — e.g. the ETF-holdings view's
  **持倉比重增減 / 股數增減** (module 03 `get_etfs_holding_stock`) are computed by diffing the current
  `etf_holdings` date partition against the prior one; only the per-date snapshots are stored
  (append-daily partitions), so the UI's **資料日期 picker** (e.g. 07-31 / 07-30 / 04-30) can compare
  any two dates for free. The **操作日報 forward view** (`get_active_etf_report`) is the same diff
  the other way round: its **狀態 新增/刪除/加碼/減碼 is a derived classification** of the張數 delta
  (new position / dropped / +shares / −shares), and 變動張數·變動幅度·權重變動%·基金規模較前日 are all
  computed from consecutive partitions — not stored. Same pattern for 法人連買 streaks,
  外資持股率變化, revenue MoM/YoY.
- **Deadline-window data** (月營收/財報) — no per-company scheduled date; model as `expected`
  markers (module 18), poll through the window, upsert as rows land, flip `expected→released`,
  surface early/late filing as a signal.
- **Data-quirk validators (catalog §6)** — `TaiwanStockPrice` publishes `0` on no-trade days →
  filter `close > 0`, **not** volume; prefer `PriceAdj` for a gap-free adjusted series; 興櫃
  `open` = prev-day average (can fall outside [min,max]); 分點 emerging market-maker rows show
  price=0 legitimately.

## 5. Quota budgeting — whole-market-by-date is non-negotiable

- **Whole-market-by-date** (`TaiwanStockPrice` etc. **without `data_id`, by `start_date`**) =
  **≈1 call/dataset/day**. The 10 real whole-market FinMind jobs wired today
  (`ingest.jobs.ALL_WHOLE_MARKET_JOBS`, §2/§6b) are **≈10 calls/day**; the originally-designed full
  daily bundle (once every still-design-intent dataset in §2 is wired) would be **~25–35
  calls/day**. Either figure is trivially inside the platform's own operator-held FinMind token's
  rate ceiling, whichever plan it's on. **This is the design.** (This is FinMind's own published
  per-plan rate limit on the platform's single ingest token — a different concept from the
  per-user Sponsor-pool reservation described in [modules/12](12-multi-user-tiers.md) §4, which
  gates *user-facing* Sponsor-tier tool calls, not ingest.)
- **Per-symbol is the trap** — ~2,000+ symbols × datasets = thousands of calls/dataset/day; one
  dataset alone would exhaust even a generous hourly ceiling in minutes. **Never ingest
  per-symbol for market-wide EOD.** Enforce whole-market at the router/adapter level; alert if a
  market-wide job issues >(datasets) calls.
- **Tier reality:** whole-market pulls require **≥Backer** (Free is a per-symbol interactive
  tier and 403s on whole-market — fail loud in preflight, surface via M5Q). The 分點/官股/產業鏈/
  主動ETF/tick datasets are **Sponsor-only**. Steady-state daily ingest is comfortable on Sponsor;
  backfill is the only real pressure → own bucket, off-peak, throttled.

## 6. Trading-calendar awareness

- Gate every cron on **`TaiwanStockTradingDate`** (FinMind, Free) — authoritative trading-day
  list; refresh monthly (published ahead).
- Overlay the **NCDR/DGPA closure feed** (`ncdr_closure.py`) for **typhoon days**: a scheduled
  trading day can become a closure — suppress that day's ingest and mark it non-trading. This
  also matters for `PriceAdj` (ex-date adjustment follows the day trading *actually* occurred).
- Calendar math (期貨結算 3rd Wed, 選擇權週結算, 融券強制回補, 財報 deadlines) comes from the module
  18 rule engine — ingest consumes these markers to know *when to expect* late/monthly data.

## 6a. Startup preflight catch-up (`INGEST_ON_STARTUP`) — dev-only

> **Implementation status.** The gap-fill logic itself is real (`ingest/preflight.py`'s
> `run_preflight_if_enabled`/`last_available_trading_day`), and its production hard gate is real
> (`ingest_on_startup=False` short-circuits before any fetch or leader check). But **there is no
> app-startup entrypoint wired to call it yet** — `preflight.py`'s own docstring says so plainly:
> "There is no app entrypoint yet (that's a later phase)... it is not wired to anything today."
> `python -m backend.ingest.preflight` (below) is **not a real command** — there is no
> `ingest/__main__.py` guard that parses `--dev`/`--once`-style flags at all (`ingest/__main__.py`
> takes no CLI arguments; it starts the long-running leader-scheduled daemon loop, described in
> §3, unconditionally). The **real, working operator trigger today** is
> `python -m ingest.backfill_cli` (§6b, new below) — a separate one-shot CLI that already calls
> `run_preflight_if_enabled` for every job in `ingest.jobs.ALL_WHOLE_MARKET_JOBS`. Use that command
> for the dev catch-up this section describes; the `dev.sh` snippet below is design intent for a
> future direct-module invocation, not something you can run today.

In **dev** the server (and its in-process cron) isn't kept alive overnight, so the scheduled
ingest never fires and the store goes stale. A startup preflight would close the gap — it is just
the **gap-fill code path (§4) invoked manually** at boot instead of by cron:

```bash
# design intent — NOT a real command today (no app-startup entrypoint calls preflight.py yet);
# use `python -m ingest.backfill_cli --yes` (§6b) for the real equivalent
INGEST_ON_STARTUP=true python -m backend.ingest.preflight   # catch up to last trading day
uvicorn backend.src.api.app:app --reload
```

`preflight` logic (real, in `run_preflight_if_enabled`):
1. Sweep orphaned temp dirs from a crashed mid-write leader startup first (cheap, local, no
   network) before touching FinMind.
2. Compute `last_available_trading_day` from the trading-day calendar, **respecting the FinMind
   lag** (if today's EOD isn't published yet — before 17:30 — target yesterday).
3. Request the bounded window `[target - dev_backfill_years*250 trading days, target]` via
   `backfill_range(only_missing=True)` — the same gap-diff the daily cron path uses, so a
   non-empty store naturally backfills just its actual gap and an empty store naturally gets a
   first-run backfill, through one call. `dev_backfill_years` comes from `Settings` (config
   `DEV_BACKFILL_YEARS`), not hardcoded to 1994.

- **Gated behind a flag, dev-only.** **Production sets `INGEST_ON_STARTUP=false`** — workers must
  boot clean; a startup fetch on every worker boot would reintroduce the "no startup network
  call" hazard N× and race the leader. **Prod ingest is owned solely by the always-on
  leader-elected scheduler** (§3, infra §5). The preflight is a developer convenience, not a
  production path — real today at the function level, just not yet reachable from an app-boot
  hook.
- **Governance:** the startup fetch is still **platform ingest under §9c** (purpose-/calendar-
  bounded, cache-building, journaled) — just manually triggered by the developer rather than by
  cron. It introduces no new capability; it runs the exact §4 gap-fill.

## 6b. The real operator backfill CLI (`ingest/backfill_cli.py`)

> **Implementation status.** This is real and shipped — the actual way an operator runs a bounded
> gap-fill or dev backfill today, in place of the not-yet-wired direct-module invocation in §6a.

`python -m ingest.backfill_cli` is the one-shot, exit-code-bearing operator tool — deliberately a
separate module from `ingest/__main__.py` (the long-running leader-scheduled daemon), since a
one-shot interactive CLI and an always-on daemon are different process shapes that shouldn't share
argument-parsing or exit semantics.

- **Walks the canonical whole-market job list.** For each `WholeMarketJobSpec` in
  `ingest.jobs.ALL_WHOLE_MARKET_JOBS` (the same canonical list the daemon's `SCHEDULED_JOBS`
  wraps — currently **10 specs**: `price_eod`, `institutional_flow`, `financials`, `dividends`,
  `balance_sheet`, `cash_flow`, `month_revenue`, `market_value`, `active_etf_holdings`,
  `active_etf_holding_changes` — see §2 below for the full scheduled-job count including the
  derived-compute, monthly theme, and US-market jobs this list does not cover), it calls
  `run_preflight_if_enabled` once. One failing spec is logged (`ingest_backfill_cli_spec_errored`)
  and does not stop the remaining specs — the same per-item resumability philosophy
  `backfill_range` itself uses.
- **`--yes` override, not an env-var requirement.** Rather than exporting `INGEST_ON_STARTUP=true`
  into the whole process environment, `--yes` passes the same override into
  `run_preflight_if_enabled`'s existing `ingest_on_startup` parameter — per-invocation instead of
  via env. Without `--yes` and without `INGEST_ON_STARTUP` already set, the CLI logs
  `ingest_backfill_cli_refused` and exits cleanly having done nothing.
- **`--years` bounds the dev backfill window** (default from `Settings.dev_backfill_years`,
  validated into `[1, 10]` — `_MAX_BACKFILL_YEARS = 10` is a hard ceiling regardless of what an
  operator passes).
- **Unconditional production hard-stop.** `_check_not_production` raises
  `ProductionHardStopError` whenever `Settings.aitw_env == "production"`, checked **before**
  touching any spec and **regardless of `--yes`** — so a mis-set override can never turn into an
  accidental prod backfill. This is the load-bearing safety property of this CLI.
- **Exit code reflects failure.** Returns `1` if any `JobRun` in the batch has
  `status=RunStatus.FAILED`, else `0` — scriptable by an operator or a CI/ops check.

## 6c. Leader election — still a single-process stub, not real cross-process election

> **Implementation status, stated honestly.** `ingest/leader.py`'s own docstring says it plainly:
> "This is the ingest-local version; a future infrastructure phase will generalize this into a
> real cross-process election (Postgres advisory lock or a broker-based lease)." Today
> `get_default_leader_election()` returns `AlwaysLeader` — every process that asks `is_leader()`
> gets `True`, unconditionally. This is correct for local dev and a single-scheduler-worker
> deployment (the only topology that exists today), and every write-path job function already
> checks `is_leader()` before writing rather than assuming single-process forever — so swapping in
> a real election later is a one-line change at the construction site, not a redesign. It is
> **not** correct once ≥2 scheduler workers run concurrently; that topology has not shipped.
> `NeverLeader` also exists, purely as a test double for asserting a write path correctly no-ops
> under standby. Everywhere else in this doc that says "leader-elected" or "leader-gated" is
> accurate about the *behavior* (every write path really does check leadership first) but should
> be read with this caveat about what "leader" currently resolves to.

## 7. Store & concurrency — DuckDB/Parquet, and when to reconsider

The derived store is **DuckDB over Parquet + a Redis hot cache** (infra §3): right for this
workload — **single-writer (leader ingest), append-daily, columnar analytical reads** (the ~50
screener SQL screens, MA/KD windows, factor scans over years × whole-market).

| Option | Fit here | Verdict |
|--------|----------|---------|
| **DuckDB/Parquet** (chosen) | Embedded (no server), columnar/vectorized, reads Parquet directly, window functions native, store = files (cheap backup/DR/partition shipping) | ✅ keep |
| **ClickHouse** | Faster at scale; real server (ops + cost) | migration target for the *right* trigger (below), not read fan-out |
| **TimescaleDB** | Time-series Postgres, but row-store (slower on wide columnar factor scans) + couples market data to the app DB (kept separate by design) | not better here |
| **Parquet + Polars only** | great batch compute but loses SQL + the screener engine | weaker |
| plain Postgres | fine for small data; degrades on multi-year whole-market analytical scans | ❌ |

**The design constraint (DuckDB's documented model, `duckdb.org/docs/stable/connect/concurrency`).**
A single `.duckdb` file supports **either one read-write process *or* multiple read-only
processes — never both at once**; MVCC multi-reader concurrency is **in-process only**. Our
topology is a leader *writer* process **plus many API-worker *reader* processes** — separate
processes. So they **must not share one writable `.duckdb` file**: a reader opening it while the
leader holds the write lock **fails with a lock error** (not a stale read, not a queue) — and
that would fire during exactly the post-close ingest windows when users are most active.

> **Correction to an earlier framing.** It is **not** true that "Redis fronts reads, so DuckDB
> read-concurrency is a non-issue." Redis fronts *point reads* (a symbol snapshot, a health flag);
> the **analytical screener/scoring SQL that defines this store cannot be a Redis key** — those
> queries always execute against the columnar store, and on a shared writable file they'd lock-error.

**The pattern (what DuckDB's own docs recommend for this case):**
- **Writer = the leader ingest process only.** It writes **Hive-partitioned Parquet**
  (`market=…/date=…/…parquet`) via the atomic temp-dir→rename + manifest swap (§4).
- **Readers = every API worker, read-only, over Parquet directly** — a **per-worker in-memory
  DuckDB** running `read_parquet(glob, hive_partitioning=true)`. No shared write lock; any number
  of reader processes read the same Parquet tree concurrently while the leader writes elsewhere.
  (Reuse the per-worker connection — DuckDB is fastest reusing a connection — but keep it
  read-only over Parquet, never the writer's file.)
- Redis still caches hot **point** reads; the manifest pointer (§4) makes a partition swap a
  single atomic flip so a glob never straddles a write.

**Migration trigger for ClickHouse** is therefore **not** read fan-out (the read-only-Parquet
pattern scales horizontally across all workers). The honest triggers are: (1) a need for
**concurrent writers** / sub-daily continuous ingest, (2) write→read latency must be **seconds,
not minutes**, or (3) partition-rewrite volume makes the atomic-swap ETL too heavy. Absent those,
DuckDB/Parquet is correct; ClickHouse would be premature ops + cost.

## 8. Risks (design around these)
1. **Per-symbol trap** (§5) — biggest quota risk; enforce whole-market + alert.
2. **Free-tier misconfig** — whole-market silently 403s on Free; preflight + M5Q.
3. **Shared Sponsor-pool contention** — ingest/backfill/live-poller share the 6000/hr token →
   separate buckets + fair-share (infra §4); off-peak backfill.
4. **Thundering herd at 21:00** — everything updates then; stagger within 21:00–21:30 +
   single-flight per key.
5. **Scraper drift** — MOPS/PTT/ETF-PCF/rebalance are HTML; silent parse corruption ≠ staleness
   → versioned parsers + canaries + circuit breaker (infra §7); yfinance unofficial → degrade,
   never block a core flow.
6. **Calendar edge cases** — typhoon closure (NCDR overlay) + `PriceAdj` actual-vs-scheduled
   ex-date; missing these corrupts the adjusted series.

## Governance obligations
- Runs under the new **§9c platform-ingest carve-out** ([governance.md](../governance.md)) —
  purpose-/cadence-/calendar-bounded, non-trading, cache-building not per-user observation,
  separate quota bucket, leader-elected, fully auditable (every run journaled: dataset, date,
  rows, status). **Not authorized under §9a/§9b** — those are user-scoped.
- FinMind token stays env-only, decrypt-at-call, never crosses the scheduler boundary in
  plaintext (governance §6, infra §8). Ingest writes the shared derived store, not per-user
  state; currentness caveats are preserved through the cache tier and propagate to output.
