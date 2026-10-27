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
- **FinMind is the only date-range/backfill engine for most datasets** (for the four dated
  price/flow/margin/valuation datasets the exchanges' own date-parameterised reports are asked
  first, §1b). So ingest's primary writer is **FinMind
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

## 1b. Source order for the dated whole-market datasets — official exchange reports first

FinMind's free tier (600 requests/hour) 403s the whole-market pulls, so for the four date-keyed
datasets the exchanges publish themselves the runner asks the **free official reports first** and
FinMind only when they fail or return nothing. The order is data, in
`config/whole_market_source_priority.yaml` (a capability not listed there is FinMind alone):

| Capability (job) | 1st: `exchange_official` (`sources/exchange_whole_market/`) | 2nd |
|---|---|---|
| `price_eod` | TWSE `afterTrading/MI_INDEX` (`ALLBUT0999`, the `每日收盤行情` table) + TPEx `afterTrading/otc` (`EW`, prices) completed by TPEx `afterTrading/dailyQuotes` (`EW`, volume/turnover; below) | `finmind` |
| `institutional_flow` | TWSE `fund/T86` (`ALLBUT0999`) + TPEx `insti/dailyTrade` (`EW`) | `finmind` |
| `margin_short` | TWSE `marginTrading/MI_MARGN` (`ALL`: stocks + ETFs) + TPEx `margin/balance` | `finmind` |
| `valuation_ratios` | TWSE `afterTrading/BWIBBU_d` (`ALL`) + TPEx `afterTrading/peQryDate` | `finmind` |

Everything else (fundamentals, 分點, rosters, ...) stays FinMind-only. Per-symbol routing
(`source_priority.yaml`: `price_eod: [finmind, twse_openapi]`) is unchanged: the exchange reports
have no per-symbol form, and `twse_openapi` is latest-only, so it must not outrank FinMind for a
symbol's date range.

- **One runner.** `ingest/jobs/source_chain.py::walk_chain` is called by `run_whole_market_job`
  (cron, gap-fill and the backfill CLI all share it) and by the trend-score batch's margin read.
  The first source that is healthy **and** has rows wins; a healthy-but-empty answer or an error
  hands over to the next. Calendar gate, leader gate, row validation, the atomic partition write
  and the `data.updated` publish are unchanged. If no source serves and any failed, the day is
  journaled `FAILED` with every source's reason; if every source is healthy and empty it is a
  legitimate empty trading day (SUCCESS, 0 rows), as before.
- **Exchange-confirmed closures.** The internal calendar is learned from stored partitions (FinMind's
  trading calendar is not free), so it cannot know a closure such as Teachers' Day. The adapter
  tells an *explicit* "nothing published" (TWSE's no-data `stat`, a TPEx report without rows; the
  parsers raise `NoDataPublishedError`) from trouble (HTTP error, 429, block page, wrong date, layout
  change). When BOTH exchanges answered explicitly, it returns STALE with the detail constant
  `sources.base.NO_DATA_PUBLISHED`. If the date is strictly before today (Taipei) the chain stops
  there without calling FinMind, writes nothing and the job journals `SKIPPED`
  (`market closed: TWSE and TPEx published no data`). Any other case is unchanged: trouble in
  either exchange, one empty and one with rows, or today/a future date (maybe not published yet)
  falls back to FinMind and ends `FAILED` if nothing serves. The skip is sticky: it leaves a marker
  `<parquet_root>/_closures/<dataset>/<market>/<date>` (outside every `dataset=` tree, so no reader
  or partition scan sees it) and `detect_missing_trading_days` (`only_missing`, gap-fill, preflight,
  the backfill CLI) treats a marked day as done for THAT dataset only; a FAILED run leaves no
  marker, and a later successful write clears it. The marker is deliberately not fed into the
  learned `TradingDayCalendar` (frozen, built per process from partitions, shared by all jobs), so
  one dataset's closure never silently suppresses another's fetch; each of the four datasets
  confirms it once. Derived jobs (scores, screener flags, trend) still skip a date without a price
  partition (`has_rows`), and the backfill CLI exit status and `dev-backend.sh` count only `FAILED`.
- **Provenance** is the journal detail: `served by exchange_official: TWSE 1368 + TPEx 990 rows`,
  or `served by finmind: ok (after exchange_official: <why>)`. The Parquet layout is unchanged.
- **Same row shapes.** The parsers emit exactly FinMind's row dicts (names, units, signs, ISO
  `trade_date`, `market="TW"`), so readers and derived jobs are source-agnostic. Institutional flow
  stays the long shape: five rows per stock (`Foreign_Investor`, `Foreign_Dealer_Self`,
  `Investment_Trust`, `Dealer_self`, `Dealer_Hedging`). A missing PER/PBR/yield (`-`) is `0.0`, as
  FinMind; a stock that did not trade (prices `--`) has no price row.
- **Never half a market.** TWSE and TPEx are fetched together; one failing, or one empty while
  the other has rows, makes the whole answer UNREACHABLE (so FinMind is tried) instead of
  overwriting a date partition with one exchange only. A response dated differently from the
  request, an unexpected `stat`, or a changed column layout is an error, never an empty day.
- **Volume and turnover are the all-session totals.** FinMind's `Trading_Volume`/`Trading_money`
  are the exchange's full-day totals: regular-session round lots + 盤後定價 (fixed-price after-hours)
  + 盤中零股 (intraday odd lots) + 盤後零股 (after-close odd lots); block trades (鉅額) are NOT in
  them. TWSE's `MI_INDEX` already reports exactly that (1364/1364 symbols equal FinMind on
  2026-09-24; adding the separate `BFT41U`/`TWT53U` reports on top double counts). TPEx's
  `afterTrading/otc` ("不含定價") reports round lots only (OTC median -1.5% to -1.8%, worst -40% to
  -99.7% for illiquid names, 4% exact), so the TPEx price assembly also reads `afterTrading/dailyQuotes`
  (上櫃股票行情; the same day, warrants included, 1.5-2 MB) and takes volume and turnover from it:
  `otc` stays the universe and the OHLC source (warrants never enter), `dailyQuotes` only
  supplies the two figures per symbol. Measured on 2026-06-16, 07-15, 08-13 and 09-24 against stored
  FinMind rows: volume and turnover exact on 100% of the 991-998 OTC symbols each day (before: 4-5%
  exact); the decomposition on 09-24 is `dailyQuotes = otc + fixPricing(張x1000) + oddQuote + a
  non-negative remainder (after-close odd lots)`. FinMind's separate trade count (`Trading_turnover`)
  is not part of the stored row, so `成交筆數` is not read. One extra request per day to the same
  throttled client (2 s gap).
- **Never partial volume.** If the `dailyQuotes` supplement cannot complete EVERY `otc` symbol (HTTP
  error, a Cloudflare challenge or any non-JSON page after the client's bounded retries, a changed
  layout, a wrong date, a missing symbol, a total below its own round-lot figure) the TPEx outcome is
  *incomplete*: the exchange answer is UNREACHABLE with detail
  `TWSE n + TPEx m rows, volume=round-lot (TPEx: <why>)` (`sources.base.ROUND_LOT_VOLUME`) but still
  carries its rows. The chain therefore asks FinMind next (complete volume) and uses it if it serves.
  Only when no later source serves (FinMind failed or empty) does `walk_chain` serve the round-lot rows,
  and the journal detail says so:
  `served by exchange_official: TWSE n + TPEx m rows, volume=round-lot (TPEx: ...) (no complete source: finmind: ...)`.
  The partition then holds round-lot TPEx volume for that date until a later run overwrites it
  (the tag in the journal is how to find such dates). A failing `otc` itself is unchanged (UNREACHABLE,
  no rows). No schema change: the tag lives in the existing journal detail.
- **Politeness.** TWSE bans an IP above about 3 requests / 5 s: each host has a minimum gap
  between request starts (TWSE 2.5 s, TPEx 2.0 s; `asyncio.Lock` + monotonic clock, never
  blocking the loop), a hard 20 s timeout, at most 3 tries with a doubling backoff on 429 / 5xx /
  timeout / non-JSON (the block page), a descriptive `User-Agent`, TLS strict. A dataset-date
  costs one request per exchange (two for TPEx price: `otc` + `dailyQuotes`).
- **Known differences from FinMind** (accepted, measured on 2026-06-16 and 2026-09-24 against
  stored FinMind rows: open/high/low/close, volume/turnover, every margin column, institutional buy/sell and
  PER/PBR/yield match 100% on the symbols both have): warrants and CBBC are not ingested (about 21k
  rows/day; every reader already filters them, `analytics/symbol_universe.py`); 興櫃 (emerging)
  stocks (~350 price rows, ~140 institutional symbols) are not in the exchanges' bulk reports;
  margin `note` is whitespace-trimmed.

## 1c. Period-keyed datasets — official filings first (`filings_official`)

The datasets companies report for a period (not a trading day: monthly revenue, financial
statements) are asked of
`sources/company_filings/` (source id `filings_official`, `OFFICIAL_FILINGS`) before FinMind, through
the same chain (`config/whole_market_source_priority.yaml`). They are keyed by a reporting period, so
the adapter also decides which run dates it owns:

- **Where a period is stored.** The partition of a period is the **first trading day on or after its
  nominal date** (`ingest/jobs/period_dates.py`, `WholeMarketJobSpec.period_anchor`): monthly revenue
  of month M is filed under the 1st of M+1 (`trade_date`), a statement under its quarter end
  (`period_end`). When that date is a trading day (two months in three) the partition is that date,
  as it always was; when it is not (a weekend or holiday 1st, a Sunday quarter end), the partition is
  the next trading day. The runner still asks every source for the nominal date, and the ROWS keep
  FinMind's own fields (`trade_date` = the 1st), so FinMind-era partitions, row models and the
  FinMind fallback are unchanged; readers walk trading-day partitions (or list the stored dates), so
  they find the shifted partition without a change. Before this, a non-trading nominal date was
  skipped by the calendar gate and its period was never ingested: 15 of the 38 months from 2023-09 to
  2026-10 (2023-10, 2024-01, 2024-06, 2024-09, 2024-12, 2025-01, 2025-02, 2025-03, 2025-06, 2025-11,
  2026-01, 2026-02, 2026-03, 2026-05, 2026-08 by the calendar rules; the dev volume was not
  readable when this was written) and the quarters ending 2023-09-30, 2023-12-31, 2024-03-31 and
  2024-06-30. The gap-fill needs no change: the partition is an ordinary trading day with no rows, so
  `detect_missing_trading_days` lists it and a run fills it (`only_missing` backfill, the daily
  catch-up).
- **A date that holds no rows by definition** is answered `HealthStatus.OK` with no rows and costs no
  request (monthly revenue: every date that is not a month's partition; statements: every date that
  is not a quarter's partition; the adapter sees such a date as itself, never as a 1st or a quarter
  end, so its own rule gives the same answer). The
  runner reads an `OK` answer without rows as "nothing here, by definition" and stops:
  the empty partition is written (SUCCESS, 0 rows) and FinMind is not asked. (`STALE` stays "not
  published yet": the next source is asked.) Before this, every such date was a FinMind request and,
  on the free tier, a failed run: the ~4,500 failed runs of the 2026-10-26 backfill.
- **No churn**: an empty answer for a date that already holds an empty placeholder partition writes
  no new version and sends no `data.updated` signal (nothing changed), so the backfill sweep over the
  many dates of a period dataset does not rewrite thousands of empty partitions on every run.
- **A period that is not complete yet** (before the statutory filing deadline) is `STALE` without a
  request: a partition with rows is never re-fetched, so serving a half-filed period would freeze it
  half-filed. The next source is asked and the date stays a gap for the next gap-fill.
- **Never half a market**, as §1b: every required page must answer.
- **Provenance**: the journal detail names the source and the page counts, e.g.
  `served by filings_official: MOPS t21sc03 2026-06: listed 1089 + OTC 895 + emerging 339 rows`.

| Capability (job) | Source | History | Partition / row key (identical to FinMind) |
|---|---|---|---|
| `financials`, `balance_sheet`, `cash_flow_statement` | MOPS summary reports `mopsov.twse.com.tw/mops/web/ajax_{t163sb04,t163sb05,t163sb20}` (form POST: `TYPEK` sii/otc/rotc, ROC `year`, `season`; UTF-8; thousands of TWD; every industry template on the page; 3 markets x 1-4 pages, 3 s apart) | any quarter back to the IFRS years (checked 2025Q1-2026Q2) | the quarter end (`period_end` = 03-31, 06-30, 09-30, 12-31, FinMind's partition when it is a trading day, else the next trading day's); served after the last statutory deadline (Q1 5/30, Q2 8/31, Q3 11/29, annual 3/31 of the next year) |
| `month_revenue` | MOPS static report pages `mopsov.twse.com.tw/nas/t21/{sii,otc,rotc}/t21sc03_<ROC year>_<month>.html` (Big5, thousands of TWD; listed + OTC + emerging, 3 requests, 3 s apart) | any month, verified back to 2006-01 (two page layouts, both parsed) | the revenue of month M is filed under the 1st of M+1 (`trade_date` = that day), stored in the partition of the first trading day on or after it; any other day holds none. Served from the 11th of that month (due by the 10th) |

`month_revenue` mapping: `revenue` = page figure x 1000 (TWD, as FinMind), `revenue_month`/
`revenue_year` of the revenue month, `country` "Taiwan", `market` "TW". `create_time` (the day FinMind
first saw the filing) has no official counterpart and is `None` (the model allows it; FinMind leaves
it empty for older rows). A figure restated after filing shows its restated value, where FinMind kept
the first filing.

Measured against the FinMind-era partitions (2026-07-01 = June 2026, 2026-05-01 = April 2026): the
symbols both have match on `trade_date`/`country`/`revenue_month`/`revenue_year` 100% and on
`revenue` 99.65% / 99.44% (the rest are amended filings); the official side lacks 12-15 symbols
FinMind carries (not on the three pages) and adds 5-6.

Statement mapping. The reports carry the AGGREGATE lines (about 15 income, 15 balance-sheet and 7
cash-flow lines per company), not the full XBRL statement FinMind stores (about 60 balance-sheet and
30 cash-flow detail lines). Every mapped line carries FinMind's own `item` (`Revenue`, `GrossProfit`,
`IncomeAfterTaxes`, `TotalAssets`, `Liabilities`, `Equity`, `CashFlowsFromOperatingActivities`, ...)
and `origin_name` (the operating cash flow under both its FinMind items), in TWD (reports in
thousands; EPS unscaled), `statement_type` `income_statement` / `balance_sheet` / `cash_flow`,
`market` "TW"; every industry template is mapped (general, bank, financial holding, securities,
insurance, other), and a line FinMind has no `item` for is left out. Value conventions, as FinMind
stores them:
- income statement: the SINGLE quarter, although the report is year-to-date. Q1 is the report; Q2-Q4
  subtract the company's nearest earlier filing of the year (walking back through the earlier
  seasons only while some company still has none), so a half-yearly filer (emerging market) gets
  H1 as its Q2 and the second half as its Q4, as FinMind does; a company with no earlier filing
  keeps the year-to-date figure.
- balance sheet: the position at the period end; cash flow: year-to-date (Q4 = the full year).
- a quarter whose earlier-season report cannot be fetched fails whole (never written year-to-date).

Lossy fields (tagged, not guessed):
- **EPS** is not additive (the share count moves), so a derived Q2-Q4 EPS is an approximation: its
  `origin_name` reads `基本每股盈餘（累計差額推算）` instead of `基本每股盈餘` (Q1 is exact). Against
  FinMind: exact for about 68% of companies, within 0.01 for another 22%.
- **Detail lines are absent**: cash, borrowings, capex, depreciation, receivables, inventories (the
  readers that need them already degrade for a company FinMind lacks; operating cash flow,
  `TotalAssets`, `Liabilities`, `Equity`, `Revenue`, `OperatingIncome`, `IncomeAfterTaxes` and
  `GrossProfit` are served). The common-size `*_per` duplicates and the share-count line are not
  produced.
- **Restated figures**: the report shows the latest filing, FinMind the first, so about 1-2% of lines
  differ by a restatement.
- A year-to-date report cannot be checked for its year (the page does not echo it): the market and
  season are checked from the page heading, the year is trusted from the request.

Measured against the FinMind-era partitions (same company and line, 1-TWD tolerance, EPS 0.005):
income Q1 2026 99.91% of 30,161 pairs, Q4 2025 98.92% of 34,871 (money lines 99.8%), Q2 2026 98.2%,
Q3 2025 97.7% (the shortfall is EPS); balance sheet Q1 2026 99.84% of 24,736, Q4 2025 98.98%, Q2 2026
99.92%; cash flow Q1 2026 99.96% of 14,024, Q4 2025 99.89%, Q2 2026 99.98%. The official side
covers 1,970-2,342 companies per quarter (FinMind 2,025-2,643): it adds 22-579 that the FinMind-era
snapshots had not captured yet (the stored 2026-06-30 partitions hold only the early filers), and
lacks 30-300 (public non-trading companies and some emerging ones).

Known cadence limit: the 1st's partition is filled by the gap-fill/backfill after the 11th, not by
the daily 21:30 run on the 1st itself (which finds the month not complete yet).

**Filling a partial partition (`--fill-missing-companies`).** A partition that already holds rows is
never re-fetched, so one written early in a filing season (2026-06-30 before every company filed)
stays partial, and re-running the job would REPLACE it: the official reports carry only the
aggregate lines (about 15 income / 15 balance sheet / 7 cash flow per company) where FinMind-era
rows carry the full statement (about 60 / 30 detail lines). The operator-run additive refresh
(`ingest/refresh.py`) fetches the official source and ADDS the rows of every company absent from the
stored partition. It never replaces, edits or deletes a stored row, so FinMind-era detail survives.

- A company is its `symbol` within the dataset. Each statement is its own dataset (income, balance
  sheet, cash flow), so "absent" is per company per statement type; `statement_type` is not part of
  the key because FinMind and the official mapping may spell it differently, which would make every
  stored company look absent and duplicate it. A stored company is never topped up line by line.
- Leader-gated (the CLI takes the writer lease; a follower journals SKIPPED), journaled
  (`added N companies (M rows), B -> A; served by filings_official: ...`, `rows_written` = rows
  added), published once as `data.updated`, and written with the same atomic partition swap. If the
  official source fails or has nothing, the partition is left as it was (FAILED / SKIPPED). It is
  idempotent: a second run adds 0, writes nothing and signals nothing. A date that is not the
  partition of a period of that dataset is SKIPPED without a request.
- Run it with the daemon stopped (the CLI exits 3 while it holds the lease), one dataset and one or
  more partition dates (the stored partition dates: for the example, 2026-06-30 holds 2026Q2 and
  2026-02-02 holds the revenue of January 2026). It prints one line per date with the company counts
  before and after:

```
docker compose run --rm --no-deps --entrypoint python ingest -m ingest.backfill_cli --yes   --fill-missing-companies TaiwanStockFinancialStatements --dates 2026-06-30
# TaiwanStockFinancialStatements 2026-06-30: companies <before> -> <after> (+<n>) [success] ...
```

  `DATASET` is one of `TaiwanStockMonthRevenue`, `TaiwanStockFinancialStatements`,
  `TaiwanStockBalanceSheet`, `TaiwanStockCashFlowsStatement`; repeat the command per dataset. The
  same flags work with `python -m ingest.backfill_cli` outside Docker. They replace the backfill
  (nothing else runs), still require `--yes` and still hard-stop in production.

Not covered by an official first source, and why: **dividends** (`TaiwanStockDividend`): FinMind's
`announced_date` is the announcement date of the ex-dividend notice and its partition date is the
ex-date plus four trading days; the official dividend feeds (`t187ap45_L`, the 除權除息 tables) carry
the board date or only the ex-date, so neither field nor the partition could be reproduced without
guessing. **Market value / weight**: Sponsor-only, no official equivalent. The TWSE/TPEx OpenAPI
statement endpoints (`t187ap06/07_*`) were not used: they hold the latest quarter only, in the same
aggregate form, and have no cash-flow report; the MOPS reports above carry the same lines with
history.

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
- **`Cadence.INTRADAY`** fires once per `every_minutes` slot from `fire_time` through `until` on a
  trading day; the only job using it is `strategy_intraday`.
- Whole-market FinMind pulls omit `data_id` (so they need a **>=Backer** token, §5).
  "Land" = DuckDB/Parquet.

| Job (`SCHEDULED_JOBS` name) | Fires (Taipei) | Cadence | What it lands |
|---|---|---|---|
| `stock_info` | 06:00 | daily | FinMind `TaiwanStockInfo` roster (industry classification) |
| `strategy_watchlist` | 08:00 | daily | derived: 策略盯盤 pre-market watchlist with trigger levels (module 20) |
| `strategy_intraday` | 09:00–13:30, every 10 min | intraday | 策略盯盤 check: one whole-market `taiwan_stock_tick_snapshot` (FinMind Sponsor) per slot vs the watchlist → `strategy_hits` (module 20) |
| `shareholding_distribution` | 06:05 | weekly | 集保股權分散/分級 `TaiwanStockHoldingSharesPer` |
| `price_eod` | 17:40 | daily | EOD OHLCV `TaiwanStockPrice` |
| `margin_short` | 17:40 | daily | 融資融券 `TaiwanStockMarginPurchaseShortSale` |
| `strategy_close_check` | 18:00 | daily | derived: 策略盯盤 close confirmation of breakout setups → `strategy_hits` slot `close` (module 20) |
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
| `sc_refdata_load` | 06:20 | daily | rebuild the supply-chain index tables from the operator's private relation files; local file read only, leader-only, no upstream call (module 20 §5b) |
| `sc_themes_entities_load` | 06:25 | daily | rebuild the concept-theme tables (mirrored into `themes`/`theme_memberships`) and the entity catalog + mention tables from the operator's private `themes/concepts/*.yaml`, `entities/catalog.yaml` and `entities/mentions.jsonl`; local file read only, leader-only, no upstream call; a bad file fails only its own load (module 20 §5b) |
| `sc_positions_refresh` | 06:30 | daily | recompute every company's upstream/midstream/downstream position per industry chain from the tier map, the verified relations and the private overrides, and rewrite `sc_positions`; local compute only, leader-only, skipped (journaled) without memberships (module 20 §5b) |
| `sc_theme_candidates` | 22:05 | weekly | concept-theme candidate mining (Step A): deterministic, from the private entity catalog/mentions and concept-theme files, writes the private `themes/theme_candidates.jsonl`; no search, no LLM, no network, so **not** gated by `SUPPLY_CHAIN_RESEARCH_ENABLED`; leader-only, journals SKIPPED with a reason when the entity files are absent (module 20 §5b) |
| `sc_research` | 22:15 | monthly | supply-chain DISCOVER -> VERIFY: budgeted web search + LLM checks, deterministic guards, evidence-backed rows written to the private relation files, then `sc_refdata_load`; leader-only; **journals SKIPPED unless `SUPPLY_CHAIN_RESEARCH_ENABLED=true`** (off by default and in production), the search key and an LLM key are present; capped per run by `SUPPLY_CHAIN_RESEARCH_*` (module 20 §5b) |
| `sc_theme_research` | 22:30 | monthly | concept-theme web research then verification (Steps B and C): budgeted search + LLM claim extraction, deterministic evidence rules, writes **claims and proposal files only** under `themes/proposals/` and **never promotes** (a person reviews them and runs `promote`); leader-only; **journals SKIPPED unless `SUPPLY_CHAIN_RESEARCH_ENABLED=true`**, the search key and an LLM key are present; capped per step by `SUPPLY_CHAIN_RESEARCH_*` (module 20 §5b) |
| `us_symbol_universe` | 05:30 | monthly | US symbol roster (§2c) |
| `us_price_eod` | 06:00 | daily (US session) | US EOD prices (§2c) |
| `us_fundamentals` | 07:00 | weekly | US financials/dividends/splits (§2c) |
| `us_scoring_batch`, `us_trend_score_batch` | 07:30 / 07:32 | daily (US session) | the scoring/trend batches run with `market=US` |

`python -m analytics.supply_chain promote` (reviewed proposal files into concept-theme files, module 20 §5b "Promoting proposals") is an operator command and is **never scheduled**; the 06:25 `sc_themes_entities_load` only indexes what it wrote.

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

`screener_flags`, `strategy_watchlist`, `scoring_batch`, `trend_score_batch` and the two theme seeds are the jobs in
`SCHEDULED_JOBS` that compute rather than pull. They are calendar- and leader-gated exactly like
the pulls: a non-trading day is a journaled skip, a non-leader pass writes nothing, and a run that
finds no upstream history writes a journaled empty result rather than an error, so stale or partial
data is never scored. Dependencies:

- `screener_flags` <- `price_eod` + `institutional_flow` (writes only true `(symbol, tag)` rows to the
  `screener_flags` Parquet dataset).
- `strategy_watchlist` <- the last session's `price_eod`, `institutional_flow`, `margin_short`,
  `stock_info`, plus `attention_stocks`/`disposition_events`/`dividends`/`material_announcements`
  for exclusions and context; writes one row per `(symbol, strategy)` dated the run morning. Skips
  (journaled) instead of publishing when the last price session is more than 3 trading days old.
- `scoring_batch` <- `price_eod`, `institutional_flow`, `financials`/`balance_sheet`/`cash_flow`,
  `market_value` (and the ETF datasets for ETF symbols); upserts the relational `stock_scores`
  cache for both horizon profiles in one pass.
- Layer 3 concept-theme research (module 20) is **deliberately
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

**A backfill publishes once per dataset, at the end.** `backfill_cli` wraps the publisher in
`CoalescingDataUpdatePublisher`: it keeps the newest `as_of` per `(capability, market, scope)` and
publishes those when the run ends, instead of one signal per partition written (about 11,000 for a
three-year backfill, each delivered to every open tab and to the alert engine). The daemon publishes
as each partition lands (a few dozen a day).

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

### 2d. US macro statistics (`ingest/jobs/fred.py`)

`fred_macro`, daily 07:40 Taipei, `market=US`, leader-gated; four datasets (`fred_observations`,
`fred_series_meta`, `fred_release_dates`, `fred_vintages`). Skips with `FRED API key not configured`
when no key is set. Full spec: [27-us-macro.md](27-us-macro.md).

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
Before both chains it also runs `sc_refdata_load`, `sc_themes_entities_load` and then `sc_positions_refresh` once (local index rebuild and derived compute; failure is logged and ignored).

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
- **Optional supply-chain research step.** After the derived catch-up, one `sc_research` pass runs when `SUPPLY_CHAIN_RESEARCH_ENABLED=true` (the job re-checks the leader, search key and LLM gates and journals SKIPPED otherwise). Its operator-run counterpart is `python -m analytics.supply_chain discover|verify [--dry-run]` (module 20 §5b).
- **`--fill-missing-companies DATASET --dates YYYY-MM-DD ...`** replaces the backfill with the
  additive refresh of a partial period-keyed partition (§1c): it adds the companies the official
  source has and the stored partition lacks, and prints the company count before and after.
- **Exit code** is `1` if any `JobRun` has `status=RunStatus.FAILED`, else `0`.

**FinMind quota.** Ingest shares the FinMind token with the API, and every call it makes is
counted against the token's cap with the API's ([16](16-infrastructure.md) §4). It may use at most
`FINMIND_INGEST_SHARE` (half) of a window; past that, the call waits for the next window
(`finmind_ingest_waiting_for_quota` in the log, with the seconds), so a long backfill slows down
rather than taking users' share. After a 402 it waits for the reset and retries once; refused again,
the run stops with `FinMindQuotaRefusedError` (the CLI exits 1, the daemon ends that pass) rather
than waiting an hour per date on a lapsed plan or token.

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
  `scripts/dev-backend.sh --backfill` (the daily dev backfill) does exactly that: it stops the
  `ingest` service, runs the CLI once per market in a one-off container
  (`docker compose run --rm --no-deps --entrypoint python ingest -m ingest.backfill_cli --yes
  --market tw`), then starts the stack; the daemon's startup gap-fill continues from there. Users
  keep being served meanwhile: the API reads each partition only after its atomic swap.
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

The scheduler daemon takes the same kind of lease under its own role (`scheduler`), see
[14](14-scheduler-alerts.md) §6.

## 6d. Memory: what the nightly batches hold

A raw `TaiwanStockPrice` partition is about 22,000 rows a day, **90% of them warrants** (the
scoreable rule is in `analytics/symbol_universe.py`); a 260-trading-day window is about 5.7M rows.
Measured on the dev data (770 trading days, 1.2 GB of Parquet), peak resident memory of one nightly
batch, run alone against a copy of the volume:

| Batch | Before | After |
|---|---|---|
| `screener_flags` | about 6.3 GB (900K rows cost 1,005 MB as dicts; the 260-day read was all of them) | 1.76 GB |
| `scoring_batch` (TW) | **more than 4 GB: OOM-killed (exit 137)** | 1.12 GB |
| `trend_score_batch` (TW) | 1.47 GB for the price frame alone, plus the rest | 1.27 GB |
| `scoring_batch` (US) | 3.49 GB | 1.76 GB (completes under a 2 GB cap) |

What changed, and the rules that keep it that way:

- **Filter inside DuckDB, not after the read.** `ParquetStore.read_range_frame(..., where=...)`
  takes a predicate, and `scoreable_symbol_sql()` builds the scoreable rule as SQL (checked against
  `is_scoreable_symbol` by a test), so the warrants never become a DataFrame. The price window drops
  from 5.67M to 676K rows (1,471 MB to 384 MB). The trend batch keeps the `TAIEX` index row it needs
  from the same frame. The flow window is filtered the same way. Do not add a whole-market
  `read_range(...)` of a multi-day window: it boxes every cell as a Python dict (about 1.1 KB a row).
- **Statements are parsed one symbol at a time.** The 400-day Taiwan statement window is 1.48M
  rows; building a `FinancialStatementLine` for each, up front, took the process to 3.06 GB (1.4 GB
  of row dicts, then 1.6 GB of objects). `LazyStatementLines` keeps the rows as a columnar frame with
  repeated text as categories and builds a symbol's lines when that symbol is scored: the same lines
  in the same order (checked on the dev data for all 2,665 symbols), 946 MB for the frame.
- **Screener flags are written for scoreable symbols only.** The warrants' 51K flags (60-day
  sample) were read by nothing: the screener engine gates its results to the scoreable universe, and
  only the legacy `run_tag_screener` tool listed them as hits.
- **The API's screener reads the same way.** Its two whole-market numeric reads (technical
  indicators over 260 days; ROE and latest EPS over about 450 days of statements) used to load row
  dicts into the API process on every request: **953 MB and 2,118 MB**, so one request with both an
  ROE and an RSI condition passed the API container's 2 GiB limit and got the API killed and
  restarted (observed on the dev stack). Now each is a filtered columnar frame (scoreable symbols
  only; only the net-income, equity and EPS statement items) of **347 MB and 214 MB**, computed
  **once per data day and shared** (`_bulk_by_day`: the first request computes, concurrent ones wait
  for it, a new partition invalidates it). Results are identical (15,864 technical and 5,125
  fundamentals values compared on the dev data). Twelve simultaneous cold requests against the API
  container: all 200, 467 MiB peak, no restart.
  **Kept warm after an ingest** (`api/screener_warm.py`, Redis backend only): an API worker that
  has already served a screener read recomputes it in the background when a TW `data.updated`
  signal arrives, after a 120 s settle (an ingest lands several partitions) plus a random 0-180 s
  spread (every worker hears the same signal), so the first request of the new data day finds it
  ready. A read nobody asked for is not warmed, so an idle worker holds nothing extra; a failed
  warm-up only means that first request pays for the read, as before.
- **US statements read the same way.** The US statement datasets are a full weekly re-pull (14.4M
  raw rows in the window, 4M distinct). `read_range_latest_by_key_frame` de-duplicates inside DuckDB
  and returns a frame, and `LazyStatementLines` builds a symbol's lines when it is scored. Two
  things it exposed: (1) the old row-dict query returned its rows in a **different order on every
  run** (two runs of the identical query over the same files hashed differently), and a symbol has
  both a quarterly and an annual line for the same item and period at a fiscal year end (about
  1.1M such combinations), while the fundamentals code takes the first match and the TTM sum is
  meant to add quarterly figures; the frame read now orders them (`US_STATEMENT_ORDER`: quarterly,
  then ttm, then annual), and on the dev data this changes no symbol's metrics (0 of 1,126).
  (2) About 800K older US rows have a NULL `period_type`; the model rejects them (its default
  applies only to an absent key), so the eager parse **silently dropped** them and so does the lazy
  path (the symbol list is identical: 1,126 symbols). **Decision: they stay excluded**, as an
  explicit rule, and are not relabelled "quarterly". Measured on the dev data: the NULL rows are
  the six weekly snapshots taken before the column existed (2026-08-15 to 09-19); every later one
  is typed. 79% of their distinct lines are exact duplicates (same symbol, period, item and value)
  of typed rows. The other 21% (about 213K lines) are older periods that fell out of the source's
  window, whose frequency (annual or quarterly) is unknowable from the row, and an annual figure
  taken as quarterly inflates a trailing-twelve-month sum about fourfold, a worse error than
  missing old history. Of the 331 symbols with statements but none typed, **none is priced in the
  latest US session** (they have left the universe), so no live stock loses scoring. Revisit only
  if the source starts returning untyped statements again.
- **`job_us_fundamentals` spools to disk and resumes** (`ingest/spool.py`). It used to hold every
  symbol's rows for its 9 capabilities in memory until its 1.9-hour loop ended (about 1-1.5 GB) and
  lose them all if the process was killed. Now every 25 symbols (about two minutes of paced calls)
  the rows go to small Parquet files under `<store root>/.spool/us_fundamentals__<date>/`, and a
  fsynced `progress.jsonl` line records which symbols are done. A run started again for the same
  date skips those symbols and carries their failure count, so a kill costs at most one batch.
  Chunk files are vouched for only by a progress line: a chunk written but not recorded is deleted
  on resume and its symbols fetched again, never counted twice. The partitions are built by DuckDB
  from the files (`ParquetStore.write_partition_from_files`, the same atomic swap), so the rows
  never return to Python. The spool is deleted after a successful write; spools older than 3 days
  are swept at the start of the next run.
- **`job_us_fundamentals` refetches only what is stale** (`ingest/us_fundamentals_plan.py`). The
  universe is 2,123 symbols (about half OTC Pink) and a call takes about 2.2 s, so refetching all
  nine capabilities for everyone was about twelve hours a week for data that mostly had not
  changed. Each capability now has a freshness window (statements 25 days listed / 45 OTC; dividends,
  splits, consensus, holders, insiders 7 / 30; sector 90 / 90) and a symbol's capability is fetched
  only when the stored copy is older. The date of each successful fetch is kept in the
  `us_fundamentals_state` dataset, so a fetch that legitimately returned nothing is not retried
  every week while a failed one (no record) is retried at the next run. A partition is written from
  the new rows plus the previous partition's rows for every symbol not refreshed
  (`write_partition_from_files(carry_from=...)`), so a skipped symbol keeps its data, and a refetch
  that returns nothing never wipes what was stored. Listed symbols are fetched before OTC ones. With
  no state yet (the first run after this shipped) the latest partitions stand in: a symbol with rows
  there counts as fetched on that partition's date. OTC gets the longer window and is not skipped:
  in the latest US scores about 8% of OTC rows use a fundamentals-based factor (42% of listed rows).
- **Container limits** (`docker-compose.yml`): ingest 5 GB (the startup catch-up runs the TW and US
  chains together, about 3.5-3.8 GB), api 2 GB, mcp and scheduler 1 GB, no swap
  (`INGEST_MEM_LIMIT`, `API_MEM_LIMIT`, ...). An out-of-memory kill now restarts one service
  (`restart: unless-stopped`) instead of taking the host down.

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
`WholeMarketJobSpec` and the dataset table; `period_dates` the partition rule of the period-keyed ones (§1c), `ingest/refresh.py` their additive refresh; `source_chain` the ordered source list (§1b);
`whole_market` the shared runner and the one-line whole-market job functions; `shareholding`, `etf`, `disclosures` (MOPS) and `risk_lists` (attention
stocks, disposition events) the jobs with their own fetch logic. Modules import only downward
(`specs` <- `whole_market` <- the rest). A test that stubs a fetcher patches it on the module that
calls it (for example `ingest.jobs.risk_lists`), not on the package.

