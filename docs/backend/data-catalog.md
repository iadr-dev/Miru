# Data Catalog & Source Review

> Verified against the live OpenAPI specs (2026-07) and `docs/finmind/llm.md`. This is
> the authoritative inventory the source-adapter layer (`backend/src/sources/`) is built
> from. Read [governance.md](governance.md) for how timing/tier rules apply to each.

## 0. TL;DR — what we can build

- **~70% of "professional" TW stock data** (法人/融資券/借券/當沖/財報/月營收/期權OI/PER)
  is available **free** from government OpenAPIs *or* FinMind's free tier.
- **The historically hard gaps** — 分點券商進出 (broker-branch flows), 集保股權分散/大戶,
  八大官股, 產業鏈資金流, tick & minute-K, real-time snapshots — are all covered by
  **FinMind Sponsor** datasets, so we no longer need bespoke scrapers.
- **Analyst 目標價/評等 + earnings consensus** — the former "paid gap" — is now filled for
  **large-cap `.TW`/`.TWO`** by **yfinance** (Yahoo-aggregated consensus, §2.12), surfaced as
  attributed third-party estimate. Mid/small-cap coverage stays sparse.
- **Genuine remaining paid/absent gaps:** curated supply-chain relationship graphs (partial via
  FinMind `TaiwanStockIndustryChain`), true real-time order-book depth beyond snapshots, and
  mid/small-cap analyst coverage.
- **Derived/computed layer** (technical indicators, financial ratios, PE/PB bands,
  法人連續買賣超, 籌碼集中度) is *our* value-add, computed from the raw feeds above — see the
  **analytics/scoring** module ([modules/20](modules/20-analytics-scoring.md)): a multi-factor
  多空 score, AI summary/SWOT, and an AI top-N screener.
- **Ingest note:** because the gov OpenAPIs keep no history, these feeds are polled daily
  **whole-market-by-date** and persisted to our own DuckDB/Parquet store by the **data-ingest /
  ETL layer** ([modules/22](modules/22-data-ingest.md), governance §9c) — the analytics and
  context layers read that store, not the sources live.

## 1. Source families

| Family | Base | Auth | History | Role |
|--------|------|------|---------|------|
| **TWSE OpenAPI** | `openapi.twse.com.tw/v1` | none | latest snapshot only | Official listed-market EOD/reference |
| **TPEX OpenAPI** (225 endpoints) | `tpex.org.tw/openapi/v1` | none (UA header req'd) | latest snapshot only | Official OTC/emerging EOD/reference |
| **TAIFEX OpenAPI** (135 endpoints) | `openapi.taifex.com.tw/v1` | none | latest snapshot only | Official derivatives EOD/reference |
| **TWSE_MIS** | mis endpoints | none | live-ish snapshot | Bounded intraday observation |
| **TAIFEX_MIS** | mis endpoints | none | live-ish snapshot | Bounded derivatives observation |
| **NCDR / DGPA** | gov closure feeds | none | event-driven | Closure/currentness evidence |
| **FinMind** (104 datasets wired, 77 whole-market-eligible) | `api.finmindtrade.com/api/v4` | Bearer token | **date-range**, deep backfill | Aggregated, history-enabled superset |
| **MOPS** (scraped) | `mopsov.twse.com.tw/mops/web` | none | event-driven | Disclosure/catalyst stream — see §2.7 |
| **News / GDELT / Finnhub** | RSS + `api.gdeltproject.org` + Finnhub | mixed (Finnhub key) | near-real-time | News freshness — see §2.8 |
| **PTT** (scraped) | `ptt.cc` | none | near-real-time | Social buzz (Stock/Tech_Job/Beauty/Gossiping) — see §2.8 |
| **ETF PCF** (scraped) | issuer / 投信投顧公會 / TWSE `fund/` | none | official-eod | Passive ETF 成分股/受益人數/NAV — see §2.10–2.11 |
| **Brave web search** | `api.search.brave.com` | `BRAVE_API_KEY` | live | Provider-agnostic web search + fetch; **per-turn toggle**, cite sources; LLM-native search when available. `public`. See [adr/0014](adr/0014-web-search.md) |

**Key architectural distinction:**
- Government OpenAPIs are **latest-only, no parameters** — each endpoint dumps the whole
  current dataset. Historical time series require daily polling + storage (which
  governance forbids as auto-polling) **or** FinMind.
- **FinMind is the history/backfill engine**: same underlying gov data, pre-collected and
  queryable by `start_date`/`end_date` + `data_id`, plus Sponsor-only feeds (分點, tick,
  minute-K, real-time) that gov OpenAPIs don't publish cleanly.

## 2. FinMind dataset catalog (grouped) — all tiers

Tier legend: **F** = Free · **B** = Backer · **S** = Sponsor. `data_id` = symbol/contract.
Full field lists live in [finmind/llm.md](finmind/llm.md); summarized here.

### 2.1 Technical / price (21)
| Dataset | Tier | Notes |
|---------|------|-------|
| `TaiwanStockInfo`, `...WithWarrant`, `TaiwanStockTradingDate` | F | universe & calendar |
| `TaiwanStockInfoWithWarrantSummary` | S | 權證標的對照表 (warrant → underlying mapping) |
| `TaiwanStockPrice` / `TaiwanStockPriceAdj` | F (per-stock) / B·S (whole-market) | daily OHLCV; Adj = 還原股價 |
| `TaiwanStockPER` | F | PER / PBR / 殖利率 |
| `TaiwanStockPriceLimit` | F/B·S | 漲跌停價 |
| `TaiwanStockDayTrading` | F/B·S | 當沖標的與量值 |
| `TaiwanStockTotalReturnIndex` | F | 加權/櫃買報酬指數 |
| `TaiwanStockWeekPrice` / `MonthPrice` / `Stock10Year` | B·S | week/month/10Y K |
| `TaiwanStockKBar` | **S** | **minute-K** |
| `TaiwanStockPriceTick` | B·S | **tick**, whole-day parquet via `storage_objects` |
| `TaiwanStockStatisticsOfOrderBookAndTrade` | F | 每5秒委託成交 |
| `TaiwanVariousIndicators5Seconds`, `TaiwanStockEvery5SecondsIndex` | F / B·S | intraday index |
| `TaiwanStockSuspended`, `...DayTradingSuspension` | B·S | halts |

### 2.2 Chip / institutional (24) — the 籌碼 core
| Dataset | Tier | Notes |
|---------|------|-------|
| `TaiwanStockInstitutionalInvestorsBuySell` / `...Wide` | F/B·S | 個股三大法人 (wide = per-investor columns) |
| `TaiwanStockTotalInstitutionalInvestors` | F | market-level 3-法人 |
| `TaiwanStockMarginPurchaseShortSale` / `Total...` | F/B·S | 融資融券 |
| `TaiwanDailyShortSaleBalances` | F/B·S | 信用額度總量管制 |
| `TaiwanStockShareholding` | F/B·S | 外資持股 |
| `TaiwanStockHoldingSharesPer` | **B·S** | **集保股權分級 (大戶/散戶)** |
| `TaiwanStockSecuritiesLending` | F/B·S | 借券成交明細 |
| `TaiwanStockTradingDailyReport` | **S** | **分點券商進出** (the top missing signal) |
| `TaiwanStockTradingDailyReportSecIdAgg` | **S** | 當日券商分點統計 |
| `TaiwanStockWarrantTradingDailyReport` | **S** | 權證分點 |
| `TaiwanstockGovernmentBankBuySell` | **S** | **八大官股** |
| `TaiwanTotalExchangeMarginMaintenance` | B·S | 大盤融資維持率 |
| `TaiwanStockBlockTrade` / `...BlockTradingDailyReport` | S | 鉅額交易 |
| `TaiwanStockLoanCollateralBalance` | S | 借貸擔保品餘額 |
| `TaiwanStockActiveETFInfo` / `...Holding` / `...HoldingChange` | F / S / S | 主動式ETF 持股明細與異動 |
| `TaiwanStockIndustryChainMoneyFlow` | **S** | **產業鏈資金流** |
| `TaiwanStockMarginShortSaleSuspension`, `...DispositionSecuritiesPeriod` | B·S | 處置/暫停融券 |
| `TaiwanSecuritiesTraderInfo` | F | 證券商資訊表 (broker id/name/address — 分點 reference) |

### 2.3 Fundamental (12)
`TaiwanStockFinancialStatements` (綜合損益表), `TaiwanStockBalanceSheet`, `TaiwanStockCashFlowsStatement`
(all F per-stock / B·S whole-market, 三市場 coverage), `TaiwanStockMonthRevenue` (月營收),
`TaiwanStockDividend` / `TaiwanStockDividendResult` (股利/除權息結果), `TaiwanStockMarketValue`
& `...MarketValueWeight` (B·S, 市值/比重), plus corporate-action refs
(`CapitalReductionReferencePrice`, `SplitPrice`, `ParValueChange`, `Delisting`).

### 2.4 Derivatives (18)
`TaiwanFutOptDailyInfo` (期貨/選擇權契約總覽, F), `TaiwanFuturesDaily` / `TaiwanOptionDaily`
(F/B·S), `TaiwanFutures/OptionInstitutionalInvestors` (+ `AfterHours` B·S),
`TaiwanFutures/OptionOpenInterestLargeTraders` (B·S, 大額交易人), `TaiwanFutures/OptionTick`
(B·S), `TaiwanFuturesSpreadTrading` / `TaiwanFuturesSpreadTick`, `Taiwan*FinalSettlementPrice`,
`TaiwanOptionVix`, dealer volume dailies (`TaiwanFuturesDealerTradingVolumeDaily`,
`TaiwanOptionDealerTradingVolumeDaily`), asset-swap dailies (`TaiwanAssetSwapFixedIncomeDaily`,
`TaiwanAssetSwapOptionDaily`).

### 2.5 Real-time (4) — **Sponsor → `sponsor` data_class (Premium/Pro, masked otherwise)**
`taiwan_stock_tick_snapshot` (individual + 91 index codes), `taiwan_futures_snapshot`,
`taiwan_options_snapshot`, `TaiwanFutOptTickInfo`.

**Price tiering (the intraday quote model):**
- **Free → EOD only** (`price_eod`, `public`): official TWSE/TPEX/TAIFEX + FinMind history.
- **Premium/Pro → live-ish quote** (`price_intraday`, `sponsor`): the snapshot datasets above,
  pushed via a **shared demand-driven server poller + SSE fan-out** — one 5s poll per *subscribed*
  symbol serves all watching clients (governance §9b carve-out; [modules/16](modules/16-infrastructure.md)
  §2a). Only symbols with a live subscriber are polled, market-hours only, auto-stops on last
  disconnect. Far lower Sponsor-quota use than per-client polling.
- **Honesty (governance §1.2):** this is **delayed snapshot polling (~5–20s vs the matching
  engine), not true tick streaming** — the currentness caveat rides on every payload even for
  Pro. "Real-time" is a tier *label*; the data stays caveated. True sub-second streaming = a
  licensed broker feed (Fugle/Shioaji/XQ) — out of scope.

### 2.6 Disposition / attention stocks (處置股 / 注意股) — the "處置王" surface
The dashboards in scope (熱門處置股: 風險股 / 處置中 / 即將出關 / 自結發布, with disposition
period, 資/券/沖 eligibility, and "最快 N 日後進入處置 / 再次處置" projections) are assembled from:
- **FinMind `TaiwanStockDispositionSecuritiesPeriod` (公布處置有價證券表, B·S)** — per-stock
  disposition records: `disposition_cnt` (第N次處置), `condition` (處置條件), `measure`
  (處置措施, e.g. 人工管制撮合/預收款券), `period_start`, `period_end`. → `data_class: public`
  (Backer-available; no Sponsor plan required).
- **Gov OpenAPI (authoritative, same-day, free):** TPEX `tpex_disposal_information`,
  `tpex_trading_warning_information` / `tpex_trading_warning_note`, `tpex_cmode`
  (變更/管理/停券); TWSE attention/disposal + `t187ap05`-family notices. → `public`.
- **Derived (our compute):** "注意股" trigger flags, 連續注意天數, and the **"最快 N 日後進入
  處置"** projection (rule-based from TWSE/TPEX 注意→處置 escalation criteria + recent
  attention hits). Self-settled (自結) EPS surfacing from MOPS announcements. → `public`.
- Chip badges shown on each card (三大法人/融資融券/借券/當沖/主力) reuse §2.2 datasets — 分點
  (主力) remains `sponsor`; the rest are `public`.

### 2.7 MOPS catalyst layer (公開資訊觀測站) — the event-driven disclosure stream
The gap our structured EOD/price/institutional data **cannot** contain: the free-text,
event-driven filing stream that *moves* price. **No official JSON API** — sourced by scraping
MOPS forms (`mopsov.twse.com.tw/mops/web/<report_id>`, ROC dates, Big5/UTF-8), with TWSE/TPEX
announcement pages as an easier real-time mirror for 重大訊息. All `public`. Priority order:

| # | Dataset (report id) | Contents | Cadence | Signal |
|---|---------------------|----------|---------|--------|
| 1 | **重大訊息** (`t05st01` / real-time `t05sr01_1`) | Free-text disclosures: contract wins, capacity, M&A, litigation, guidance, 澄清 | real-time | **top catalyst**; NLP classify + sentiment |
| 2 | **董事會決議** (subclass of 重大訊息, keyed on 主旨) | 減資/增資/私募/併購/CB發行/股利/庫藏股決議 | event | high-magnitude discrete events |
| 3 | **庫藏股買回** (`t35sc09`) | planned count/price band/purpose + actual bought-back % | event + progress | bullish + measurable follow-through |
| 4 | **內部人持股轉讓事前申報 / 董監持股 / 質押** (`t56sb01`/`t56sc01`) | insiders file **before** selling; 質押比 (pledge ratio) | event + monthly | leading; rising 質押比 = risk flag |
| 5 | **法說會** (`t100sb02_1`) | investor-conference schedule + presentation PDFs/audio | event (pre-announced) | scheduled catalyst + guidance for NLP |
| 6 | **自結損益 / 財務預測** | unaudited management earnings weeks before 財報; guidance | event/monthly | earliest earnings read |

Risk/governance overlays (Tier B): **資金貸與/背書保證** (`t05st05`), **訴訟/裁罰/財報更正**,
**財報申報行事曆/延遲** — red-flag gates + earnings-day anticipation. Note: ESG *summary
numbers* and 除權息/股利 detail are already in gov OpenAPI (`t187ap46_L_*`, `t187ap45_L`) —
MOPS adds only the ranking/PDFs and the announcement stream, not the periodic aggregates.

#### Company profile (公司基本資料) — the "個股資訊卡" backbone
The company header card (產業分類, 成立年份, 董事長, 總部, 官網, 市值…) plus a financial
snapshot. All `public`; assembled from:

| Field group | Source |
|-------------|--------|
| 產業分類, 成立年份, 董事長, 總部地址, 官方網站, 英文名, 上市/上櫃日期, 實收資本額 (股本), 發言人, 簽證會計師, 股務代理, ISIN | **MOPS `t05st03` 公司基本資料** + TWSE/TPEX 上市/上櫃公司基本資料 CSV + FinMind `TaiwanStockInfo` (產業別) |
| 集團企業 / 轉投資 / 從屬公司 (relationship graph) | MOPS (從屬公司/轉投資) — scrape |
| 大股東 (>10%) | MOPS 大股東 — scrape |
| 公司治理評鑑 ranking (前5%/6–20%…), 永續報告書 PDFs | MOPS — scrape |
| **市值** | FinMind `TaiwanStockMarketValue` |
| **本益比 / 股價淨值比 / 殖利率** | FinMind `TaiwanStockPER` |
| **季/月營收 + YoY%** | FinMind `TaiwanStockMonthRevenue` (aggregated to quarter) |
| **毛利率 / 營益率 / 淨利率 / EPS** | **derived** from FinMind `TaiwanStockFinancialStatements` |
| 股利政策 / 除權息 | FinMind `TaiwanStockDividend` / `...DividendResult` |

Static profile fields are cached and refreshed slowly (they rarely change); the financial
snapshot is recomputed each period. The card is a *composition* — no single upstream returns
all of it, so the profile tool assembles it.

### 2.8 News & social sentiment (near-real-time, scraped/keyless)
A freshness + sentiment layer on top of the structured data — all `public`, all
NLP-classified, **non-trading**. Two purposes, kept separate:

**Stock sentiment (feeds analysis):**
- **Financial RSS** (near-real-time, faster than FinMind's ~T+1 news): 鉅亨網 cnyes (台股 +
  頭條), 經濟日報 udn money, TechNews / TechNews Finance, iThome, BlockTempo. (ctee/ltn excluded
  — Cloudflare 403 / malformed XML.)
- **GDELT Doc 2.0** — keyless global news search by phrase/ticker (title/summary/source/time).
- **Finnhub** — company news (esp. US/intl names).
- **PTT `Stock` (股票版)** + **`Tech_Job` (科技業板)** — retail/industry chatter: title, author,
  **推文數 (push_count)**, tag (`[標的]`/`[新聞]`/`[心得]`), body + 推/噓 comments. Signal =
  buzz/attention + crowd sentiment, explicitly caveated as **unverified retail opinion**.

**Off-topic buzz / mascot (NOT analysis):**
- **PTT `Beauty` (表特版)** + **`Gossiping` (八卦版)** — general-buzz / trending signal powering
  a whimsy/mascot surface, kept **strictly out of the stock-analysis path** (never cited as an
  investment rationale). Included per product choice; segregated by tag so guardrails can keep
  it away from any market claim.

FinMind `TaiwanStockNews` (§2.9 Others) remains the per-stock historical news backbone; this
layer adds the fresh leading edge + social buzz.

### 2.9 Convertible bonds (6), Others (3), International & Macro
- **CB (6):** `TaiwanStockConvertibleBondInfo` (總覽), `...Daily` (日成交), `...InstitutionalInvestors`
  (法人), `...DailyOverview` (每日總覽), `...PutProvision` (賣回權時程),
  `TaiwanStockConvertibleBondMonthlyAnalysis` (月份分析). All B·S.
- **Others (3):** `TaiwanStockNews` (F), `TaiwanBusinessIndicator` (景氣對策信號),
  `TaiwanStockIndustryChain` (產業鏈分類).
- **International (9):** `USStockInfo`, `USStockPrice`, `USStockPriceMinute` (B·S, minute),
  `UKStockInfo`, `UKStockPrice`, `EuropeStockInfo`, `EuropeStockPrice`, `JapanStockInfo`,
  `JapanStockPrice`.
- **Macro:** `TaiwanExchangeRate`, `InterestRate`, `GoldPrice`, `CrudeOilPrices`,
  `GovernmentBondsYield`, `CnnFearGreedIndex`.

### 2.10 TDCC (集保) & TWSE non-OpenAPI surfaces — gaps & duplicates
Audited against our FinMind + OpenAPI stack. Most chip data is **already covered**; a few
ETF/dispersion/intraday items are genuine gaps.

**Already covered — do NOT re-ingest (flagged to prevent duplication):**
- **TDCC 集保戶股權分散表** (weekly, 15 lot brackets, open CSV `getOD.ashx?id=1-5`) is wrapped
  by FinMind **`TaiwanStockHoldingSharesPer`** — whose columns `HoldingSharesLevel, people,
  percent` already give **股東人數 (人數)** and the **千張大戶比率** (level 15 = >1,000,000 股).
  So the "commonly-missed" dispersion pair (股東人數 + 大戶持股比率) is **derivable from data we
  already have** — just compute trends. No new adapter.
- Per-stock 外資/投信/自營 net (TWSE `TWT38U`/`TWT44U`/`TWT43U`) ≈ FinMind
  `TaiwanStockInstitutionalInvestorsBuySell(Wide)`; 融資融券 `TWTB4U` ≈ FinMind margin; 借券賣出
  `TWT72U` ≈ FinMind `TaiwanStockSecuritiesLending`; 當沖 `TWTASU` ≈ FinMind `TaiwanStockDayTrading`;
  處置 ≈ FinMind `TaiwanStockDispositionSecuritiesPeriod`; 除權息 `TWT49U`/股利 ≈ FinMind 股利.

**Genuine gaps worth adding (ETF-focused agent):**
| Signal | Source | Cadence | Access |
|--------|--------|---------|--------|
| **ETF 受益人數** (beneficiary count) — cleanest retail-inflow proxy for 0056/00878/00919… | TDCC / SITCA 投信投顧公會 | weekly/monthly | scrape/file → `public` |
| **ETF NAV / IOPV / 折溢價** (premium-discount, fair value) | TWSE `fund/` + issuer + MIS IOPV | daily / real-time | scrape → `public` |
| **注意股 (attention)** — precursor to 處置 | TWSE `announcement/notice` + TPEX warning | daily | OpenAPI/scrape → `public` |
| **停資停券預告** — forced-unwind warning around AGM/ex-dates | TWSE announcement family | event | scrape → `public` |
| **未含金融電子 / 產業分類 sub-indices** — sector-rotation baseline | TWSE `MI_INDEX` | daily | OpenAPI → `public` |
| **MIS best-5 bid/ask + intraday** (`mis.twse.com.tw`) — only free orderbook/intraday | TWSE MIS | real-time (5s) | keyless → `public`, via `twse_mis.py` |

These attach to existing adapters: ETF NAV/受益人數 → a small `tdcc`/`etf` extension; 注意股/
停資停券/sub-indices → `twse_openapi.py` + announcement scrape; best-5 → the existing
`twse_mis.py` (§bounded observation). All `public`, non-trading, caveated.

### 2.11 主動式ETF analytics & 大盤 views (mostly derived)
The 主動式ETF / 大盤 dashboards (operator daily-report, cross-ETF money-flow, index
contribution, advance/decline distribution) are **derived** from data we already hold:

| View | Built from | Class |
|------|-----------|-------|
| ETF **操作日報** (新增/刪除/加碼/減碼, 變動張數, 權重±) | FinMind `TaiwanStockActiveETFHoldingChange` + `...Holding` | sponsor |
| ETF **折溢價** (股價 vs 淨值) | `get_etf_nav_premium` (§2.10) | public |
| **資金持股** per-stock: 主動式檔數 (# active ETFs holding), 持有市值/張數, 佔個股比重 | **cross-ETF aggregation** of `TaiwanStockActiveETFHolding` | sponsor |
| **資金流向** 流入/流出最多, 多空共識 (買賣檔數 n:m), 加碼/減碼 counts | **aggregation** of `TaiwanStockActiveETFHoldingChange` | sponsor |
| **指數貢獻排行** (per-stock point contribution to 加權/櫃買) | derived: `TaiwanStockMarketValueWeight` × daily move | public |
| **個股漲跌分佈** (>5%/3–5%/…/平盤 buckets) + 漲跌家數 | derived: all-stock daily prices (`STOCK_DAY_ALL` / `TaiwanStockPrice`) | public |
| 產業別漲跌 (類指數) | `get_sector_indices` (§2.10) | public |

**Partial gap:** ETF **基金規模 (AUM)** = 流通在外單位數 × NAV — units-outstanding from TWSE
`fund/` reports (+ TDCC); add as a small `public` fetch. Everything else is compute over
existing feeds (cross-ETF aggregation inherits `sponsor` since it reads active-ETF holdings).

#### Reverse view — "被 N 支 ETF 持有" (stock → ETFs holding it)
For a stock, list every ETF holding it with 持倉比重(+增減) and 持股張數(+增減), filterable by
ETF type (市值型/科技型/主動型/配息型/債券型) and date. Built by **inverting the holdings index**:

| ETF type | Holdings source | Coverage |
|----------|-----------------|----------|
| **主動型 (active)** | FinMind `TaiwanStockActiveETFHolding` (component → ETF reverse index) + `...HoldingChange` for 增減 | ✅ have it (`sponsor`) |
| **市值型/科技型/配息型/債券型 (passive/傳統)** | issuer **PCF (portfolio composition file)** / 投信投顧公會 / TWSE `fund/` daily constituents | ⚠️ **gap** — needs scrape (`public`); this is the "ETF 成分股/權重" gap noted in §4 |

To fully reproduce the screenshot ("被 45 支 ETF 持有" across all types), add a **passive ETF
holdings** source (daily issuer PCF constituents+weights). We maintain a normalized
`etf_holdings(etf_id, stock_id, weight, shares, date)` table (active from FinMind + passive
from PCF) so both the forward (ETF → holdings) and reverse (stock → ETFs) views are one query.

### 2.12 yfinance — analyst consensus / ESG / classification (narrow gap-filler)
Wraps yfinance for the **few fields it uniquely adds for `.TW`/`.TWO`**; **not** a price/
financials source. `data_class: public`, `timing_class: reference`. Coverage is cap-tiered
(good large-cap, sparse mid/small) and the API is unofficial → cache + retry + degrade via M5Q.

| Field | Value | TW coverage |
|-------|-------|-------------|
| `analyst_price_targets`, `recommendations_summary`, `upgrades_downgrades` | **目標價 / 買賣評等 / 升降評** — the licensed gap | large-cap good, mid/small sparse |
| `earnings_estimate`, `revenue_estimate`, `eps_trend`, `growth_estimates`, `earnings_history` | forward EPS/revenue **consensus** + surprise history | large-cap partial |
| `info` scalars: `targetMeanPrice`, `numberOfAnalystOpinions`, `recommendationKey`, forward EPS/PE | quick consensus summary | gate on `numberOfAnalystOpinions > 0` |
| `sustainability` | ESG risk score (Sustainalytics) | large-cap only |
| `info.sector`/`industry`, `Search`/`Lookup` | English GICS-like taxonomy + symbol resolution ("台積電"→2330.TW) | good |

**Skipped (empty/US-only for TW or already covered):** prices, financials, dividends,
`institutional_holders`/`mutualfund_holders`/`insider_*` (US 13F/Form-4 → empty for TW),
`options`, `funds_data` ETF holdings, `Market.status/summary`, news, macro. Also skip the
yfinance **`Sector`/`Industry` aggregate methods** (`top_companies`, `top_etfs`, `industries`,
`top_performing_companies`) — Yahoo scopes them US-first and they're **sparse/weak for TW**.

**No conflict with FinMind on sector/industry (different taxonomies, clear ownership):**
- **FinMind owns TW classification** — `TaiwanStockInfo.industry_category` (上市/上櫃 產業別) and
  `TaiwanStockIndustryChain` (產業鏈/供應鏈 relationships) are the **authoritative, native**
  per-stock TW sector + chain graph. This is the source of truth for anything TW-facing.
- **yfinance contributes only** the **English cross-market taxonomy label** (`info.sector`/
  `industry`, Yahoo's global GICS-like scheme) — used to map a TW name into the *same* taxonomy
  as our US/UK/EU/JP coverage for cross-market peer reasoning — plus `Search`/`Lookup` symbol
  resolution. It is **not** used as the TW 產業別 authority; where the two disagree, FinMind wins.

**Governance (non-trading):** analyst targets/ratings are **third-party opinion**, surfaced with
explicit attribution ("Yahoo-aggregated consensus, N analysts, as of <date>") and **never**
restated as our recommendation. Empty scaffolding is never shown as data (coverage-gated).

### 2.13 Calendar / upcoming-events sources (forward-dated)
Powers the events calendar ([modules/18](modules/18-calendar-events.md)). Most events are
**derived (rule engine)** or come from existing feeds (FinMind dividend/CB/disposition, MOPS
法說/股東會/庫藏股, TPEX/TWSE pre-announce). Genuinely **new forward-dated scrapers** (`public`):

| Event | Source (new scrape) | Cadence |
|-------|---------------------|---------|
| **Index rebalance / 定期審核** (0050/0056/00878/00919 underlyings) | **台灣指數公司 (TIP)** taiwanindex.com.tw + **MSCI** (Feb/May/Aug/Nov) + **FTSE** (Mar/Jun/Sep/Dec) | scheduled far ahead |
| **Macro release calendar** (dates, not values) | DGBAS (CPI/GDP), 財政部 (出口), 國發會 (景氣信號), 央行 理監事會議; external FOMC/US-CPI | annual schedule (scrape once/yr) |
| **IPO 承銷/抽籤 dates** | TWSE/TPEX 承銷 announcement | event |

Rule-derived (no feed): 期貨/選擇權結算 (3rd Wed / weekly Wed), 財報 deadlines (Q1 5/15 · Q2
8/14, 金融 8/31 · Q3 11/14 · 年報 3/31), 月營收 by-10th window, 融券強制回補 (停止過戶−6營業日),
交易日曆/休市 (`TaiwanStockTradingDate` + NCDR). **月營收/財報 have no per-company scheduled
date** — only statutory deadlines → render as markers, reconcile on release.

## 3. Government OpenAPI highlights (authoritative, free, latest-only)

- **TWSE:** `STOCK_DAY_ALL` (日成交), `BWIBBU_ALL` (PER/殖利率/PBR), `T86` (三大法人), `MI_MARGN`
  (融資融券), `TWT84U` (當沖), `MI_QFIIS_*` (外資持股), `t187ap05_P` (月營收),
  `t187ap06/07` (財報), `t187ap45_L` (股利), ESG `t187ap46_L_*`, `t187ap47_L` (基金/ETF).
- **TPEX (225):** OTC equivalents + 興櫃 (`tpex_esb_*`), 權證, 債券, indices/constituents
  (`tpex50_*`, `tphd_*`), block trades, governance/ESG (`mopsfin_t187ap*_O`).
- **TAIFEX (135):** `DailyMarketReportFut/Opt`, `PutCallRatio`, 三大法人 (10 endpoints),
  `OpenInterestOfLargeTraders*`, settlement/margins/specs, `DailyForeignExchangeRates`.

## 4. Gap analysis → build strategy

| Category | Source strategy |
|----------|-----------------|
| 三大法人, 融資融券, 借券, 當沖, 外資持股, PER/yield, 月營收, 財報, 期權OI/PC, **股權分級/大戶** | **Direct** — gov OpenAPI or FinMind Free/Backer (`public`; 股權分級 via Backer `TaiwanStockHoldingSharesPer`) |
| 分點券商進出, 八大官股, 產業鏈資金流, 主動ETF持股 | **FinMind Sponsor** (`sponsor`) — replaces scrapers |
| minute-K, tick, 週/月K, 還原股價 (whole-market) | **FinMind Backer/Sponsor** |
| real-time snapshots | **FinMind Sponsor** → `sponsor` data_class (Premium/Pro; masked teaser otherwise) |
| 技術指標, 財務比率/杜邦/FCF, PE/PB 河流圖, 法人連續買賣超, 籌碼集中度 | **Derive** — our compute layer over the raw feeds |
| **重大訊息/董事會決議/庫藏股/內部人異動/法說會/自結** (event catalysts) | **MOPS scrape** — no open API; the unique catalyst layer (§2.7) |
| news freshness + 社群情緒 (RSS/GDELT/Finnhub/PTT) | **News/social layer** (§2.8) — near-real-time RSS + keyless GDELT + Finnhub + PTT; NLP sentiment |
| **passive/傳統 ETF 成分股/權重** (市值型/科技型/配息型/債券型) → reverse "被 N 支 ETF 持有" | **Issuer PCF scrape** (§2.11) — 投信投顧公會/TWSE `fund/`; active side already via FinMind |
| **analyst 目標價/評等, earnings/revenue consensus** (large-cap) | **yfinance** (§2.12) — Yahoo-aggregated consensus for `.TW`/`.TWO`; attributed third-party estimate, coverage-gated |
| order-book depth, curated 供應鏈圖譜, mid/small-cap analyst coverage | **Absent / paid** — out of scope; note as caveats |

## 5. How FinMind fits the framework

- New adapter `backend/src/sources/finmind.py` implementing the same `SourceAdapter` contract.
  It is the **only adapter that carries a Bearer token** — supplied via env
  (`FINMIND_TOKEN`), **never stored** (consistent with [governance.md](governance.md)).
- **data_class mapping for FinMind datasets:**
  - `public`: datasets available on FinMind Free/Backer (EOD/fundamental + most history) —
    free to all our users.
  - **`sponsor`**: datasets requiring the FinMind **Sponsor** plan — 分點, 八大官股,
    產業鏈資金流, 主動ETF持股, tick/minute-K, and `taiwan_*_snapshot` real-time.
    Served to Premium/Pro; masked teaser (blurred + upgrade CTA) for Free users. No approval
    gate; still non-streaming / user-driven per governance §1.
    (Note: 股權分級/大戶 `TaiwanStockHoldingSharesPer` is **Backer** → `public`, not sponsor.)
- **FinMind plan tier availability** is surfaced through the **M5Q source-health** view; a
  dataset the current token can't access reports as `unavailable-tier`, not a hard error. There
  is no live upstream quota poll — the platform reserves its own budget locally: a single
  static Redis-backed ceiling (6000 req/hr) shared across every Premium/Pro user, atomically
  reserved before each Sponsor-tier fetch and refunded on failure/under-use (see
  [modules/12](modules/12-multi-user-tiers.md) §3). Free tier never draws on this pool at all —
  there is no Free-tier FinMind quota concept.
- **Timing classes:** FinMind EOD datasets = `official-eod` (with a "T+1 aggregator"
  caveat); snapshots = `live-ish`; news/macro = `reference`.
- FinMind's `storage_objects` bulk parquet (tick/minute-K/分點) is used for whole-day
  pulls where available, ignoring `data_id`.

## 6. Verification notes for adapters

- Re-fetch specs anytime: `openapi.twse.com.tw/v1/swagger.json`,
  `tpex.org.tw/openapi/swagger.json` (needs `User-Agent` header),
  `openapi.taifex.com.tw/swagger.json`.
- FinMind: `GET /translation?dataset=...` gives column CH/EN mapping; `GET /datalist`
  enumerates valid `data_id`s. There is no live quota-polling endpoint in use — see §5's note on
  the real, locally-reserved Sponsor-pool mechanism.
- Data quirks to encode in validators: `TaiwanStockPrice` publishes `0` for no-trade days
  (filter on `close > 0`, not volume); `PriceAdj` forward-fills instead; emerging-board
  `open` = previous-day average, can fall outside [min,max].
