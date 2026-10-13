# Module: Data ingest / ETL (`backend/src/ingest/`)

The layer that **populates our own derived market-data store** (DuckDB/Parquet + Redis,
[modules/16](16-infrastructure.md) §3) on a schedule, so the platform owns a time series the
government OpenAPIs don't keep. Everything downstream — the context pipeline (module 02/04),
analytics/scoring/screener (module 20), the calendar (module 18) — reads this store instead of
hitting sources live. Ingest is the **only writer** of the derived store.

## 0. Three distinct scheduled activities — do not conflate

The doc set has **three** kinds of scheduled network activity. They must not share a scheduler
lane or a quota bucket:

| | What it is | Authorization | Trigger | Quota model |
|-|-----------|-----------|---------|-------------|
| **(A) Platform ingest** (this module) | Bulk-building the shared derived store; unattended, market-wide | platform-ingest carve-out | trading-calendar cron | whole-market **by-date** (≈1 call/dataset/day) |
| **(B) Live-quote poller** ([16](16-infrastructure.md) §2a) | 5s `price_intraday` snapshots, only for symbols with a live subscriber | the live-quote poller's bounded observation | user opens `/quote/stream` | per-subscribed-symbol, refcounted |
| **(C) User jobs / alerts** ([14](14-scheduler-alerts.md)) | A Pro user's own scheduled alert/watchlist refresh | the user-authorized scheduler carve-out | user creates a rule | per-user sub-quota |

Ingest (A) writes the EOD store and never touches the live snapshot path; the live poller (B)
reads snapshots and never writes the EOD store; user jobs (C) read the store per-user. **Three
separate quota buckets** so a backfill can never starve live Pro users ([16](16-infrastructure.md) fair-share).

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
  history engine), every cron anchors on FinMind's `Update:` time (+~10 min) — not on an assumed exchange clock. **Lag chain:** official publish → FinMind (T+hours) →
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

## 2. Cadence — what is scheduled today

The scheduler is `ingest/daemon.py`'s `IngestDaemon`: a **leader-gated, wall-clock poll loop**. One
pass (`run_due_jobs`) checks the fixed `SCHEDULED_JOBS` tuple of `ScheduledIngestJob(name,
fire_time, run_fn, cadence, market)` entries against the current Taipei-local time and fires
whatever has newly become due, marking it handled *before* running it (a failed run is never
retried in a tight loop; the next window's pass or an explicit backfill retries). `python -m ingest`
(`ingest/__main__.py`) wraps this in a sleep/wake loop with graceful SIGTERM/SIGINT handling.

- **Fire times are code constants** anchored on each upstream's published update time (FinMind's
  documented `Update:` time, see `finmind/llm.md`) plus a ~10 min buffer — not on an assumed
  exchange clock. Lag chain: official publish -> FinMind (hours) -> our cron; e.g. price is on
  TWSE by ~14:30 but on FinMind at 17:30, so our EOD lands ~17:40, acceptable for a non-trading
  EOD product.
- **`Cadence.DAILY`** jobs fire once per local trading day and are gated on the job's own market
  calendar (TW or US); **`WEEKLY`/`MONTHLY`** jobs are reference-data cadences, not trading-day
  gated, and fire when never run or last run >= 7 / 28 days ago. US daily jobs fire for the last
  *completed US session*, not the Taipei date.
- Whole-market FinMind pulls omit `data_id` (so they need a **>=Backer** token, §5).
  "Land" = DuckDB/Parquet.

| Job (`SCHEDULED_JOBS` name) | Fires (Taipei) | Cadence | What it lands |
|---|---|---|---|
| `stock_info` | 06:00 | daily | FinMind `TaiwanStockInfo` roster (industry classification) |
| `shareholding_distribution` | 06:05 | weekly | 集保股權分散/分級 `TaiwanStockHoldingSharesPer` |
| `price_eod` | 17:40 | daily | EOD OHLCV `TaiwanStockPrice` |
| `margin_short` | 17:40 | daily | 融資融券 `TaiwanStockMarginPurchaseShortSale` |
| `valuation_ratios` | 18:10 | daily | PER/PBR/殖利率 `TaiwanStockPER` |
| `institutional_flow` | 20:10 | daily | 個股三大法人 `...InstitutionalInvestorsBuySell` |
| `screener_flags` | 20:30 | daily | derived: tag screener (module 20) |
| `active_etf_holdings`, `active_etf_holding_changes` | 21:15 | daily | 主動ETF 成分/異動 (FinMind Sponsor) |
| `etf_nav` | 21:20 | daily | ETF 折溢價 vs NAV (TWSE MIS `all_etf.txt`) |
| `etf_expense_ratio` | 21:25 | monthly | ETF/基金內扣費用 (SITCA IN2105) |
| `etf_aum` | 21:27 | weekly | ETF 資產規模 (yfinance `totalAssets`, paced per-symbol) |
| `financials`, `balance_sheet`, `cash_flow`, `dividends`, `month_revenue` | 21:30 | daily | statement / dividend / 月營收 families |
| `material_announcements`, `insider_transfer_declaration` | 21:35 | daily | MOPS 重大訊息, 內部人股權轉讓事前申報 |
| `attention_stocks`, `disposition_events` | 21:35 | daily | TWSE/TPEX 注意股, 處置股 |
| `market_value`, `market_value_weight` | 23:40 | daily | 市值, 市值比重 |
| `scoring_batch` | 23:55 | daily | derived: 多空 5-factor `stock_scores` (module 20) |
| `trend_score_batch` | 23:57 | daily | derived: 趨勢評分 `trend_scores` (module 20) |
| `theme_industry_seed`, `theme_tier_seed` | 18:30 / 18:35 | monthly | theme membership + tier seeds (module 20) |
| `us_symbol_universe` | 05:30 | monthly | US symbol roster (§2c) |
| `us_price_eod` | 06:00 | daily (US session) | US EOD prices (§2c) |
| `us_fundamentals` | 07:00 | weekly | US financials/dividends/splits (§2c) |
| `us_scoring_batch`, `us_trend_score_batch` | 07:30 / 07:32 | daily (US session) | the scoring/trend batches run with `market=US` |

Chain order is enforced by fire time: the derived batches run after the pulls they read.
The whole-market FinMind pulls are the specs in `ingest.jobs.ALL_WHOLE_MARKET_JOBS`
(`ingest/jobs/specs.py`; the backfill CLI walks it, §6b).

**Not scheduled yet** (`STUBBED_DATASETS` in `ingest/jobs/specs.py` is the checklist): 市場總法人/借券,
報酬指數, 漲跌停, `TaiwanOptionVix`, 期貨/選擇權 daily + 法人 (including 盤後 法人, which only finalizes
the next morning), 外資持股/信用總量/大盤維持率, 當沖 values, ETF PCF scrape, 分點/八大官股/產業鏈資金流
(`chip_broker` has no whole-market request form, so it stays per-symbol), 景氣信號, the
next-morning **reconcile pass** (§4), and the event-driven pollers (news, PTT, index rebalance,
macro calendar, IPO, yfinance consensus).

### Derived-compute jobs (analytics — module 20; consume the store, don't hit FinMind)

`screener_flags`, `scoring_batch`, `trend_score_batch` and the two theme seeds are the jobs in
`SCHEDULED_JOBS` that compute rather than pull. They are calendar- and leader-gated exactly like
the pulls: a non-trading day is a journaled skip, a non-leader pass writes nothing, and a run that
finds no upstream history writes a journaled empty result rather than an error, so stale or partial
data is never scored. Dependencies:

- `screener_flags` <- `price_eod` + `institutional_flow` (writes only true `(symbol, tag)` rows to the
  `screener_flags` Parquet dataset).
- `scoring_batch` <- `price_eod`, `institutional_flow`, `financials`/`balance_sheet`/`cash_flow`,
  `market_value` (and the ETF datasets for ETF symbols); upserts the relational `stock_scores`
  cache for both horizon profiles in one pass.
- Layer 3 concept-theme research + in-theme supply-chain edges (module 20) is **deliberately
  unscheduled** — an operator runs `python -m analytics.themes` by hand, since it is the only
  analytics job that spends on LLM calls and does live web research.

**On completion -> cache-invalidation signal.** Every job in `ingest/jobs/` and `ingest/us_jobs.py`
that feeds a cached dataset calls `ctx.data_update_publisher.publish_data_updated({capability,
market, as_of, scope})` on landing — only the leader publishes. The daemon binds the real Redis
publisher via `ingest/publisher_binding.py` (the `INGEST_DATA_UPDATE_PUBLISHER_FACTORY` setting
names a factory in `events/`); on the in-memory bus, with the setting emptied, or on any wiring
failure it keeps the no-op, so the partition still lands and clients fall back to
boundary-anchored revalidation. API workers relay the signal on `GET /updates/stream`
(`api/routers/context.py`; the broker is built by `events/live_quote_factory.py`'s
`build_data_update_broker`, in-memory shared object or Redis pub/sub per `event_bus_backend`), so
clients revalidate exactly the affected keys the moment a partition lands. The derived batches
(`screener_flags`, `scoring_batch`, `trend_score_batch`) do **not** publish; they write their
result and journal the run.

### 2c. US-market ingest jobs (`ingest/us_jobs.py`, `ingest/us_universe.py`)

The US-market counterparts to the TW whole-market jobs, scheduled in `SCHEDULED_JOBS` (§2)
alongside them (plus `us_scoring_batch` / `us_trend_score_batch`, the scoring batches run with
`market=US`):

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

`job_us_price_eod` and `job_us_fundamentals` publish `data.updated` on a successful leader write — they call `ctx.data_update_publisher.publish_data_updated({capability, market:
US, as_of, scope})` per dataset written, the same signal the TW whole-market jobs publish (see
"On completion" above); `job_us_symbol_universe` does not, since nothing downstream revalidates
against the roster itself the way it does against price/fundamentals partitions.

### Backfill (on-demand ops job — resumable)
Backfill is `ingest/backfill.py`'s `backfill_range(only_missing=True)`: diff the expected trading
days against the dates already present for a dataset and run the **same per-date whole-market code
path as the daily cron** for each gap — there is deliberately no second write path. It is driven by
the operator CLI (§6b) or the dev startup catch-up (§6a); keep it throttled and off-peak, in its
own quota bucket.

**Backfill depth is per-dataset, not a global start** (from `finmind/llm.md` data-ranges):
price/PriceAdj **1994-10**, 損益表 **1990-03**, 月營收 2002-02, 融資融券 2001, 三大法人(Wide) 2005,
期貨 daily 1998-07 · **shallow / Sponsor-only:** 分點 **2021-06**, 八大官股 2021-06, 主動ETF **2025-05**,
集保股權分級 2010-01, 盤後法人 2021-10, 市值比重 2024-10. **No historical backfill** for tick /
minute-K (`storage_objects` exist only from FinMind's feature-launch date) — set each dataset's
backfill window from its documented range; don't assume a uniform history.

## 3. Scheduling model

There is no cron-bundle concept in code: `IngestDaemon` has one flat `SCHEDULED_JOBS` tuple (§2)
and `run_due_jobs()` simply awaits each due job in-process within one pass. It runs on the
**leader-gated scheduler worker** (see [§6c](#6c-leader-election--still-a-single-process-stub-not-real-cross-process-election)
for what "leader" means today). There is **no separate task broker/queue and no thread/process
pool for scrapers**; the event-driven pollers listed under "Not scheduled yet" (§2) would need
one if they are built.

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
  so a swap is a single pointer flip. Writers take a per-partition lock for the swap, old versions
  are deleted only after a grace period, and the leader sweeps **old orphaned temp dirs** left by a
  crashed mid-`COPY` write ([§6c](#6c-single-writer-lease-leader-election)). (Reader/writer isolation: [§7](#7-store--concurrency--duckdbparquet-and-when-to-reconsider), [16](16-infrastructure.md) §3/§10.)
- **Detect missed days** — diff expected trading days (`TaiwanStockTradingDate`) vs distinct
  dates present per dataset; any gap (crash/outage/FinMind lag) → enqueue a **targeted date-range
  backfill** (same code path as first-run, scoped to the gap). The journal makes gaps visible.
- **Late-arriving data** — stagger crons to each dataset's update time (融資融券 21:00, 官股/市值
  23:30, 當沖 values ~21:30); a **next-morning (~08:30) reconcile pass** that re-pulls yesterday and swaps the
  partition would catch values that landed after the primary run, including the datasets that are
  only final the next morning (期/選 盤後 三大法人 `...InstitutionalInvestorsAfterHours`, the weekly
  集保股權分散). **Not built yet** (§2); until then, a missed or partial day is repaired by the
  gap-fill/backfill path.
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
- **Data-quirk validators** — `TaiwanStockPrice` publishes `0` on no-trade days →
  filter `close > 0`, **not** volume; prefer `PriceAdj` for a gap-free adjusted series; 興櫃
  `open` = prev-day average (can fall outside [min,max]); 分點 emerging market-maker rows show
  price=0 legitimately.

## 5. Quota budgeting — whole-market-by-date is non-negotiable

- **Whole-market-by-date** (`TaiwanStockPrice` etc. **without `data_id`, by `start_date`**) =
  **≈1 call/dataset/day**. The whole-market FinMind jobs wired today
  (`ingest.jobs.ALL_WHOLE_MARKET_JOBS`, §2/§6b) are **about one call each per day** (a dozen or so);
  wiring the remaining datasets in §2 would add a handful more. Either figure is trivially inside the platform's own operator-held FinMind token's
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

## 6a. Startup catch-up (`INGEST_ON_STARTUP`) — dev-only

In **dev** the stack isn't kept up overnight, so scheduled jobs miss their fire time and
`IngestDaemon.seed_startup()` deliberately does not replay an elapsed window ("production boots
clean"). When `Settings.ingest_on_startup` is true, `python -m ingest` (`ingest/__main__.py`'s
`_startup_gap_fill`) runs a best-effort, never-fatal catch-up **once before the poll loop**: the TW
chain (the gap-fill of `ingest/preflight.py`'s `run_preflight_if_enabled` for each whole-market
spec, then the derived `scoring_batch`/`screener_flags`, then the MOPS and 處置王-board jobs, whose
upstreams take no date and so cannot be recovered later) and the US chain, concurrently.

`run_preflight_if_enabled` itself:
1. Sweeps orphaned temp dirs from a crashed mid-write (cheap, local, no network).
2. Computes `last_available_trading_day` from the trading-day calendar, **respecting the FinMind
   lag** (before ~17:30 it targets yesterday).
3. Requests the bounded window `[target - dev_backfill_years*250 trading days, target]` via
   `backfill_range(only_missing=True)` — the same gap-diff the daily cron path uses, so a non-empty
   store backfills just its gap and an empty store gets a first-run backfill through one call.
   `dev_backfill_years` is `Settings.dev_backfill_years` (env `DEV_BACKFILL_YEARS`, default 3).

**Production sets `INGEST_ON_STARTUP=false`** — workers must boot clean; a startup fetch on every
boot would reintroduce the "no startup network call" hazard N× and race the leader. Prod ingest is
owned solely by the always-on leader-gated scheduler (§3). This is platform ingest under the
platform-ingest carve-out, just manually triggered rather than by cron; it introduces no new
capability.

## 6b. The operator backfill CLI (`ingest/backfill_cli.py`)

`python -m ingest.backfill_cli` is the one-shot, exit-code-bearing operator tool — deliberately a
separate module from `ingest/__main__.py` (the long-running daemon), since the two are different
process shapes.

- **`--market tw|us`** (default `tw`). The TW path calls `run_preflight_if_enabled` once per spec in
  `ingest.jobs.DAILY_WHOLE_MARKET_JOBS` (`ALL_WHOLE_MARKET_JOBS` minus the weekly shareholding
  spec, which is walked once per week instead), then walks the SITCA monthly fee backfill, runs the
  forward-only jobs that have no date parameter (`etf_nav`, `etf_aum`, the MOPS and 處置王-board
  jobs) for today only, and finally catches up the derived `scoring_batch`/`screener_flags`. The US
  path refreshes the symbol universe if stale, then backfills prices and runs the fundamentals job.
  One failing spec is logged and does not stop the rest.
- **`--yes` override, not an env-var requirement.** `--yes` passes the same override into
  `run_preflight_if_enabled`'s `ingest_on_startup` parameter, per invocation. Without `--yes` and
  without `INGEST_ON_STARTUP` set, the CLI logs `ingest_backfill_cli_refused` and does nothing.
- **`--years`** bounds the window (default `Settings.dev_backfill_years`, validated into `[1, 10]`).
- **Unconditional production hard-stop.** `_check_not_production` raises `ProductionHardStopError`
  whenever `Settings.env == "production"`, checked before touching any spec and **regardless of
  `--yes`**, so a mis-set override can never become an accidental prod backfill.
- **Exit code** is `1` if any `JobRun` has `status=RunStatus.FAILED`, else `0`.

## 6c. Single-writer lease (leader election)

Exactly one process may write the derived store, however many ingest replicas, deploy overlaps or
hand-run CLIs are alive. `ingest/leader.py` provides the lease; every job already asks
`ctx.leader.is_leader()` before it writes.

| Implementation | Used when | Mechanism |
|---|---|---|
| `PostgresAdvisoryLeader` | the app database is Postgres (dev stack, production) | a session-level `pg_try_advisory_lock` on a **dedicated** autocommit connection outside the pool; Postgres drops it the instant that connection dies, so a crashed leader frees the role with no timeout |
| `FileLockLeader` | SQLite / no Postgres on the machine | an OS file lock (`<parquet_root>/.ingest-writer.lease`), released by the OS when the process exits |
| `AlwaysLeader` / `NeverLeader` | tests only | doubles; production wiring never returns them |

`build_leader_election(role, database_url, scope_root)` chooses one; the lock key is derived from
the role and the data root, so two data roots never share a lease.

- **Daemon** (`python -m ingest`): takes the lease at startup, then calls `refresh_leadership` at the
  start of every pass. A process that did not get it runs as a **standby**: every pass is a logged
  no-op, and it takes over within one poll interval after the leader goes away.
- **Manual backfill** (`python -m ingest.backfill_cli`): takes the same lease. If the daemon (or a
  second backfill) holds it, the CLI **exits with status 3** and says so; it never writes
  alongside the daemon. Run it when the daemon is stopped, or stop the daemon for the run.
- **Partition writes are serialised on top of that.** `ParquetStore.write_partition` does its
  expensive `COPY` into a private temp dir, then takes a per-partition file lock (`.write.lock`) for
  only the rename, the manifest flip and the cleanup, so even two writers that both believe they
  lead (a bug, a split brain) cannot interleave.
- **Cleanup never races a reader.** A superseded version is kept for `VERSION_GRACE_SECONDS`
  (600 s) before it is deleted, and the version the manifest points at is never deleted. The leader
  runs `sweep_stale_versions()` once at startup for versions no later write came back to clean.
  `.tmp` leftovers of a crashed write are removed only when older than an hour, so the recovery
  sweep cannot kill a write that is still running in another process.
- **A torn partition heals itself.** `has_partition` is true only if the manifest's version
  directory exists, so gap-fill and backfill rewrite a partition whose manifest points at nothing.

The scheduler still uses `AlwaysLeader` today; it moves to the same lease next
([14](14-scheduler-alerts.md), [16](16-infrastructure.md) §5).

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
- Runs under the **platform-ingest carve-out** (rules in [`backend/CLAUDE.md`](../../../backend/CLAUDE.md)
  §4 governance-as-code and [`AGENTS.md`](../../../AGENTS.md)): purpose-/cadence-/calendar-bounded,
  non-trading, cache-building rather than per-user observation, separate quota bucket,
  leader-gated, and fully auditable (every run journaled: dataset, date, rows, status). It is
  **not** covered by the user-scoped scheduler or live-quote carve-outs.
- The FinMind token stays env-only, decrypted at call, and never crosses the scheduler boundary in
  plaintext (`backend/CLAUDE.md` §8, [16](16-infrastructure.md)). Ingest writes the shared derived
  store, not per-user state; currentness caveats are preserved through the cache tier and
  propagate to output.

## Where the job code lives

`backend/src/ingest/jobs/` is a package; `ingest.jobs` re-exports every public name, so
`from ingest.jobs import job_price_eod, ALL_WHOLE_MARKET_JOBS` is unchanged. `specs` holds
`WholeMarketJobSpec` and the dataset table; `whole_market` the shared runner and the one-line
whole-market job functions; `shareholding`, `etf`, `disclosures` (MOPS) and `risk_lists` (attention
stocks, disposition events) the jobs with their own fetch logic. Modules import only downward
(`specs` <- `whole_market` <- the rest). A test that stubs a fetcher patches it on the module that
calls it (for example `ingest.jobs.risk_lists`), not on the package.

