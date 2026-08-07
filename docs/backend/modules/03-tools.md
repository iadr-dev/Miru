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
    card_type: str | None          # opts this tool's output into a `rich_card` SSE event
    def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel: ...
```

`ToolContext` carries the `user_id`/entitlement, the active TLS policy, and the journal
handle. There is no confirmation token.

**`card_type`** (default `None` — no behavior change for a tool that hasn't opted in) is a
deliberate, per-tool opt-in into the `rich_card` SSE event ([07-conversation-streaming.md](07-conversation-streaming.md)):
a chart/table/gauge-shaped tool result the frontend renders as a structured card rather than
plain prose. Chosen over inferring card-worthiness from a tool's `output_model` shape, since
shape alone (e.g. field count) can't reliably distinguish a genuine table from an incidentally
wide scalar result. **69 tools are opted in today** — the price/chip/flow/valuation/derivatives/
fundamental/ETF/macro/theme/score/screener reads whose typed output is genuinely tabular,
time-series, or gauge-shaped (the exact name→label mapping is locked by a parametrized test in
`tests/agent/test_loop.py`).
What stays `card_type=None` is shaped, not arbitrary: reference/roster/lookup tools (rosters,
symbol search/lookup, trading calendar, trader info, industry-chain classification), health/
diagnostics (`get_source_health`, `get_source_matrix`), event/notice streams (material
announcements, corporate actions, dispositions, suspensions, halts, calendar-events feed),
text-first tools (news, `web_search`, `fetch_url`), context-assembly tools (`get_canonical_context`,
`build_conversation_package`, `run_bounded_observation`), and low-value scalar reads
(settlement price, market status) — none of these render better as a card than as prose or a
citation. A card is built strictly after
`dispatch_tool`'s entitlement gate returns — a masked/unentitled `sponsor` result never reaches
card-building, so an unentitled caller gets no card (not a card with real data), matching the
masked-teaser discipline every other `sponsor` surface follows. The card's structured payload is
scanned for trading language and stamped with the standard non-trading caveat, the structured
analogue of `guardrails.check_output`.

## Registered tools (grouped)

**Real count: 97 tools**, registered from 22 domain files under `tools/` (`context_tools.py`,
`health_tools.py`, `market_tools.py`, `mops_tools.py`, `calendar_tools.py`, `live_tools.py`,
`gov_api_tools.py`, `etf_pcf_tools.py`, `etf_overlap_tools.py`, `web_search_tools.py`,
`analyst_tools.py`, `chip_tools.py`, `technical_tools.py`, `fundamental_tools.py`,
`derivatives_tools.py`, `convertible_bond_tools.py`, `macro_tools.py`, `international_tools.py`,
`others_tools.py`, `theme_tools.py`, `score_tools.py`, `screener_tools.py`) and assembled by
`tools/__init__.py::build_default_tool_registry` into one `ToolRegistry`
(`tools/registry.py`). The list below is a domain-grouped index of the real tool names;
per-tool detail (input/output shape, capability wrapped) lives in each file's own docstrings.

Naming vs. the original design: several tools this design named turned out, once built against
real adapters, to need a different name (usually because the real capability is narrower or
bundles several FinMind datasets into one report-shaped tool per this registry's bundling
convention) or don't exist yet at all. Corrections are called out inline below.

### `public` — free for everyone (incl. anonymous & MCP)
- `get_canonical_context` — canonical package for symbols (`context/canonical.py`).
- `get_source_matrix` — adapter matrix / capabilities.
- `build_conversation_package` — assemble the AI-safe conversation package.
- `get_source_health` — source-health + FinMind quota diagnostics.
- `run_bounded_observation` — capability-agnostic bounded live-ish observation from any router
  capability (generic redaction shape). *No real tool named `get_market_context` exists* — there
  is no market-level canonical-overview tool, only per-symbol `get_canonical_context`.
- `get_live_quote_snapshot` — a named, purpose-built bounded TWSE MIS best-5 bid/ask + last-trade
  snapshot for one TW symbol (free source; complements `run_bounded_observation`'s generic shape).
- `get_material_announcements` — MOPS 重大訊息 stream for a stock (`Capability.CORPORATE_ACTIONS`;
  current-day dump only, no date-range param upstream).
- `get_insider_shareholding` — MOPS 董監事持股餘額 (`Capability.SHAREHOLDING`): per-person
  current/at-election shares + 質押比 (risk flag). Design named a broader `get_insider_activity`/
  `get_board_resolutions`/`get_buyback_status`/`get_investor_conferences`/
  `get_self_settled_earnings` set — the real tool is narrower, scoped to exactly what
  `sources/mops.py` serves; those other five have **no adapter and no tool** (no matching
  `Capability` exists in `schemas/enums.py` — a data-catalog gap, not a tool-registry one).
- `get_insider_activity` — US-style Form-4-derived insider transactions (non-TW only,
  `NonTwSymbolInput`), sparse/empty outside the US — a distinct, real tool from the MOPS one above.
- `get_institutional_holders` — non-TW institutional-holder rows.
- `get_fund_holdings` — non-TW ETF/mutual-fund top holdings + sector weightings.
- `get_options_chain` — non-TW options chain.
- `get_symbol_search` / `get_symbol_lookup` — yfinance free-text search / scoped lookup.
- `get_calendar_events_feed` — bundles yfinance earnings + IPO + economic calendar feeds
  (non-TW). Design named this `get_calendar_events` — that name now belongs to a distinct,
  differently-shaped tool (`tools/calendar_tools.py`; TW-first, feed composition over
  `calendar/events.py`; see [18-calendar-events.md](18-calendar-events.md)) that composes real
  除權息/ETF配息 + CB 賣回權/到期 events, classed `sponsor` as a whole (its CB slice is
  Sponsor-tier); this tool remains the separate, narrower yfinance-native non-TW feed bundle.
- `get_sector_overview` / `get_industry_overview` — yfinance sector/industry overview (non-TW).
  *Design's `get_sector_indices` (TW 未含金融電子/產業分類 sub-indices) has no real tool.*
- `get_market_status` — yfinance market open/closed status (non-TW).
- `run_equity_screener_us` — yfinance-backed US/GB/JP/EU equity screener. Deliberately **not**
  named `run_screener` — that generic name is reserved for the design's separate,
  not-yet-built `analytics/`-owned gates→setup→rank funnel (see [20](20-analytics-scoring.md)).
- `get_margin_short` / `get_margin_short_total_market` — 融資融券 per-stock / whole-market.
- `get_institutional_flow_wide` / `get_institutional_flow_total_market` — wide-format /
  whole-market 三大法人.
- `get_foreign_shareholding` — 外資持股比率.
- `get_securities_lending` — 借券成交/賣出趨勢.
- `get_margin_short_sale_suspension` — 融資融券/借券 suspension notices.
- `get_daily_short_sale_balances` — 融券餘額.
- `get_securities_trader_info` — 券商分點基本資料.
- `get_futopt_roster` — whole-market futures/options contract-code reference roster.
- `get_futures_options_daily` — 期貨/選擇權日成交 + settlement.
- `get_put_call_ratio` — raw call/put daily volume rows (the P/C-ratio arithmetic itself is left
  to the caller — no separate ratio dataset exists upstream).
- `get_dealer_volume` — 期貨/選擇權 per-dealer trading volume.
- `get_derivatives_settlement` — TAIFEX OpenAPI 最後結算 (not a FinMind-covered capability).
- `get_financial_statement` / `get_dividend_result` / `get_month_revenue` — FinMind fundamentals.
- `get_corporate_action_events` — 減資/下市/合股/減併股/面額變動.
- `get_stock_roster` — whole-market stock+warrant info roster.
- `get_trading_calendar` — whole-market trading-day calendar.
- `get_valuation_ratios` — daily PER/PBR/dividend-yield.
- `get_order_book_stats` — whole-exchange 5-second order-book stats.
- `get_taiex_index_snapshot` — whole-market TAIEX 5-second index reading. *Design's
  `get_advance_decline`/`get_index_contribution` (漲跌分佈/家數, 指數貢獻排行) have no real tool —
  this snapshot is the index level only, not a breadth/contribution breakdown.*
- `get_day_trading` — 當沖標的/量值/當沖比重.
- `get_total_return_index` — 報酬指數.
- `get_price_limit` — 漲跌停價.
- `get_international_stock` — FinMind's own US/GB/EU/JP roster+price bundle (non-yfinance path).
- `get_active_etf_roster` — free-tier whole-market active-ETF roster.
- `get_finmind_stock_news` — FinMind's own news dataset (distinct from the RSS/GDELT/Finnhub
  aggregator the design also names). Design's `get_stock_news` (multi-source aggregator with a
  `category` param) **has no real tool** — `sources/news.py`/`sources/ptt.py` were explicitly
  excluded from tool-wrapping this pass (confirmed in `others_tools.py`'s own docstring: "no
  `news` aggregator tool exists yet per the explicit scope decision to skip
  `sources/news.py`/`sources/ptt.py` entirely"). `get_social_buzz`/`get_trending_buzz` (PTT)
  likewise have **no real tool** for the same reason.
- `get_etf_fund_info` — TW ETF fund-level metadata (issuer, tracked index, units outstanding) —
  not per-constituent holdings or NAV/IOPV (documented gap in the adapter itself). *Design's
  `get_etf_nav_premium`/`get_etf_aum`/`get_etf_beneficiaries` have no real tool.*
- `get_etf_overlap` (`etf_overlap_tools.py`) — the Reddit-r/ETFs-sourced 3+-fund overlap-checker:
  fans `Capability.FUND_HOLDINGS` out over 3–10 caller-named **non-TW** ETFs/funds (same
  `US`/`GB`/`JP`/`EU` restriction as `get_fund_holdings`) and computes which requested ETFs hold
  each shared holding (2+ of them, not just all) plus a per-pair overlap percentage (`sum(min
  (weight_in_a, weight_in_b))` over shared holdings). A symbol whose holdings fetch degrades is
  named in `unavailable_symbols`, never silently dropped. TW passive ETFs (0050/00713/00878/...)
  are deliberately **out of scope**: `sources/etf_pcf.py`'s own module docstring discloses that
  per-constituent holdings/weights for TW ETFs are a genuine, unfilled adapter gap.
- `get_fund_comparison` (`etf_overlap_tools.py`) — 2–10-symbol total-return comparison, **any
  market including TW** (unlike the overlap-checker, this only needs `Capability.PRICE_EOD` +
  `Capability.DIVIDENDS`, both real for TW): total return over a lookback window with each cash
  dividend treated as reinvested at that ex-dividend date's closing price (a stated
  approximation, not an account-level reinvestment simulation), plus a basic-profile slice
  (non-TW: `FUND_HOLDINGS` sector weightings; TW: `get_etf_fund_info`'s issuer/tracked-index
  metadata). Never computes overlap%/premium-discount/NAV for a TW symbol — that row's
  `unavailable_note` names the same TW-holdings-gap reason as `get_etf_overlap`, rather than
  omitting the field silently or fabricating a number.
- `get_company_profile` — see immediate-core note below; a **partial** profile (sector/industry +
  latest dividend only — no MOPS adapter exists, so 董事長/總部/資本額/市值/PE/PB/EPS/… are named
  in its own `missing_fields`, never fabricated). *Design's `get_company_relationships` (集團
  企業/轉投資/大股東) has no real tool.*
- `get_price_quote` — see immediate-core note below.
- `get_dividend_schedule` — per-stock/ETF 除權息 + 配息 timeline.
- `get_institutional_flow` — 三大法人 per-stock (public slice; `get_chip_flows`/
  `get_government_bank_flows` below are the Sponsor-gated finer cuts).
- `web_search` — general web search (query → results with source URLs). **Enablement-gated**:
  only available when the user toggles "web search" on for the turn (see below). Backed by the
  LLM's **native search** if the active provider supports it, else **Brave** (`web_search.py`);
  the agent doesn't care which — one tool, uniform result shape. Results must be **cited**.
- `fetch_url` — fetch & extract readable text from a specific page (for the agent to read a
  result). Size-capped per the output rules; caveated.
- `get_theme_members` — the member symbols of one theme (industry-chain or concept), each with
  its sub-industry, confidence, and source (industry-chain fact vs AI-researched). Reads the
  relational theme store directly (not the router), so it is wired with the DB session factory
  rather than a capability adapter. Unknown key → `found=False`, never an error.
- `get_symbol_themes` — the reverse lookup: every theme a symbol belongs to, each membership
  carrying its own sub-industry/confidence. Answers "what themes is 2330 in".
- `get_theme_tiers` — one theme's sub-industries grouped upstream/midstream/downstream, each
  assignment marked curated (editorial) or heuristic (auto-classified) so a guess never reads as
  authoritative as a verified position. These three carry no scoring/directional framing, so they
  are `public` — the same tier at which `get_company_profile` returns sector/industry.
- `get_stock_score` — the stock-detail-page read: one symbol's cached multi-factor composite
  (momentum/trend_quality/value_guard/quality/revenue_flow) + 偏多/中性/偏空 band, for the `swing`
  (default) or `position` horizon profile. **Cache-only, never live-compute** — reads the
  `stock_scores` row the daily `analytics/scoring_batch.py` batch wrote; returns `found=false`
  with a plain-language reason if today's batch hasn't landed yet for this symbol, rather than
  falling back to an expensive (and non-comparable, since every factor is cross-sectional)
  per-symbol live computation. Reads the relational score cache directly (like the theme tools),
  so it is wired with the DB session factory. `public` — every input factor (price, chip flow,
  financial statements, market value) is already `public` elsewhere in this registry.
- `get_score_leaderboard` — the whole-TW-universe leaderboard: top-N (max 50) strongest
  (`direction=bullish`) or weakest (`bearish`) cached composite scores for a horizon profile — a
  sort-and-slice read of the same nightly batch's cache, never a live rescan. `public`.
  (Both in `tools/score_tools.py`; see [20-analytics-scoring.md](20-analytics-scoring.md) §2c.)
- `run_tag_screener` — the tap-tile TW tag screener: match symbols against named end-of-day tags
  (e.g. 外資連3買, 站上20日均線) combined AND/OR, each hit carrying the tags it satisfied. Reads the
  nightly-computed `screener_flags` Parquet dataset through the derived-store reader (not the
  router, not a live evaluation), so it is wired with that reader rather than a capability adapter.
  `public` — the flags are derived descriptive classification with no scoring, and even the chip
  tags rest only on the public three-major-institutional-investor dataset. A filtered list matching
  described criteria, never trading advice.
- `get_tag_catalog` — the discoverable list of screener tags, grouped by category
  (技術面/籌碼面/熱門/智能) and bilingual, so an agent/frontend knows what `run_tag_screener`
  accepts. `public`. (Both live in `screener_tools.py`; see
  [20-analytics-scoring.md](20-analytics-scoring.md) §5T for the staged build plan — the catalog
  now also carries KD-cross and the unambiguous candlestick-pattern tags, plus a 熱門 category
  (volume-surge, 52-week high/low) and a 智能 category (pre-bundled AND-combinations of existing
  tags, not a new composite score); the tools themselves are unchanged, enumerating the catalog
  dynamically. Fundamental-gate and theme tags remain later dispatches.)

**Confirmed not built (no matching `Capability`/adapter at all, not just no tool):**
`get_disposition_stocks`/`get_disposition_forecast`/`get_attention_stocks` (only the narrower,
Sponsor-gated `get_disposition_securities` below exists — no TPEX 注意股 adapter),
`get_esg_score` (the `esg` capability exists in yfinance and is wired for context-building, but
**no tool wraps it**), `get_analyst_consensus` (same: `analyst_consensus` capability exists and
feeds `context/builder.py`'s default context, but has no dedicated tool).

### `sponsor` — Premium/Pro only (masked teaser otherwise)
- `get_chip_flows` — 分點券商進出 (FinMind Sponsor).
- `get_government_bank_flows` — 八大官股 (Sponsor).
- `get_active_etf_report` — 主動式ETF **操作日報** (forward view of one ETF): per-holding
  **狀態 新增/刪除/加碼/減碼** (a *derived classification* of the張數 delta vs prev-day partition),
  變動張數, 變動幅度, 目前權重 + 權重變動%, plus 基金規模較前日 & 折溢價 header. All Δ/較前日 fields are
  **diffed-on-write** (§22.4), not stored twice.
- `get_etf_money_flow` — cross-ETF 資金流向: 主動式檔數/持有市值 per stock, 流入/流出排行,
  多空共識 (買賣檔數) — aggregated over active-ETF holdings (Sponsor). Design's reverse-view
  `get_etfs_holding_stock` has no real tool.
- `get_industry_money_flow` / `get_industry_chain_classification` — 產業鏈資金流 / 分類 (Sponsor).
- `get_shareholder_dispersion` — 股東人數/千張大戶比率 (股權分散) concentration signal (Sponsor;
  the original design documented this `public` — the real `TaiwanStockHoldingSharesPer` dataset
  is FinMind Sponsor-tier, so the tool mirrors that).
- `get_margin_maintenance` — 大盤融資維持率 (Sponsor; same public→sponsor correction as above).
- `get_chip_broker_agg` — 分點進出彙總.
- `get_block_trade_broker_report` / `get_block_trade` — 鉅額交易 broker-level / plain.
- `get_loan_collateral_balance` — 資金貸與/質押 collateral balance (**note:** this is a balance
  figure, not the 質押比/背書保證/訴訟裁罰 governance-risk composite the design's
  `governance-risk` skill wants — see [08-skills.md](08-skills.md)).
- `get_disposition_securities` — 公布處置有價證券 event stream (narrower than the design's
  `get_disposition_stocks` composite — no TPEX 注意股/停資停券 adapter exists to fold in).
- `get_derivatives_institutional` — 期/選 三大法人 day-session flow + 大額交易人 OI.
- `get_taiex_vix` — 臺指選擇權波動率指數 VIX.
- `get_futures_spread_trading` — 期貨價差 quote.
- `get_final_settlement_price` — 期貨/選擇權最後結算價.
- `get_asset_swap_daily` — 資產交換 daily trading (bond + option).
- `get_convertible_bond_profile` / `get_convertible_bond_flow` — bundled CB roster/overview/put
  schedule and CB trading/institutional-flow/custody, respectively (six FinMind CB datasets
  bundled into these two report-shaped tools per this registry's bundling convention). Design
  called this `get_convertible_bonds` (singular) — the real tools split "what is this bond" from
  "how is it trading."
- `get_calendar_events` — feed-sourced forward events (除權息/ETF配息 via `Capability.DIVIDENDS`;
  CB 賣回權/到期 via `Capability.CB_PUT_PROVISION`) for one stock/ETF/CB symbol over a bounded
  date range (`tools/calendar_tools.py`, composing `calendar/events.py`; see
  [18-calendar-events.md](18-calendar-events.md)). Classed `sponsor` as a whole per this
  registry's mixed-tier bundling convention (its CB slice is Sponsor-tier even though dividend
  data is `public`). Does not include Layer-1 rule markers (財報/月營收 deadlines, derivatives
  settlement) or AGM/法說會 (no structured MOPS feed exists for those two — see that doc's own
  "Implementation status" section for why).
- `get_macro_indicators` — 匯率/利率/債殖利率/黃金/原油 + Fear-Greed (Sponsor in the real build;
  the original design documented this `public`).
- `get_business_indicator` — 景氣對策信號 藍黃紅燈 (Sponsor in the real build; same correction).
- `get_market_value` — daily market-capitalization rows (Sponsor; no free per-symbol tier
  upstream).
- `get_market_value_weight` — daily whole-market market-cap-weight ranking (Sponsor).
- `get_us_stock_price_minute` — US-only minute-level price (Sponsor).
- `get_warrant_underlying` — 權證-to-underlying mapping.
- `get_price_tick` — tick-level trade history.
- `get_ten_year_price` — 10-year price history.
- `get_intraday_kbar` — tick / minute-K.
- `get_price_multi_tf` — 週/月/10Y price (`Week`/`Month`/`Stock10Year`).
- `get_index_snapshots` — 5-second index snapshot (non-TAIEX indices).
- `get_trading_halts` — 暫停交易 announcement stream (`DAY_TRADING_SUSPENSION` 先賣後買當沖
  suspension is a documented, deliberate niche skip — same shape, narrower audience).

**Deliberately left unwrapped this pass** (per each tool file's own docstring, not a silent
drop): FinMind tick-level `futures_tick`/`futures_spread_tick`/`option_tick` and the two
after-hours institutional-flow variants (`derivatives_tools.py`); `DAY_TRADING_SUSPENSION`
(`technical_tools.py`); a second TWSE/TPEX-specific EOD-price tool (`gov_api_tools.py` — already
reached transparently through `get_price_quote`'s router fallback, so a dedicated tool would be
a redundant view).

**No real tool at all (Premium-tier design intent, not yet an adapter or a tool):**
`get_realtime_snapshot`, `build_live_context` (`TAIFEX_MIS` context is staged behind
`enable_taifex_mis_validation` in `context/builder.py`, not exposed as a tool).

### `public`/AI — derived analytics & scoring ([20](20-analytics-scoring.md))
**None of these exist as real tools.** `analytics/` today is three flat modules
(`indicators.py`, `score.py`, `models.py`) with no `scoring/`, `screener/`, `summary/`, or
`themes/` subpackages — module 20's own implementation-status note is authoritative here; this
doc does not re-litigate it. The design intent (kept for forward reference only): `get_indicators`,
`get_score`, `get_score_breakdown`, `get_ai_summary`, `get_swot`, `run_screener`,
`get_factor_catalog`, `get_screener_presets`, `save_screen`/`list_screens`/`delete_screen`,
`get_top_stocks`, `get_theme_flow`, `get_themes`, `get_figure_events` — all **not built**, tracked
against [20](20-analytics-scoring.md), not this registry.

**Not in this registry:** the **pet/gamification** system ([21](21-pet-gamification.md)) is a
cosmetic companion with its own `/pets/*` endpoints — it is **not** an agent tool, grants no
data/analysis, and never enters the tool-dispatch or entitlement path.

Tool names are stable public identifiers (used by the LLM and over MCP); the domain
filenames they live in are internal.

## Deferred / lazy tool loading

With **97 tools**, sending every schema every turn wastes context. Following Pi's
`splitDeferredTools()`, the registry splits tools into:
- **immediate** — **the canonical core set** whose full schemas are always sent (defined **here**,
  the single source of truth; other docs reference this list, never re-enumerate it). The real,
  wired-up core (`tools/registry.py`'s `immediate=True` registrations) is exactly:
  `get_canonical_context`, `build_conversation_package` (`context_tools.py`), `get_source_health`
  (`health_tools.py`), `get_company_profile`, `get_price_quote` (`market_tools.py`) — **5 tools**.
  This is narrower than the design's originally-named 6-tool core: `get_market_context` has no
  real tool at all (see above), and there is no separate immediate news tool; `get_price_quote`
  is the basic price-quote read the design meant. **The MCP projection exposes the exact same
  5-tool immediate core, not a subset** — `mcp/app.py::build_mcp_server` registers a dedicated
  MCP tool for every name in `registry.immediate_tools()`, verified end-to-end against the real
  97-tool registry in `tests/mcp/test_app_real_registry.py`
  (`test_real_immediate_tools_are_registered_as_dedicated_mcp_tools` asserts all 5 names,
  including `get_company_profile`/`get_price_quote`, appear as dedicated MCP tools).
- **deferred** — the long tail (92 tools: per-signal chip/ETF/derivatives/MOPS/analyst/
  technical/macro/international tools, the ETF overlap-checker/comparison pair, the three
  relational theme-lookup tools, the stock-score pair, the tag-screener pair, and the calendar-
  events tool); exposed to
  the model only as a compact **index** (name +
  one-line description). When the model needs one, it's loaded on demand (full schema injected
  next turn), mirroring progressive skill loading ([08-skills.md](08-skills.md)). A skill's
  `tools` allowlist pre-loads exactly its deferred set.

## Web search — a capability the agent uses (tool), not a pipeline

Web search is exposed to the agent as **tools** — `web_search` and `fetch_url` — registered
here (`tools/web_search_tools.py`), deferred like any other long-tail tool, invoked in the loop
like any other tool call. `web_search` is `public` data-class.

**Real implementation vs. design intent — several claims below do not hold today:**
- **No per-turn enablement toggle exists.** `POST /agent/query` (`AgentQueryRequest` in
  `api/routers/agent.py`) takes only `message`/`thread_id` — there is no `web_search: true`
  field, and nothing in `tools/`, `agent/`, or `orchestrator/` conditionally hides these two
  tools for a turn. Both are simply deferred long-tail tools, reachable exactly like any other
  deferred tool once a skill's allowlist includes them or `execute_tool` names them.
- **No backend-selection logic exists.** The only implemented search backend is
  **Brave Search API** (`sources/web_search.py::WebSearchAdapter`, gated on
  `BRAVE_SEARCH_API_KEY`). `llm.base.ModelCapabilities.native_web_search` is declared on each
  provider client (`True` for Anthropic/Gemini, `False` for OpenAI/local — reflecting whether
  the *provider's own SDK* exposes native grounding) but **nothing reads this field** — there is
  no `AITW_WEBSEARCH` config key, no `auto | brave | native | off` selection, and no code path
  that ever routes a search through the LLM's native tool instead of Brave. `fetch_url` is a
  second, plain `httpx` GET behind the same adapter (no separate config).
- **No `deep_research` tool/mode is built.** `accounts/entitlements.py::FEATURE_MIN_TIER`
  reserves the string `"deep_research"` at `Tier.PRO` for a future feature gate, but no tool,
  skill, or endpoint implements a "multi-step deep research" mode — `feature_allowed(user,
  "deep_research")` exists as a predicate with nothing behind it yet.

What genuinely works: both tools run through `WebSearchGateway` (`dataaccess/web_search.py`) →
`WebSearchAdapter`, size-cap `fetch_url`'s extracted text at 20,000 characters (truncated flag
on overflow), and return a `currentness_caveat` on every result. Governance still applies as
designed: web results are external/unverified, so every claim drawn from the web must be
**cited with its source URL** and is subject to the same non-trading `guardrails.check_output`
scan as any other output.

## Tool output size control

**Design intent, not built.** No tool in `tools/` truncates a large result, persists it to a
file/artifact for later re-reading, or attaches row/line anchors — this is aspirational, not a
real mechanism. Every row-returning tool (via the shared `tools/_rows.py::fetch_rows`/
`validate_rows` helper) returns its full validated row tuple as-is; the only real size control
in the registry today is `fetch_url`'s flat 20,000-character text cap
(`tools/web_search_tools.py::_FETCH_URL_TEXT_CAP`, with a `truncated` flag on overflow) — there
is no equivalent for any market-data tool's row payload. What genuinely is enforced: every
`SourceResult`-backed tool still carries its `currentness_caveat`, and raw upstream payloads
are never surfaced verbatim (each tool validates rows into its own typed `schemas.capabilities`
model — see governance §6) — but there is no head/summary compaction, no artifact-path
handoff, and no re-read-a-slice mechanism for any tool today.

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
