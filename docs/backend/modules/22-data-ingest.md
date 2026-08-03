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

These are **pure-compute / LLM jobs, not ingest jobs** — they read the freshly-written columnar
store, so they run **in dependency order *after* `ingest-daily-late` lands** (chained, not on an
independent clock). They draw the **compute/LLM budget, not the FinMind quota**.

| Job | Cadence | Depends on | Cost | Notes |
|-----|---------|-----------|------|-------|
| **`compute-scores`** (多空 5-factor, module 20 §2) | **daily** post-close (~23:50, after 市值/官股 land) | daily-late ingest | pure compute (no LLM) | winsorize → percentile → blend both profiles; write `stock_scores` |
| **`compute-topN`** (AI 篩選, module 20 §5C) | **daily**, **right after** `compute-scores` | `compute-scores` | pure compute | gates → setup → rank survivors; cache the ranked lists |
| **`compute-ai-summary`** (智能摘要 + SWOT, module 20 §3) | **weekly, ALL ~1,900 stocks, weekend (Sat/Sun)** → upsert `ai_summaries`; **on-demand only when the DB row is empty** (new IPOs) | `compute-scores` (Fri) | **LLM (cheap-tier)** | full coverage affordable at cheap-tier (~$/week); if row has data, serve as-is — no refresh (module 20 §3) |
| **`discover-themes`** + figure events (module 20 §5C) | scheduled LLM pass, **~7-day window** | ingested news/price signals | **LLM** | proposes descriptive themes + confidence; theme = a tag, not a scored factor |

**Dependency chain (daily):** `ingest-daily-late` → `compute-scores` → `compute-topN`. The
weekly `compute-ai-summary` and `discover-themes` hang off the same store on their own cadence.
A score/topN job that finds the day's ingest incomplete (gap-detect, §4) **skips and reports**
rather than scoring stale/partial data — no half-day rankings.

**On completion → cache-invalidation signal.** When a derived-compute job commits, the leader
**publishes a `data.updated` event** `{capability, market, as_of, scope}` over the §2 event
transport ([16](16-infrastructure.md) §3a) — `compute-scores`→`{score,…}`, `compute-topN`→`{topN,…}`,
`compute-ai-summary`→`{summary,…}`, `discover-themes`→`{themes,…}`. API workers relay it on
`/updates/stream` so clients **revalidate exactly the affected keys** the moment fresh analytics
exist, instead of waiting out a time boundary ([web data-freshness.md](../../web/data-freshness.md) §4).
Same trigger updates the read-model `as_of`/`ETag` so a boundary-anchored conditional GET also sees it.

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

1. **`ingest-daily-postclose`** (15:10–20:10 staggered per §2 cron column) — 市場總法人/借券
   (15:10), 報酬指數 (17:00), EOD prices (**17:40**), PER/漲跌停 (18:10), 處置/注意 + VIX (18:00–18:10),
   期/選 daily + regular 法人 (20:10), **個股三大法人 (20:10 — its own 20:00 slot, not the early edge)**,
   ETF NAV/PCF. Stagger to each dataset's FinMind update time; don't fire at the group's early edge.
2. **`ingest-daily-late`** (21:05 + 23:40 sub-job) — 融資融券, 外資持股, 信用總量, 大盤維持率 (21:05),
   當沖 values (21:40), Sponsor chip 分點/主動ETF (21:15), 官股/市值/產業鏈 (23:40).
2b. **`ingest-reconcile`** (next morning ~08:30) — re-pull *yesterday* and upsert via the atomic
   swap (§4): catches late/revised 融資融券/官股/市值, **and the next-morning-only** 期/選 盤後(夜盤)
   三大法人 (`...InstitutionalInvestorsAfterHours`) + the weekly 集保股權分散 (Mon).
3. **`ingest-weekly`** (**Sun 06:00** + Mon reconcile) — 集保股權分級 (`TaiwanStockHoldingSharesPer`;
   TDCC lands Sun/Mon, **not** Sat — see §2), ETF 受益人數.
4. **`ingest-monthly`** — 月營收 (daily days 1–12), 財報 (deadline windows), 景氣信號, 股利.
5. **`ingest-event`** — MOPS/news/PTT coarse pollers (queue, not cron), IPO daily, rebalance
   weekly, macro yearly, yfinance weekly.
5b. **`compute-*`** (derived analytics, module 20 — consume the store, not FinMind) — chained
   after daily-late: **`compute-scores` (daily)** → **`compute-topN` (daily)**;
   **`compute-ai-summary` (weekly + on-demand)** and **`discover-themes` (~7-day)** on their own
   cadence. Draw the compute/LLM budget, not the FinMind quota.
6. **`ingest-backfill`** — one-time/on-demand, checkpointed & resumable.

Runs on the **leader-elected scheduler workers** (infra §5); tasks dispatched to the broker/
queue; scrapers in the thread/process pool (infra §6). Reuse the §5 at-least-once + idempotent
machinery, dedupe key `(job_id, trigger_date)`.

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
  **≈1 call/dataset/day**. The full daily bundle is **~25–35 calls/day** — trivially inside even
  Free 600/hr, and vast headroom on Sponsor 6000/hr. **This is the design.**
- **Per-symbol is the trap** — ~2,000+ symbols × datasets = thousands of calls/dataset/day; one
  dataset alone blows Free's 600/hr in ~18 min. **Never ingest per-symbol for market-wide EOD.**
  Enforce whole-market at the router/adapter level; alert if a market-wide job issues >(datasets) calls.
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

In **dev** the server (and its in-process cron) isn't kept alive overnight, so the scheduled
ingest never fires and the store goes stale. A **startup preflight** closes the gap — it is just
the **gap-fill code path (§4) invoked manually** at boot instead of by cron:

```bash
# dev.sh
INGEST_ON_STARTUP=true python -m backend.ingest.preflight   # catch up to last trading day
uvicorn backend.src.api.app:app --reload
```

`preflight` logic:
1. Read `max(date)` present per **core** dataset in the Parquet store.
2. Compute `last_available_trading_day` from `TaiwanStockTradingDate`, **respecting the FinMind
   lag** (if today's EOD isn't published yet — before ~17:30 — target yesterday).
3. `store_max < target` → enqueue a **targeted date-range backfill** for the gap (same code as
   gap-fill; whole-market-by-date, cheap — a few dozen calls even for a multi-day gap).
4. **Empty store** → first-run backfill **bounded to a dev window** (e.g. last 1–2 years, config
   `DEV_BACKFILL_YEARS`), not to 1994.

- **Gated behind a flag, dev-only.** **Production sets `INGEST_ON_STARTUP=false`** — workers must
  boot clean; a startup fetch on every worker boot would reintroduce the §1.3 "no startup network
  call" hazard N× and race the leader. **Prod ingest is owned solely by the always-on
  leader-elected scheduler** (§3, infra §5). The preflight is a developer convenience, not a
  production path.
- **Governance:** the startup fetch is still **platform ingest under §9c** (purpose-/calendar-
  bounded, cache-building, journaled) — just manually triggered by the developer rather than by
  cron. It introduces no new capability; it runs the exact §4 gap-fill.

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
