# Module: Data-source adapters (`backend/src/sources/`)

## Responsibility

The **only** layer permitted to perform network I/O. Each adapter fetches from one
upstream source, validates the response against a contract, and returns a typed model
tagged with provenance and a currentness caveat.

## Public interface

```python
class TimingClass(str, Enum):
    LIVE_ISH = "live-ish"        # bounded snapshot, NOT streaming
    OFFICIAL_EOD = "official-eod"
    EVENT_DRIVEN = "event-driven"
    REFERENCE = "reference"      # news/social/web/static — near-real-time or slow-moving

class SourceResult(BaseModel):
    source_id: str
    data_class: DataClass         # public / sponsor — entitlement gate reads this
    timing_class: TimingClass
    fetched_at: datetime          # UTC
    currentness_caveat: str       # human-readable staleness statement
    payload: dict                 # validated against the source contract
    health: SourceHealth          # ok / stale / unreachable / unavailable-tier + detail

class SourceAdapter(Protocol):
    source_id: str
    timing_class: TimingClass
    def supports(self) -> set[tuple[Capability, Market]]: ...   # what capabilities, which markets
    async def fetch(self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **params) -> SourceResult: ...
    def validate(self, raw: dict) -> dict: ...   # raises on contract violation
```

**Adapters are never called directly by callers.** The **data-access router**
([19-data-access-router.md](19-data-access-router.md)) resolves `(market, capability) → ordered
adapters` from config and calls `fetch` in priority order with fallback. `supports()` is the
adapter's declaration of *what capabilities it provides and for which markets* — so adding a
market or source is config, not caller changes. This adapter table below is the TW-era set; each
adapter's `supports()` set widens as more markets are added (e.g. `yfinance` already covers
US/GB/JP `price_eod`; `finmind` covers US/JP prices too).

## Adapters

| File | source_id | timing_class | Notes |
|------|-----------|--------------|-------|
| `twse_openapi.py` | `TWSE_OPENAPI` | official-eod | Latest EOD, no backfill (no params) |
| `exchange_whole_market/` | `EXCHANGE_OFFICIAL` | official-eod | Ingest-only, date-keyed whole-market reports of TWSE (`www.twse.com.tw/rwd`) + TPEx (`www.tpex.org.tw/www/zh-tw`) merged into one payload: price EOD, 三大法人 (long rows), 融資融券, PER/PBR/殖利率. Free, keyless, any past date. Asked **before** FinMind by the ingest runner (`config/whole_market_source_priority.yaml`, ingest module §1b); not a `SourceAdapter`, not routable. Per-host request spacing (TWSE 2.5 s, TPEx 2.0 s), retry with backoff; warrants/CBBC and 興櫃 not included |
| `company_filings/` | `OFFICIAL_FILINGS` | official-eod | Ingest-only, period-keyed company filings from MOPS: monthly revenue (static report pages, listed + OTC + emerging, any month back to at least 2006, Big5 HTML, 3 requests per month) and the income / balance-sheet / cash-flow summary reports (form POST per market, season and statement, aggregate lines in FinMind's items, income reduced to single quarters), 3 s apart. Asked **before** FinMind by the ingest runner (ingest module §1c); not a `SourceAdapter`, not routable. Answers a date that holds no rows by definition with `ok` + no rows (the runner stops there) and a period not yet complete with `stale` (the next source is asked) |
| `tpex_openapi.py` | `TPEX_OPENAPI` | official-eod | OTC 上櫃 price_eod only, via `tpex_mainboard_daily_close_quotes` — **1 of ~225** upstream endpoints wired; needs UA header; TLS-strict fails against this server's cert (real upstream defect — see file docstring), so returns `UNREACHABLE` under the default `strict` TLS policy. **Not instantiated in `dataaccess/composition.py`** — unlike the adapters wired there, `TpexOpenApiAdapter` has no adapter-map entry and no `source_priority.yaml` row, so it isn't reachable through the production router today despite being fully implemented and tested |
| `taifex_openapi.py` | `TAIFEX_OPENAPI` | official-eod | Derivatives `derivatives_oi` only, via `DailyMarketReportFut` — **1 of ~135** upstream endpoints wired |
| `twse_mis.py` | `TWSE_MIS` | live-ish | Browser-observed snapshot; free → `public` |
| *(not built)* | `TAIFEX_MIS` | live-ish | `context/builder.py`'s `enable_taifex_mis_validation` flag is staged (M8C) but there is no adapter behind it yet — like `twse_mis.py`, this would be a browser-observed bounded snapshot, **never a scraper**, once built |
| `ncdr_closure.py` | `NCDR_DGPA_CLOSURE_CAP` | event-driven | Work-suspension/closure evidence; declares `(CALENDAR_EVENTS, TW)`, is instantiated as `"ncdr_closure": NcdrClosureAdapter()` in `dataaccess/composition.py`'s adapter map, and has a `calendar_events: [ncdr_closure]` row in `config/source_priority.yaml` — wired and reachable through the router today (see [18](18-calendar-events.md)) |
| `tdcc/` (package: `_adapter.py`/`_registry.py`/`_shared.py`/`raw_query.py`) | `TDCC` | official-eod | 集保結算所 (TDCC) OpenData — free, keyless, TW-only. Serves 6 capabilities: `tdcc_securities_info`, `tdcc_pledged_custody_shares`, `tdcc_shareholding_distribution`, `tdcc_offshore_fund_nav`, `tdcc_evoting_schedule`, `tdcc_monthly_custody_accounts`; instantiated as `"tdcc": TdccAdapter()` in `dataaccess/composition.py`, with matching rows in `config/source_priority.yaml` |
| `finmind/` (package) | `FINMIND` | official-eod / live-ish / reference | 104 history-enabled datasets across Technical/Chip-Institutional/Fundamental/Derivative/Real-Time/Convertible-Bond/Others/International/Global-Economic; **only token-bearing adapter** |
| `mops.py` | `MOPS` | event-driven / reference | 公開資訊觀測站 disclosure stream via TWSE's own OpenAPI (`t187ap04_L`/`t187ap11_L`) — real JSON, **not scraped**; the legacy HTML scraper this was long assumed to require is dead |
| `news.py` | `NEWS` | reference (near-real-time) | Financial RSS (鉅亨/經濟日報/TechNews…) + GDELT (keyless) + Finnhub |
| `ptt.py` | `PTT` | reference (near-real-time) | PTT boards — Stock/Tech_Job/Warrant (stock buzz) + Beauty/Gossiping (off-topic buzz) — **scraped**; not instantiated in `dataaccess/composition.py` (by design — it is reached through the `dataaccess/ptt.py` gateway with a required board parameter, not through the router) |
| `etf_pcf.py` | `ETF_PCF` | official-eod | **Fund-level metadata only** — listed-ETF roster (issuer/listing date/tracked index, via TWSE `rwd/zh/ETF/list` JSON) + fund basic-data disclosure (units outstanding/manager, via TWSE OpenAPI `t187ap47_L`). Both are TWSE JSON endpoints, not an HTML scrape, despite the file/module name. Per-constituent holdings/weights (成分股/權重) are **not built** — a genuine, disclosed gap, not guessed (受益人次 comes from `etf_theme_tags.py`, premium/discount from `twse_etf_nav.py`) |
| `sitca.py` | `SITCA` | official-eod | ETF/fund internal fees (經理費/保管費/保管機構) from the SITCA monthly fee report; `etf_expense_ratio` capability; always runs under `TLSPolicy.UNSAFE_EXPLICIT` (its cert chain is malformed) |
| `etf_theme_tags.py` | `ETF_THEME_TAGS` | official-eod | TWSE ETF e添富 microsite scrape: 主題/因子 editorial tags + 受益人次; `etf_theme_tags` capability |
| `twse_etf_nav.py` | — | live-ish | Whole-market ETF premium/discount snapshot (`mis.twse.com.tw/stock/data/all_etf.txt`); called by the ETF ingest job (`ingest/jobs/etf.py`), not through the router |
| `financecalendar.py` | — | reference | Third-party US/global macro-release calendar (GDP/CPI/PCE/PMI/NFP/FOMC); reached via the `dataaccess/financecalendar.py` gateway, not the router |
| `web_search.py` | `WEB` | reference (live) | **Brave Search API** (provider-agnostic) — general web search + fetch; the fallback when the LLM has no native search |
| `yfinance/` (package) | `YFINANCE` | reference (near-real-time) | **TW**: analyst consensus/target prices (the one licensed gap) + ESG + English sector/symbol-resolution — narrow. **US/GB/JP/EU**: primary source for price/financials/dividends/splits/analyst/ESG/sector plus yfinance-native capabilities (options, holders, search, calendars, sector/industry aggregates, market status, fund holdings) — plus the `run_screener` module-level carve-out (equity/ETF/fund screening, outside `SourceAdapter.fetch()`) |

**Caching wrapper (not a source, but adapter-shaped):** `caching_adapter.py`'s
`CachingSourceAdapter` wraps any adapter above with a Redis read-through cache before
`dataaccess/composition.py` hands it to the router — see
[02-packages.md](02-packages.md)'s `MarketDataStore` section for the full description.

The endpoint/dataset inventory each adapter wraps is in the adapter's own module under
`backend/src/sources/` (FinMind/TDCC dataset configs live in each package's `_registry.py`; the
capability names are in `schemas/enums.py`).

**Not a `sources/` adapter:** an earlier draft of this table listed a `calendar_ext.py` scraper
(`CALENDAR_EXT`) for index-rebalance/macro-release/IPO dates. The real implementation is
`calendar/rules.py` — a rule-engine computing statutory/exchange dates directly (期貨結算,
選擇權週結算, 財報 deadlines, 月營收, AGM/融券回補), not a scraped source. See
[18-calendar-events.md](18-calendar-events.md) for the real design; there is no `CALENDAR_EXT`
source_id and no separate calendar-scraping adapter.

## FinMind adapter — special handling

FinMind is the **history/backfill engine** and fills the professional-grade gaps (分點,
八大官股, 股權分級, 產業鏈資金流, tick/minute-K, real-time snapshots). It differs from the
gov adapters:

- **Token auth via env** (`FINMIND_TOKEN`), Bearer header — never stored (secrets stay in env; see [../../../backend/CLAUDE.md](../../../backend/CLAUDE.md) §8).
- **Per-dataset timing class:** EOD/fundamental/chip → `official-eod` (T+1 aggregator
  caveat); `taiwan_*_snapshot` → `live-ish`; news/macro → `reference`.
- **Per-dataset data_class:** FinMind Free/Backer datasets → `public`; **FinMind Sponsor
  datasets → `sponsor`** (分點, 八大官股, 股權分級, 產業鏈資金流, tick/minute-K, real-time
  snapshots, active-ETF holdings) — entitlement-masked for unentitled users, not approval-gated.
- **FinMind membership tiers** (Free/Backer/Sponsor) gate which datasets an adapter call may
  request; there is no live upstream quota poll — the platform tracks its own **Sponsor-pool
  budget locally** (a Redis-backed atomic counter against a static **6000 req/hr** ceiling
  shared across all Premium/Pro users, reserved pre-flight and refunded on failure/under-use,
  [16](16-infrastructure.md) §4); Free-tier calls never draw on that pool at all. An out-of-plan
  dataset returns `unavailable-tier`, not a hard failure, surfaced through M5Q.
- Uses `/translation` (column CH/EN map), `/datalist` (valid `data_id`s), and
  `storage_objects` (whole-day parquet for tick/minute-K/分點).

## MOPS adapter — the catalyst layer

MOPS (公開資訊觀測站) is the **event-driven disclosure stream**: 重大訊息 (`t187ap04_L`), 董監事持股
(`t187ap11_L`) and 基本資料 (`t187ap03_L`), served as real JSON through TWSE's own OpenAPI. **It is
not scraped** — the legacy `mopsov.twse.com.tw` HTML forms (ROC-date/Big5 parsing) are dead and the
adapter no longer uses them.

- Each dump is whole-market; `fetch()` still serves one symbol per call (one upstream GET, filtered
  client-side). Capabilities: `corporate_actions`, `shareholding`, `profile`.
- `timing_class`: 重大訊息 = `event-driven`; 董監事持股 and 基本資料 = `reference`. All `public`.
- Emits structured records — **never raw HTML/JSON** downstream. Failures surface through the
  health report as degraded, never fabricated.
- Fetched only when a tool or the user-authorized scheduler asks (no startup calls, no
  high-frequency polling; the scheduler may refresh at a coarse cadence).
- Text disclosures feed the agent's NLP (classify contract-win vs litigation vs guidance;
  extract magnitude/sentiment) — all output stays caveated and **non-trading**.

## News & social adapters — freshness + sentiment

`news.py` and `ptt.py` add a near-real-time news + social layer. All `public`, all output caveated & non-trading.

- **`news.py`:** parses financial **RSS** (鉅亨網 cnyes 台股/頭條, 經濟日報 udn money, TechNews,
  iThome, BlockTempo), **GDELT** Doc 2.0 (keyless phrase search), and **Finnhub** company news.
  Emits `{title, link, source, published_at, summary}`; RSS/GDELT keyless, Finnhub token via env.
- **`ptt.py`** (scraper): boards tagged by purpose —
  - `Stock` (股票版), `Tech_Job` (科技業板), `Warrant` (權證版) → **stock buzz**: title, author, **推文數**, tag
    (`[標的]/[新聞]/[心得]`), body + 推/噓; usable in analysis but flagged **unverified retail opinion**.
  - `Beauty` (表特版), `Gossiping` (八卦版) → **off-topic buzz** for a whimsy/mascot surface;
    tagged `offtopic` so guardrails keep it out of any market claim.
- Both are rate-limited, politely backed off, honest staleness via the health report — no
  high-frequency polling; the user-authorized scheduler may refresh at a coarse cadence.

## yfinance adapter — TW gap-filler, non-TW primary source

The `yfinance/` package plays two different roles depending on market, and this is a deliberate policy
choice, not an oversight:

- **TW: narrow gap-filler** (unchanged from the original scope). Prices/financials stay off
  yfinance for TW — FinMind/TWSE own those (yfinance's TW OHLCV/financials are lower-quality).
  - **Analyst consensus (primary)** — `analyst_price_targets` (mean/median/high/low),
    `recommendations_summary`, `upgrades_downgrades`, `earnings_estimate`/`revenue_estimate`/
    `eps_trend`/`growth_estimates`, and `info` scalars (`targetMeanPrice`,
    `numberOfAnalystOpinions`, `recommendationKey`, forward EPS/PE). **This is the licensed
    "analyst ratings/target prices" gap** — no free TW-native source has it.
  - **ESG** — `sustainability` (Sustainalytics risk score, large-cap only).
  - **English classification** — `info.sector`/`info.industry` (maps TW names into the same
    taxonomy as our US/UK/EU/JP coverage). **No conflict with FinMind:** FinMind
    `TaiwanStockInfo.industry_category` + `TaiwanStockIndustryChain` remain the **authoritative
    TW 產業別 + 產業鏈** (source of truth); yfinance adds only the *English cross-market label*.
    Where the two disagree, FinMind wins.
  - `Sector`/`Industry` aggregate methods (`top_companies`/`top_etfs`/`industries`) stay
    **skipped for TW** (US-skewed data) even though they're now real capabilities for non-TW
    markets (below).

- **US/GB/JP/EU: yfinance is PRIMARY**, not a gap-filler. `config/source_priority.yaml` encodes
  "yfinance before FinMind" for these markets, the inverse of the TW ordering. yfinance now
  serves, for these markets:
  - The **same shared capability names** other adapters use for TW — `price_eod` (via
    `Ticker.history()`), `financials`/`balance_sheet`/`cash_flow_statement` (via
    `Ticker.financials`/`.balance_sheet`/`.cash_flow`), `dividends` (via `Ticker.dividends`),
    `split_price` (via `Ticker.splits`) — so downstream tools/context code never need
    per-source branching to consume a non-TW quote/statement/dividend/split.
  - `analyst_consensus`/`esg`/`sector_class` — the same capabilities as TW, but with real,
    non-sparse coverage; `analyst_consensus` additionally carries a richer set of fields for
    non-TW markets (`recommendations_summary` tally, latest `upgrades_downgrades` action,
    forward EPS/revenue estimate, long-term growth estimate) that TW payloads never populate.
  - New capabilities with **no FinMind/gov equivalent at all**: `options_chain` (`Ticker.options`
    + `.option_chain()`), `institutional_holders` (`Ticker.institutional_holders`/
    `.mutualfund_holders`), `insider_activity` (`Ticker.insider_transactions` — US Form-4-style
    disclosure, expected empty outside the US), `symbol_search`/`symbol_lookup`
    (`yfinance.Search`/`Lookup`), `earnings_calendar`/`ipo_calendar`/`economic_calendar`
    (`yfinance.Calendars`), `sector_overview`/`industry_overview` (`yfinance.Sector`/`Industry`
    top-companies/top-ETFs/top-mutual-funds aggregate views — the market-wide, not per-symbol,
    counterpart to the TW-skipped `Sector`/`Industry` methods above), `market_status`
    (`yfinance.Market` open/closed + summary — verified live: Yahoo's underlying markettime
    endpoint only actually serves the `US` region string; `GB`/`JP`/`EU` degrade to `STALE`
    honestly rather than fabricate a status), `fund_holdings` (`Ticker.funds_data` top holdings
    + sector weightings — relevant to this project's ETF focus).

**Coverage gating & reliability:** yfinance is an **unofficial** Yahoo scraper — breakage/rate-
limit/empty-response risk. `data_class = public`, `timing_class = reference`. Every capability in
this module (TW and non-TW alike) follows the same rule: gate on a real, non-empty upstream
result — analyst rendering on `numberOfAnalystOpinions > 0`, ESG on a non-null score, price/
financials/dividends/splits on a non-empty frame, and so on — never show empty scaffolding as
data; degrade via `HealthStatus.STALE`/`UNREACHABLE`, and **never let a yfinance outage break a
core flow**. Pin the library version.

**Transport note:** yfinance's SDK manages its own HTTP session internally (`requests`-based) —
this codebase's `sources/tls.py` `httpx` client is never constructed inside this adapter; there
is no seam for the caller's `tls_policy` to apply here.

**Fetch pattern today (per-symbol, threadpool-isolated):** every blocking yfinance call —
`Ticker.get_info()`, `.history()`, `.financials`, `Search`, `Lookup`, `Calendars`, `Sector`,
`Industry`, `Market` — runs through `anyio.to_thread.run_sync`, never synchronously on the
request hot path. `yf.set_tz_cache_location(...)` is called once, lazily, before the first such
call, pointed at a writable persistent path (dev `./.cache/yf_tz`, configurable via
`Settings.yf_tz_cache_dir`) — the library's own default timezone-cache path can be read-only/
ephemeral in a container.

**Batch/ingest entry point:** `sources/yfinance/batch.py::batch_download(symbols, ...)` wraps
`yf.download([...], group_by='ticker')` — one call for many tickers, analogous to
`FinMindAdapter.fetch_whole_market`'s ingest-layer carve-out. It is deliberately **not** part of
the `SourceAdapter` protocol (`fetch()` stays strictly per-symbol); the daily US EOD ingest job
(`ingest/us_jobs.py::job_us_price_eod`) calls it.

`web_search.py` is the **provider-agnostic** web-search backend (**Brave Search API**, key via
`BRAVE_SEARCH_API_KEY`): `search(query) → [{title, url, snippet, published}]` + `fetch(url) → text`.
It is used when the active LLM has no native search, or always if configured. The
**LLM-native** search path (Anthropic `web_search` tool / OpenAI / Gemini grounding) lives in
the LLM adapter ([04-llm-adapter.md](04-llm-adapter.md)), not here — but both surface through
the same `web_search` tool ([03-tools.md](03-tools.md)) and are governed identically (results
cited, caveated, non-trading).

## Governance obligations

- Enforce the TLS policy (`strict` default; no silent fallback; the only chokepoint is
  `sources/tls.py`). Rules: [../../../backend/CLAUDE.md](../../../backend/CLAUDE.md) §4 and §8.
- **No caching that masks staleness** and **no retries that hide failure** — surface
  health honestly so M5Q can report it.
- **No startup network calls.** Adapters fetch only when invoked by a tool, the router, or an ingest/scheduler job.
- Every result carries `source_id`, `timing_class`, `fetched_at`, `currentness_caveat`.

## Testing

Recorded fixtures per adapter; the default and CI profiles are **non-network**. Live
fetch is exercised only under the `operator-preflight` / `browser-e2e` profiles.
