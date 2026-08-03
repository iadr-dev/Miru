# Module: Tool registry (`backend/src/tools/`)

The agent's entire action surface. The agent **never** touches network or filesystem
directly — only through registered tools. All tools are **read-only and always runnable**;
there is no safety-tier system and no confirmation/approval gate. The only access control is
the **`sponsor` data-class entitlement** (paid FinMind data). These are also the tools
projected over MCP (see [06-interfaces.md](06-interfaces.md)).

## Tool protocol (`base.py`)

```python
class DataClass(str, Enum):
    PUBLIC = "public"     # gov OpenAPI + FinMind Free/Backer + derived — free for all
    SPONSOR = "sponsor"   # paid FinMind Sponsor data — Premium/Pro only

class Tool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]   # Pydantic v2 — doubles as MCP structured input schema
    output_model: type[BaseModel]  # Pydantic v2 — structured output
    data_class: DataClass          # public | sponsor
    def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel: ...
```

`ToolContext` carries the `user_id`/entitlement, the active TLS policy, and the journal
handle. There is no confirmation token.

## Registered tools (grouped)

Folder groups: `tools/market/`, `tools/context/`, `tools/health/`.

### `public` — free for everyone (incl. anonymous & MCP)
- `get_canonical_context` — canonical package for symbols (`context/canonical.py`).
- `get_market_context` — market-level canonical overview (index, breadth).
- `get_source_matrix` — adapter matrix / capabilities.
- `build_conversation_package` — assemble the AI-safe conversation package.
- `get_source_health` — source-health + FinMind quota diagnostics.
- `run_bounded_observation` — bounded live-ish observation from **free** sources (`TWSE_MIS`).
- `get_disposition_stocks` — 處置股 list & per-stock disposition records (次數/條件/措施/期間)
  + attention (注意股) flags; buckets 風險股/處置中/即將出關/自結發布. Backed by TPEX OpenAPI +
  FinMind `TaiwanStockDispositionSecuritiesPeriod` (Backer) + derived escalation projection.
- `get_disposition_forecast` — derived "最快 N 日後進入處置 / 再次處置" projection for a stock.
- `get_material_announcements` — MOPS 重大訊息 stream for a stock/date (classified: contract/
  M&A/litigation/guidance/庫藏股/減增資/澄清), with extracted subject + sentiment.
- `get_board_resolutions` — MOPS 董事會決議 (減資/增資/私募/併購/CB/股利/庫藏股決議).
- `get_buyback_status` — 庫藏股 buyback plan + actual execution %.
- `get_insider_activity` — 內部人持股轉讓事前申報, 董監持股, 質押比 (risk flag).
- `get_investor_conferences` — 法說會 schedule + presentation links.
- `get_self_settled_earnings` — 自結損益 / 財務預測 (early earnings read).
- `get_stock_news` — news for a stock/topic (RSS + GDELT + Finnhub + FinMind history),
  NLP-classified with sentiment. **`category` param** partitions the feed:
  - **`realtime` 即時新聞** — the fast RSS stream (鉅亨/經濟日報/TechNews), time-ordered, minimal
    curation — "what just broke."
  - **`finance` 財經新聞** — curated financial coverage (FinMind history + Finnhub + GDELT),
    topic/sentiment-classified — "the considered read."
  - `all` (default) — merged, deduped, each item tagged with its `category` + `source` + `published`.
- `get_social_buzz` — PTT `Stock`/`Tech_Job` chatter for a symbol: post volume, 推文數, crowd
  sentiment — flagged **unverified retail opinion**.
- `get_trending_buzz` — off-topic PTT `Beauty`/`Gossiping` trend signal for the mascot/whimsy
  surface only; **excluded from investment rationale** by guardrails.
- `web_search` — general web search (query → results with source URLs). **Enablement-gated**:
  only available when the user toggles "web search" on for the turn (see below). Backed by the
  LLM's **native search** if the active provider supports it, else **Brave** (`web_search.py`);
  the agent doesn't care which — one tool, uniform result shape. Results must be **cited**.
- `fetch_url` — fetch & extract readable text from a specific page (for the agent to read a
  result). Size-capped per the output rules; caveated.
- `get_shareholder_dispersion` — 股東人數 + 千張大戶比率 trend, **derived** from FinMind
  `TaiwanStockHoldingSharesPer` (TDCC 股權分散); chip-concentration signal.
- `get_etf_beneficiaries` — ETF 受益人數 trend (TDCC/SITCA) — retail-inflow proxy.
- `get_etf_nav_premium` — ETF NAV / IOPV / 折溢價 (premium-discount vs fair value).
- `get_attention_stocks` — 注意股 (precursor to 處置) + 停資停券預告.
- `get_sector_indices` — 未含金融電子 / 產業分類 sub-indices (sector rotation).
- `get_etf_aum` — ETF 基金規模 (流通在外單位數 × NAV).
- `get_index_contribution` — 加權/櫃買 指數貢獻排行 (per-stock point contribution).
- `get_advance_decline` — 個股漲跌分佈 (>5%/3–5%/…) + 漲跌家數 (market breadth).
- `get_company_profile` — composed 個股資訊卡: 產業分類/成立年份/董事長/總部/官網/資本額/
  發言人/簽證會計師 (MOPS `t05st03` + TWSE CSV + `TaiwanStockInfo`) **+** financial snapshot
  (市值, 本益比, 股價淨值比, 季營收+YoY, 毛利/營益/淨利率, EPS, 殖利率, 股利). Assembled across
  MOPS + FinMind (`TaiwanStockMarketValue`/`PER`/`MonthRevenue`/`FinancialStatements`/`Dividend`).
- `get_company_relationships` — 集團企業/轉投資/從屬公司 + 大股東(>10%) (MOPS scrape).
- `get_analyst_consensus` — Yahoo-aggregated **目標價/評等/升降評 + earnings/revenue consensus**
  (yfinance; large-cap `.TW`/`.TWO`). Returns `null`/`unavailable` when `numberOfAnalystOpinions
  == 0`. Output is **attributed third-party estimate** ("N analysts, as of <date>"), never our
  recommendation — the guardrail blocks restating a target/rating as our own view.
- `get_esg_score` — ESG risk score (yfinance `sustainability`, large-cap; `null` otherwise).
- `get_calendar_events` — forward events for a stock/ETF/watchlist/market over a date range,
  by type + state (`scheduled|expected|confirmed|released`) — see [18](18-calendar-events.md).
- `get_dividend_schedule` — per-stock/ETF 除權息 + 配息 timeline (除息日/基準日/發放日).
- `get_etfs_holding_stock` — **reverse view**: for a stock, all ETFs holding it with 持倉比重
  (+增減) and 持股張數(+增減), filterable by ETF type (市值型/科技型/主動型/配息型/債券型) & date.
  Active side from FinMind `TaiwanStockActiveETFHolding` (sponsor); passive side from issuer
  PCF (`public`). Reads the normalized `etf_holdings(etf_id, stock_id, weight, shares, date)`.

### `sponsor` — Premium/Pro only (masked teaser otherwise)
- `get_chip_flows` — 分點券商進出 (FinMind Sponsor).
- `get_government_bank_flows` — 八大官股 (Sponsor).
- `get_active_etf_report` — 主動式ETF **操作日報** (forward view of one ETF): per-holding
  **狀態 新增/刪除/加碼/減碼** (a *derived classification* of the張數 delta vs prev-day partition),
  變動張數, 變動幅度, 目前權重 + 權重變動%, plus 基金規模較前日 & 折溢價 header. All Δ/較前日 fields are
  **diffed-on-write** (§22.4), not stored twice. Reverse view = `get_etfs_holding_stock` (Sponsor).
- `get_etf_money_flow` — cross-ETF 資金流向: 主動式檔數/持有市值 per stock, 流入/流出排行,
  多空共識 (買賣檔數) — aggregated over active-ETF holdings (Sponsor).
- `get_industry_money_flow` — 產業鏈資金流 (Sponsor).
- `get_intraday_kbars` — tick / minute-K (Sponsor).
- `get_realtime_snapshot` — real-time snapshots (Sponsor).
- `build_live_context` — `TAIFEX_MIS` + real-time snapshot context (Sponsor where it uses paid data).

### `public` — derivatives, macro & chip/price datasets (previously catalogued, no tool — now defined)
These wrap FinMind/gov datasets that the data-catalog lists but had no tool. All `public` (free-tier
sourced), non-trading. Backs the web's derivatives/macro/當沖/借券 surfaces (see [data-catalog](../data-catalog.md)).
- `get_futures_options_daily` — 期貨/選擇權 日成交 + settlement (catalog §2.4).
- `get_derivatives_institutional` — 期/選 三大法人 + 大額交易人 OI (§2.4).
- `get_put_call_ratio` — 選擇權 P/C ratio (§2.4).
- `get_taiex_vix` — 臺指選擇權波動率指數 VIX (§2.4).
- `get_day_trading` — 當沖標的/量值/當沖比重 (`TaiwanStockDayTrading`, §2.1).
- `get_securities_lending` — 借券成交/賣出趨勢 (`TaiwanStockSecuritiesLending`, §2.2).
- `get_block_trade` — 鉅額交易 (`TaiwanStockBlockTrade`, §2.2).
- `get_margin_maintenance` — 大盤融資維持率 (`TaiwanTotalExchangeMarginMaintenance`, §2.2) — market-leverage risk.
- `get_total_return_index` — 報酬指數 (`TaiwanStockTotalReturnIndex`, §2.1).
- `get_macro_indicators` — 匯率/利率/債殖利率/黃金/原油 + Fear-Greed (`/macro/*` family, §2.9).
- `get_business_indicator` — 景氣對策信號 藍黃紅燈 (`TaiwanBusinessIndicator`, §2.9).
- `get_convertible_bonds` — 可轉債 daily + 法人 (catalog §2.9; niche).

> **Governance note:** derivatives (OI/PC-ratio/VIX) are the most signal-adjacent — surfaced as
> **descriptive positioning context**, never a directional call (guardrail §7). Multi-TF price
> (`get_price_multi_tf`: 週/月/10Y from `Week/Month/Stock10Year`) and `get_price_limit` (漲跌停價)
> also added here (§2.1).

### `public`/AI — derived analytics & scoring ([20](20-analytics-scoring.md))
Derived compute over the ingest store (no network I/O). `public` unless a factor is
`sponsor`-fed (then that factor masks); AI-generated ones are Premium+ (tier axis 2, not a data class).
- `get_indicators` — MA/KD/MACD/RSI/Bollinger/ATR/OBV/VWAP + K-line patterns + `set=volume,sr,wave`
  (量能, 支撐/壓力, N 波回檔); latest values + series (public).
- `get_score` — lean 5-factor 多空 composite (momentum/RS · trend · value · quality · 籌碼-flow),
  **horizon-aware** (`{profile: swing|position}`) → **偏多/中性/偏空** band + regime caveat (public;
  分點 flow sub-signal masks if unentitled).
- `get_score_breakdown` — per-factor **percentile + contribution + 描述** (transparency requirement §7).
- `get_ai_summary` — AI 智能摘要 + 市場定位 card, LLM-summarized from the deterministic factors (Premium+).
- `get_swot` — factor-grounded SWOT, each bullet tied to a cited datum (Premium+).
- `run_screener` — the **funnel**: hard **gates** (liquidity/price/處置-注意-全額交割/…) → **setup** via a
  named **preset** (Minervini / CANSLIM / Weinstein Stage-2 / Piotroski F≥7 / Magic Formula / TW
  favorites), a single `screen_type`, **or a custom AND/OR `conditions[]` list** from the builder
  (`{market, preset|screen_type|conditions[], match, trade_date?, gate_overrides?, count_only?}`).
  `op ∈ {gt,gte,lt,lte,eq,between,in}`; `count_only` returns just the survivor count (live-count probe).
  `market` = TW (full) / ETF `(TW,etf)` / US (yfinance, no 籌碼, phased → `not_supported`). Public/sponsor per criteria.
- `get_factor_catalog` — bilingual catalog (技術/基本面/動能/籌碼 for TW; 估值/成長/動能/分析師 for US;
  折溢價/規模/持股/動能 for ETF) **+ per-market `fields[]` descriptors** (unit/min/max/step/enum/preset-chips)
  the condition builder needs; takes `{market}`, factors gated by adapter `supports()` (module 19 §3).
- `get_screener_presets` — saved + named-framework presets.
- `save_screen` / `list_screens` / `delete_screen` — per-user named screen CRUD (`{name, market,
  conditions, match, gate_overrides}`); backs saved/shared screens (`/analytics/screener/screens`, [20](20-analytics-scoring.md) §5B).
- `get_top_stocks` — **AI 篩選前 N 名**: gates → optional setup → rank survivors by composite or a
  single factor (`{profile, sort, n, screen?}`), each with its factor breakdown + regime/over-extension caveat.
- `get_theme_flow` — 三大法人 net flow per discovered theme (資金流向).
- `get_themes` — LLM-discovered emerging themes + member stocks + confidence (Premium+).
- `get_figure_events` — named-figure news events (黃仁勳/馬斯克…) scanned from ingested news.

**Not in this registry:** the **pet/gamification** system ([21](21-pet-gamification.md)) is a
cosmetic companion with its own `/pets/*` endpoints — it is **not** an agent tool, grants no
data/analysis, and never enters the tool-dispatch or entitlement path.

Tool names are stable public identifiers (used by the LLM and over MCP); the domain
filenames they live in are internal.

## Deferred / lazy tool loading

With ~50 tools, sending every schema every turn wastes context. Following Pi's
`splitDeferredTools()`, the registry splits tools into:
- **immediate** — **the canonical core set** whose full schemas are always sent (defined **here**,
  the single source of truth; other docs reference this list, never re-enumerate it):
  `get_canonical_context`, `get_market_context`, `get_company_profile`, `build_conversation_package`,
  `get_source_health` (+ the basic `get_stock_news` / price-quote read). The MCP projection
  ([06-interfaces.md](06-interfaces.md)) exposes the same core minus the agent-loop-only
  `get_company_profile`/news-price (those load via `execute_tool` for MCP clients) — a *deliberate*
  projection difference, not a divergent core.
- **deferred** — the long tail (per-signal chip/ETF/derivatives/MOPS tools); exposed to the
  model only as a compact **index** (name + one-line description). When the model needs one,
  it's loaded on demand (full schema injected next turn), mirroring progressive skill loading
  ([08-skills.md](08-skills.md)). A skill's `tools` allowlist pre-loads exactly its deferred set.

## Web search — a capability the agent uses (tool), not a pipeline

Web search is exposed to the agent as **tools** — `web_search` and `fetch_url` — in this same
registry, invoked in the loop like any other tool (not a hardcoded pre/post step). The agent
decides when to search, subject to the per-turn enablement toggle below.

`web_search`/`fetch_url` are **off by default** and turned on per-turn via the chat's **web-
search toggle** (`POST /agent/query {web_search: true}` — [api-reference](../api-reference.md)).
When off, the tools aren't in the registry view the model sees, so it can't call them.

When on, the runtime picks the backend transparently:
- **LLM-native search** — if the active provider exposes it (Anthropic `web_search` tool,
  OpenAI/Gemini grounding), prefer it (the model searches + grounds in one call).
- **Brave Search API** — the provider-agnostic fallback (and the only option for local Ollama
  / providers without native search). Configured via `web_search.py` + `BRAVE_API_KEY`.
- Selectable via config (`AITW_WEBSEARCH = auto | brave | native | off`); `auto` = native if
  available else Brave.

Governance: web results are external/unverified → every claim drawn from the web is **cited
with its source URL**, caveated, and **non-trading**; results feed the same guardrails and
NL-`activity` narration ("Searching the web for 台積電 CoWoS capacity news"). `web_search` is
`public` data-class but **entitlement-gated for depth** (see [12](12-multi-user-tiers.md): basic
search available, `deep_research` multi-step is Pro).

## Tool output size control

Market tools can return huge payloads (whole-market holdings, tick, 分點). Each tool caps
output and, when it would overflow, returns a **compacted result**, never the raw dump:
- truncate to a head/summary; **persist the full result to a file/artifact** and return its
  path so the agent can re-read a slice on demand;
- return a structured summary + key rows/figures (+ line/row anchors) instead of everything;
- always caveated; raw source payloads are never surfaced verbatim (governance §6).

## Enforcement

- A tool runs freely; if its `data_class == sponsor` and the caller lacks the entitlement,
  the `accounts/entitlements` check returns a **masked teaser** (`{locked, required_tier,
  preview}`) instead of full data — the frontend renders it blurred/馬賽克 with an upgrade
  CTA. This check runs in the `pre_tool` hook ([10-events-hooks.md](10-events-hooks.md)).
- No tool requires approval or a confirmation token to execute.
- Tools are the only callers of `context/`, which are the only callers of `sources/`.
- Every dispatch (args + result summary + data_class) is written to the journal.

## MCP exposure

**All** tools are exposed over MCP (all read-only). `sponsor` tools return the masked teaser
unless the MCP principal is entitled — same rule as the API, no special-casing.
