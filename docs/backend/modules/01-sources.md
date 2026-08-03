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
    timing_class: TimingClass
    fetched_at: datetime          # UTC
    currentness_caveat: str       # human-readable staleness statement
    payload: dict                 # validated against the source contract
    health: SourceHealth          # ok / stale / unreachable + detail

class SourceAdapter(Protocol):
    source_id: str
    timing_class: TimingClass
    def supports(self) -> set[tuple[Capability, Market]]: ...   # what capabilities, which markets
    def fetch(self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **params) -> SourceResult: ...
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
| `tpex_openapi.py` | `TPEX_OPENAPI` | official-eod | OTC + 興櫃; 225 endpoints; needs UA header |
| `taifex_openapi.py` | `TAIFEX_OPENAPI` | official-eod | Derivatives EOD/reference; 135 endpoints |
| `twse_mis.py` | `TWSE_MIS` | live-ish | Browser-observed snapshot; free → `public` |
| `taifex_mis.py` | `TAIFEX_MIS` | live-ish | M8C-02 staged behind validation flag |
| `ncdr_closure.py` | `NCDR_DGPA_CLOSURE_CAP` | event-driven | Work-suspension/closure evidence |
| `finmind.py` | `FINMIND` | official-eod / live-ish / reference | 75+ history-enabled datasets; **only token-bearing adapter** |
| `mops.py` | `MOPS` | event-driven / reference | 公開資訊觀測站 disclosure stream — **scraped** (no open API) |
| `news.py` | `NEWS` | reference (near-real-time) | Financial RSS (鉅亨/經濟日報/TechNews…) + GDELT (keyless) + Finnhub |
| `ptt.py` | `PTT` | reference (near-real-time) | PTT boards — Stock/Tech_Job (stock buzz) + Beauty/Gossiping (off-topic buzz) — **scraped** |
| `etf_pcf.py` | `ETF_PCF` | official-eod | Passive/傳統 ETF 成分股/權重 (issuer PCF / 投信投顧公會 / TWSE `fund/`) + 受益人數 + NAV/units — **scraped** |
| `web_search.py` | `WEB` | reference (live) | **Brave Search API** (provider-agnostic) — general web search + fetch; the fallback when the LLM has no native search |
| `yfinance.py` | `YFINANCE` | reference (near-real-time) | **Analyst consensus/target prices** (the one licensed gap) + ESG + English sector/symbol-resolution for `.TW`/`.TWO` — **narrow scope** |
| `calendar_ext.py` | `CALENDAR_EXT` | reference / scheduled | Forward-dated calendar gaps — **scraped**: index rebalance (TIP/MSCI/FTSE), macro release calendar (DGBAS/財政部/國發會/央行/FOMC), IPO 承銷/抽籤 |

The full endpoint/dataset inventory these adapters wrap is in
[../data-catalog.md](../data-catalog.md).

## FinMind adapter — special handling

FinMind is the **history/backfill engine** and fills the professional-grade gaps (分點,
八大官股, 股權分級, 產業鏈資金流, tick/minute-K, real-time snapshots). It differs from the
gov adapters:

- **Token auth via env** (`FINMIND_TOKEN`), Bearer header — never stored (governance §6).
- **Per-dataset timing class:** EOD/fundamental/chip → `official-eod` (T+1 aggregator
  caveat); `taiwan_*_snapshot` → `live-ish`; news/macro → `reference`.
- **Per-dataset data_class:** FinMind Free/Backer datasets → `public`; **FinMind Sponsor
  datasets → `sponsor`** (分點, 八大官股, 股權分級, 產業鏈資金流, tick/minute-K, real-time
  snapshots, active-ETF holdings) — entitlement-masked for unentitled users, not approval-gated.
- **FinMind membership tiers** (Free/Backer/Sponsor) and the **tier-aware rate limit** (Free
  token **600/hr**, Sponsor token **6000/hr**) are reported through M5Q; an out-of-plan
  dataset returns `unavailable-tier`, not a hard failure.
- Uses `/translation` (column CH/EN map), `/datalist` (valid `data_id`s), and
  `storage_objects` (whole-day parquet for tick/minute-K/分點).

## MOPS adapter — the catalyst layer (scraped)

MOPS (公開資訊觀測站) is the **event-driven disclosure stream** — 重大訊息, 董事會決議, 庫藏股,
內部人持股轉讓事前申報/質押, 法說會, 自結/財測 (see [../data-catalog.md](../data-catalog.md) §2.7).
It has **no official JSON API**, so this adapter is the one **scraper**:

- POSTs MOPS forms (`mopsov.twse.com.tw/mops/web/<report_id>`); handles **ROC-year dates** and
  **Big5/UTF-8** quirks; parses HTML → typed models. For real-time 重大訊息 it may instead poll
  the TWSE/TPEX announcement lists (easier, same data).
- Emits structured records (announcement type, subject, filed_at, parsed fields) — **never raw
  HTML** downstream. `timing_class`: 重大訊息/董事會/庫藏股/內部人/法說會 = `event-driven`;
  基本資料/集團/評鑑 = `reference`. All `public` data_class.
- **Politeness & resilience:** rate-limited, backed off, cached with honest staleness; a
  scrape failure surfaces via M5Q as degraded, never fabricated. Still **user-driven only** —
  no startup calls, no high-frequency polling (governance §1.3); the scheduler may refresh the
  announcement list at a coarse cadence under the §9a carve-out.
- Text disclosures feed the agent's NLP (classify contract-win vs litigation vs guidance;
  extract magnitude/sentiment) — all output stays caveated and **non-trading**.

## News & social adapters — freshness + sentiment

`news.py` and `ptt.py` add a near-real-time news + social layer (see
[../data-catalog.md](../data-catalog.md) §2.8). All `public`, all output caveated & non-trading.

- **`news.py`:** parses financial **RSS** (鉅亨網 cnyes 台股/頭條, 經濟日報 udn money, TechNews,
  iThome, BlockTempo), **GDELT** Doc 2.0 (keyless phrase search), and **Finnhub** company news.
  Emits `{title, link, source, published_at, summary}`; RSS/GDELT keyless, Finnhub token via env.
- **`ptt.py`** (scraper): boards tagged by purpose —
  - `Stock` (股票版), `Tech_Job` (科技業板) → **stock buzz**: title, author, **推文數**, tag
    (`[標的]/[新聞]/[心得]`), body + 推/噓; usable in analysis but flagged **unverified retail opinion**.
  - `Beauty` (表特版), `Gossiping` (八卦版) → **off-topic buzz** for a whimsy/mascot surface;
    tagged `offtopic` so guardrails keep it out of any market claim.
- Both are **user-driven**, rate-limited, politely backed off, honest staleness via M5Q — no
  high-frequency polling; a coarse refresh may run under the §9a scheduler carve-out.

## yfinance adapter — narrow-scope analyst/ESG gap-filler (`.TW`/`.TWO`)

`yfinance.py` wraps the yfinance library for the **few fields it uniquely adds for Taiwan** — it
is **not** a general price/financials source (those stay on FinMind/TWSE; yfinance TW OHLCV/
financials are lower-quality). Scope is deliberately tight:
- **Analyst consensus (primary)** — `analyst_price_targets` (mean/median/high/low),
  `recommendations_summary`, `upgrades_downgrades`, `earnings_estimate`/`revenue_estimate`/
  `eps_trend`/`growth_estimates`, and `info` scalars (`targetMeanPrice`, `numberOfAnalystOpinions`,
  `recommendationKey`, forward EPS/PE). **This is the licensed "analyst ratings/target prices"
  gap** — no free TW-native source has it.
- **ESG** — `sustainability` (Sustainalytics risk score, large-cap only).
- **English classification / resolution** — `info.sector`/`info.industry` (maps TW names into the
  same taxonomy as our US/UK/EU/JP coverage) and `Search`/`Lookup` (resolve "台積電"/"TSMC"→2330.TW).
  **No conflict with FinMind:** FinMind `TaiwanStockInfo.industry_category` + `TaiwanStockIndustryChain`
  are the **authoritative TW 產業別 + 產業鏈** (source of truth); yfinance adds only the *English
  cross-market label*. Its `Sector`/`Industry` **aggregate** methods (`top_companies`/`top_etfs`/
  `industries`) are US-skewed → **skipped for TW**. Where the two disagree, FinMind wins.
- **Explicitly NOT via yfinance** (already covered or **empty/US-only for `.TW`/`.TWO`**): prices,
  financials, dividends, `institutional_holders`/`mutualfund_holders`/`insider_*` (US 13F/Form-4
  only → empty for TW), `options` (no TW equity options), `funds_data` ETF holdings (sparse/≤10),
  `Market.status`/`summary` (US-only), news, macro.

**Coverage gating & reliability:** yfinance is an **unofficial** Yahoo scraper — breakage/rate-
limit/empty-response risk. `data_class = public`, `timing_class = reference`. Rules: gate analyst
rendering on `numberOfAnalystOpinions > 0` + non-empty frames (never show empty scaffolding as
data); gate ESG on non-null `sustainability`; coverage is cap-tiered (good large-cap, sparse
mid/small `.TWO`); cache, retry/backoff, and **never let a yfinance outage break a core flow**
(degrade via M5Q). Pin the library version.

**Fetch pattern (never on the request hot path):**
- **Batch, pre-downloaded, weekly.** The weekly `ingest-event` job (module 22) **batch-fetches**
  the covered large-cap symbol list via **`yf.download([...], group_by='ticker')`** (one call for
  many tickers, not per-symbol per-request) and writes the result to Parquet. Requests serve the
  **pre-downloaded Parquet** — if Yahoo is down/rate-limited, the core flow is unaffected (analyst
  section simply shows "unavailable" per the coverage gate). yfinance is **never** called
  synchronously inside `/agent/query` or a page load.
- **Set `yf.set_tz_cache_location(...)` explicitly at adapter init** → a **writable, persistent**
  path (dev `./.cache/yf_tz`; prod a mounted volume). The library caches timezone lookups there;
  the default path can be read-only/ephemeral in a container (permission error or re-fetch every
  run). One-line call in the adapter's module init, before the first `download`/`Ticker`.

`web_search.py` is the **provider-agnostic** web-search backend (**Brave Search API**, key via
`BRAVE_API_KEY`): `search(query) → [{title, url, snippet, published}]` + `fetch(url) → text`.
It is used when the active LLM has no native search, or always if configured. The
**LLM-native** search path (Anthropic `web_search` tool / OpenAI / Gemini grounding) lives in
the LLM adapter ([04-llm-adapter.md](04-llm-adapter.md)), not here — but both surface through
the same `web_search` tool ([03-tools.md](03-tools.md)) and are governed identically (results
cited, caveated, non-trading).

## Governance obligations

- Enforce the **M6D TLS policy** (`strict` default; no silent fallback). See
  [../governance.md](../governance.md).
- **No caching that masks staleness** and **no retries that hide failure** — surface
  health honestly so M5Q can report it.
- **No startup network calls.** Adapters fetch only when explicitly invoked by a tool.
- Every result carries `source_id`, `timing_class`, `fetched_at`, `currentness_caveat`.

## Testing

Recorded fixtures per adapter; the default and CI profiles are **non-network**. Live
fetch is exercised only under the `operator-preflight` / `browser-e2e` profiles.
