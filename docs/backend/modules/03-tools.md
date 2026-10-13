# Module: Tool registry (`backend/src/tools/`)

The agent's entire action surface. The agent **never** touches network or filesystem
directly — only through registered tools. All tools are **read-only and always runnable** (`propose_alert` is the one "propose-only" class:
it computes nothing and writes nothing — see below);
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
wide scalar result. **Over 120 tools are opted in today** (the exact set is locked by
`tests/agent/test_loop.py::CARD_OPTED_TOOLS`) — the price/chip/flow/valuation/derivatives/
fundamental/ETF/macro/theme/score/screener/TDCC/analyst reads whose typed output is genuinely tabular,
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

**Package layout.** A tool group stays one module until it grows past a few hundred lines; the
market-data group is a package, `tools/market/`, with one module per category so no file is a
wall of code:

| Module | Tools |
|---|---|
| `quotes.py` | `get_price_quote`, `get_live_price_quote` |
| `symbols.py` | `get_symbol_info`, `get_symbol_directory_search`, `get_price_history`, `get_symbol_quotes` |
| `dividends.py` | `get_dividend_schedule`, `get_dividend_calendar` |
| `chip_flows.py` | `get_institutional_flow`, `get_chip_flows` |
| `company_profile.py` | `get_company_profile` |
| `etf_report.py` / `etf_money_flow.py` / `etf_holdings.py` / `etf_list.py` / `daily_signal.py` | the active-ETF tools |
| `advance_decline.py`, `momentum.py`, `holder_accumulation.py`, `index_contribution.py` | breadth and ranking tools |
| `heatmaps.py` | `get_sector_heatmap`, `get_sector_constituents`, `get_stock_heatmap` |
| `sector_rotation.py` | `get_sector_rotation`, `get_sector_rotation_rrg` |
| `_common.py` | store-read helpers and models used by more than one of the above |

Each tool class sits next to its input/output models and private helpers. A helper or model used by
two or more modules lives in `_common.py` and has a public name; modules import only from
`_common`, never from each other. `tools/market/__init__.py` re-exports the public names and
builds the registration table. New category-sized tool groups should follow the same shape.

**Real count.** `tools/__init__.py::build_default_tool_registry(router)` assembles every domain
tool-group module (one `*_tools.py` per domain plus the `tools/market/` package) into one
`ToolRegistry` (`tools/registry.py`). Called with no `session_factory`/`market_data_reader`
(the theme/score/screener/trend-score/summary groups need them to register) it yields **158 tools**;
with the full production wiring it yields about **170**. The list below is a domain-grouped index
of the real tool names; per-tool detail (input/output shape, capability wrapped) lives in each
file's own docstrings.

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
  `sources/mops.py` serves. `get_board_resolutions`/`get_buyback_status`/`get_investor_conferences`
  have **no tool** (no matching `Capability` in `schemas/enums.py`). The other MOPS tools in
  `mops_tools.py` are `get_self_assessed_earnings`, `get_material_announcement_feed` and
  `get_insider_transfer_declarations`.
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
  *Design's `get_sector_indices` (TW 未含金融電子/產業分類 sub-indices) has no real tool; the
  whole-market TW industry heatmap is `get_sector_heatmap` (`tools/market/`, web-only).*
- `get_market_status` — yfinance market open/closed status (non-TW).
- `run_equity_screener_us` — yfinance-backed US/GB/JP/EU equity screener. Not to be confused with
  `run_screener` (`screener_tools.py`), the analytics-owned gates→setup funnel for TW/US/ETF that
  the web Screener uses (see [20](20-analytics-scoring.md)).
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
- `get_taiex_index_snapshot` — whole-market TAIEX 5-second index reading (the index level only).
  Breadth and contribution are separate tools in `tools/market/`: `get_advance_decline` (public)
  and `get_index_contribution` (sponsor).
- `get_day_trading` — 當沖標的/量值/當沖比重.
- `get_total_return_index` — 報酬指數.
- `get_price_limit` — 漲跌停價.
- `get_international_stock` — FinMind's own US/GB/EU/JP roster+price bundle (non-yfinance path).
- `get_active_etf_roster` — free-tier whole-market active-ETF roster.
- `get_finmind_stock_news` — FinMind's own news dataset. The design's `get_stock_news` (multi-source
  aggregator) and `get_social_buzz`/`get_trending_buzz` (PTT) became three real tools, all
  `public` and **web-only** (REST routes, no skill - so the chat agent cannot call them; the
  `news-sentiment` skill is still not built): `get_news_feed` (`news_tools.py`, RSS + optional
  Finnhub), `get_ptt_board` and `get_ptt_stock_search` (`ptt_tools.py`; unverified chatter,
  attention signal only).
- `get_etf_fund_info` — TW ETF fund-level metadata (issuer, tracked index, units outstanding) —
  not per-constituent holdings (documented gap in the adapter itself). Sibling tools in
  `etf_pcf_tools.py`: `get_etf_expense_ratio` (SITCA 內扣費用), `get_etf_theme_tags` (主題/因子 +
  受益人次), `get_etf_trailing_returns`, and `get_etf_premium_discount` (a bounded
  premium/discount-to-NAV history plus the latest AUM; the AUM column is also in `get_etf_list`).
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
- `get_company_profile` — see immediate-core note below; composes sector/industry, latest dividend,
  market value/PER/yield/EPS, revenue YoY/margins and the MOPS identity fields (董事長/總部/資本額/
  …); anything a call could not populate is `None` and named in its own `missing_fields`, never
  fabricated. *Design's `get_company_relationships` (集團企業/轉投資/大股東) has no real tool.*
- `get_price_quote` — see immediate-core note below.
- `get_dividend_schedule` — per-stock/ETF 除權息 + 配息 timeline.
- `get_institutional_flow` — 三大法人 per-stock (public slice; `get_chip_flows`/
  `get_government_bank_flows` below are the Sponsor-gated finer cuts).
- `get_analyst_consensus` (`analyst_tools.py::GetAnalystConsensusTool`) — analyst target
  prices/recommendations/estimates (`Capability.ANALYST_CONSENSUS`); `card_type =
  "analyst_consensus"`. Real and registered — **not** in the "not built" list below.
- `get_esg_score` (`analyst_tools.py::GetEsgScoreTool`) — Sustainalytics-derived ESG risk score
  (`Capability.ESG`); `card_type = "esg_score"`. Real and registered — **not** in the "not
  built" list below.
- `get_tdcc_securities_info` / `get_tdcc_pledged_custody_shares` /
  `get_tdcc_shareholding_distribution` / `get_tdcc_offshore_fund_nav` /
  `get_tdcc_evoting_schedule` / `get_tdcc_monthly_custody_accounts` (`tdcc_tools.py`) — six
  router-based capability tools wrapping the `TdccAdapter` (集保結算所 TDCC OpenData: 證券基本資料/
  質押集保餘額/股權分散表/境外基金 NAV/電子投票議案/月底集保帳戶數). Plus `get_tdcc_raw_dataset`, a
  direct-adapter-call catch-all for querying any other TDCC dataset id by its raw CSV shape —
  7 tools total, all `public` (free, keyless upstream).
- `ask_user` (`ask_user_tools.py::AskUserTool`) — an informational mid-turn clarification tool:
  echoes back a question + optional tappable options as its typed output (no I/O, no side
  effect) and opts into `card_type = "ask_user"` so it renders as a governed `RichCard`. Not a
  permission/approval gate — every other tool still always runs regardless; this is meant to be
  the model's only action for the turn when used, with the user's next message answering it.
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
  `stock_scores` row the daily `analytics/scoring_batch/` batch wrote; returns `found=false`
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

- `propose_alert` (`propose_alert_tools.py::ProposeAlertTool`) — **propose-only**: validates an
  alert rule assembled from the user's words (`symbol`, `market` TW/US, a typed `RuleCondition`
  from `schemas/alerts.py`, trigger mode, expiry) and echoes it as an `alert_proposal` card
  (`card_type = "alert_proposal"`). It performs **no I/O and creates nothing** — the signed-in user
  taps 建立 on the card and the *frontend* calls the ordinary `POST /alerts` (auth, caps, market
  checks). So the confirm gate is the card, not a flag the model could skip, and the agent has no
  write path. Validation lives in the input model (a Taiwan-only template on a US symbol, an
  out-of-range threshold) so a bad proposal returns to the model as a correctable argument error.
  Reachable in normal chat via `_WARM_TOOLS` (skill-less turns) and the `alert-proposal` skill
  (`tools: [propose_alert, ask_user]`). The model does not choose the frequency or the lifetime
  (`trigger_mode` is not an argument; the proposal is once-per-day, 90 days) — a live model once set
  "once" unprompted; the user edits them after creating. The result carries a `next_step` note
  ("the card is on screen; do not call again") because one model re-proposed in a loop without it.
- `get_morning_brief` (`morning_brief_tools.py`) — the data behind the 晨間摘要: one call that runs
  the market/macro/institution/sector/calendar/watchlist/ETF/news tools concurrently, isolates each
  section's failure into an `unavailable` list, and adds rule-based `flags`. PUBLIC, deferred; see
  [modules/14 §5](14-scheduler-alerts.md). Registered last because it composes the other tools.
- `get_annual_fundamentals`, `get_revenue_seasonality`, `get_risk_metrics`, `get_etf_annual_summary`,
  `get_comparison_metrics` (`report_tools.py`) — the pre-computed numbers behind the analysis
  reports (see [modules/14 §4](14-scheduler-alerts.md)). PUBLIC, per-symbol, read the same
  capabilities as the raw statement/price/dividend tools; results carry `chart_hints` and are
  registered as deferred tools. `get_dividend_policy_summary` now defaults its window to five years
  (the agent is not told today's date) and reports `days_to_fill` per 填息 event.
- `propose_schedule` (`propose_schedule_tools.py::ProposeScheduleTool`) — **propose-only**, the
  recurring-briefing twin of `propose_alert` (`card_type = "schedule_proposal"`). The model picks a
  `template` from the `schemas/schedule_templates.py` catalogue (`pre_open_brief`,
  `after_close_chip`, `disposition_watch`, `weekly_catalyst`, `weekly_etf`, `us_after_close`) and
  ticker `targets` (one for every template except the pre-open brief, up to 10); it never supplies a
  cron or a skill name. The output carries the resolved `trigger` and `action` — the exact bodies
  `POST /schedules` takes — and the card posts them on 建立. No I/O, creates nothing. Reachable via
  `_WARM_TOOLS` and the `schedule-proposal` skill (`tools: [propose_schedule, ask_user]`), whose
  recurrence triggers (每天, 每週, 定期, 排程…) are weighted so "每天開盤前幫我看…" routes here and
  not to the one-off `daily-context-brief`.

**Design names with no tool of that name:** `get_disposition_stocks`/`get_disposition_forecast`/
`get_attention_stocks` are covered by two real tools instead: the narrower, Sponsor-gated
`get_disposition_securities` (below) and `get_disposition_board` (`disposition_board_tools.py`,
Sponsor, web-only: 注意/處置 buckets with price, institutional flow, margin ratio and a rule-based
"days until next disposition" projection). There is still no TPEX 注意股 adapter.
(`get_esg_score` and `get_analyst_consensus` are real, registered tools; see `analyst_tools.py`
in the list above.)

- `get_loan_collateral_balance` — 資金貸與/質押 collateral balance (**note:** this is a balance
  figure, not a full 質押比/背書保證/訴訟裁罰 composite; the `governance-risk` skill is narrowed to
  質押比 + this balance — see [08-skills.md](08-skills.md)).
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

**Design names with no tool of that name:** `get_realtime_snapshot` and `build_live_context`
(`TAIFEX_MIS` context is staged behind `enable_taifex_mis_validation` in `context/builder.py`, not
exposed as a tool). The real live reads are `get_live_quote_snapshot` (`live_tools.py`, TWSE MIS
best-5, free), `get_tick_snapshot`/`get_futures_snapshot`/`get_options_snapshot`
(`realtime_tools.py`, Sponsor) and `get_live_price_quote` (US WebSocket tick, `tools/market/`).

### Which tools the chat agent can call
The agent only ever sees tools from three places: the **matched skill's allowlist**
(`orchestrator._tools_for`), the **warm set** used when no skill matches (`get_canonical_context`,
`get_source_health`, `propose_alert`/`propose_memory`/`propose_schedule`/`propose_watchlist_change`),
and `web_search` + `fetch_url`, added only when the chat's *Web search* toggle is on (see the
web-search section below). Every other registered tool is reachable by the **web through REST**
(`api/routers/market_rest/`, `analytics.py`, `themes.py` dispatch them by name) and by MCP's
`search_tools`, but **not by the chat agent**. Dozens of tools are in that web-only group (market
heatmaps/rankings/strips, `get_news_feed`, `get_price_history`, `get_symbol_quotes` (the list-row view: name, close, daily
change %, volume, sparkline), `get_technical_indicators`,
`get_stock_summary`, `get_trend_score`, `run_screener`, ...). That is by design for dashboards,
but a capability the agent should have needs a skill that lists the tool.

### `public`/AI — derived analytics & scoring ([20](20-analytics-scoring.md))
Registered tools (module 20 owns the engines behind them):

| Tool | Module | Class | Used by |
|------|--------|-------|---------|
| `get_stock_score`, `get_score_leaderboard` | `score_tools.py` | public | `stock-score` skill |
| `get_trend_score` | `trend_score_tools.py` | public | web AI panel (no skill) |
| `get_stock_summary` (headline, narrative, SWOT) | `summary_tools.py` | sponsor | web AI panel (no skill) |
| `run_tag_screener`, `get_tag_catalog` | `screener_tools.py` | public | `stock-screener` skill |
| `run_screener`, `get_factor_catalog`, `get_screener_presets` | `screener_tools.py` | public | web Screener (no skill) |
| `get_technical_indicators` (MA/Bollinger/KD/MACD, support/resistance, patterns) | `technical_tools.py` | public | web (no skill) |
| `get_themes`, `get_theme_members`, `get_symbol_themes`, `get_theme_tiers` | `theme_tools.py` | public | `theme-lookup` skill (`get_themes` is web-only) |
| `get_supply_chain_edges`, `get_theme_tier_heatmap` | `theme_tools.py` | public | web (no skill) |

Design names that have no tool of that name: `get_indicators` (= `get_technical_indicators`),
`get_score` (= `get_stock_score`), `get_score_breakdown` (the factor breakdown is inside
`get_stock_score`), `get_ai_summary`/`get_swot` (= `get_stock_summary`), `get_top_stocks`
(= `get_score_leaderboard`). Saved screens are REST routes on the store, not tools
(`save_screen`/`list_screens`/`delete_screen`). **Not built:** `get_theme_flow` and
`get_figure_events`.

## Deferred / lazy tool loading

With about **170 tools** in the full production wiring (fewer when the registry is built without the
`session_factory`, `market_data_reader` or summary LLM), sending every schema every turn wastes
context. Following Pi's
`splitDeferredTools()`, the registry splits tools into:
- **immediate** — **the canonical core set** whose full schemas are always sent (defined **here**,
  the single source of truth; other docs reference this list, never re-enumerate it). The real,
  wired-up core (`tools/registry.py`'s `immediate=True` registrations) is exactly:
  `get_canonical_context`, `build_conversation_package` (`context_tools.py`), `get_source_health`
  (`health_tools.py`), `get_company_profile`, `get_price_quote` (`tools/market/`) — **5 tools**.
  This is narrower than the design's originally-named 6-tool core: `get_market_context` has no
  real tool at all (see above), and there is no separate immediate news tool; `get_price_quote`
  is the basic price-quote read the design meant. **The MCP projection exposes the exact same
  5-tool immediate core, not a subset** — `mcp/app.py::build_mcp_server` registers a dedicated
  MCP tool for every name in `registry.immediate_tools()`, verified end-to-end against the real
  registry `tests/mcp/test_app_real_registry.py` builds (its own fixture wires a stub
  session factory/Parquet reader but not the full production set)
  (`test_real_immediate_tools_are_registered_as_dedicated_mcp_tools` asserts all 5 names,
  including `get_company_profile`/`get_price_quote`, appear as dedicated MCP tools).
- **deferred** — the long tail (everything else: per-signal chip/ETF/derivatives/MOPS/analyst/
  technical/macro/international/TDCC tools, `ask_user`, the ETF overlap-checker/comparison pair,
  the three relational theme-lookup tools, the stock-score pair, the tag-screener pair, and the
  calendar-events tool); exposed to
  the model only as a compact **index** (name +
  one-line description). When the model needs one, it's loaded on demand (full schema injected
  next turn), mirroring progressive skill loading ([08-skills.md](08-skills.md)). A skill's
  `tools` allowlist pre-loads exactly its deferred set.

## Web search — a capability the agent uses (tool), not a pipeline

Web search is exposed to the agent as **tools** — `web_search` and `fetch_url` — registered
here (`tools/web_search_tools.py`), deferred like any other long-tail tool, invoked in the loop
like any other tool call. `web_search` is `public` data-class.

**Real implementation — a genuine, wired per-turn toggle with native-vs-Brave selection:**
- **A real per-turn enablement toggle exists.** `AgentQueryRequest` (`api/routers/agent.py`) has
  a real `web_search: bool = False` field (mirrored on the `regenerate`/`edit` request bodies),
  threaded through `POST /agent/query` into `Orchestrator.run`'s `web_search_enabled`
  parameter. It is consulted only on the `none`/`single` framework root-agent paths — fan-out
  lanes (`subagent`/`team`) never receive it, mirroring the `model_override` carve-out.
- **Real backend-selection logic exists**, in `orchestrator/orchestrator.py::_maybe_add_web_search`:
  when `web_search_enabled` is true, it branches on the resolved LLM client's own
  `client.capabilities.native_web_search` flag. If the client has a native web-search mechanism
  (Anthropic/Gemini today), that flag alone is granted into the turn's `chat()`/`stream()` call
  (`native_web_search=True`) — no `web_search`/`fetch_url` tool is added, since the native path
  already gives the model the same grounding ability server-side, without an extra tool-call
  round trip. If the client has no native mechanism (OpenAI/local), it falls back to the
  additive path: `web_search` and `fetch_url` (looked up from the registered tool table) are
  appended to the turn's tool list. The two paths are mutually exclusive per turn — never both
  active at once. A deployment that hasn't registered the web-search tools (no Brave key
  configured) silently grants nothing rather than raising.
- The underlying Brave-backed implementation is `sources/web_search.py::WebSearchAdapter`
  (`WEB` source_id, gated on `BRAVE_SEARCH_API_KEY`), reached through `dataaccess/web_search.py`'s
  `WebSearchGateway` seam rather than the router (`WEB` has no `(capability, market)` row in
  `source_priority.yaml` — a query/URL isn't `(capability, symbol)`-shaped). `fetch_url` is a
  second, plain `httpx` GET behind the same adapter (no separate config).
- **Deep research** is not a tool: it is a Pro-only turn mode built on this adapter's hardened
  `fetch_page` (SSRF-safe, size-capped) - see [24](24-deep-research.md).

On the Brave path, both tools run through `WebSearchGateway` (`dataaccess/web_search.py`) →
`WebSearchAdapter`, size-cap `fetch_url`'s extracted text at 20,000 characters (truncated flag
on overflow), and return a `currentness_caveat` on every result. Governance still applies as
designed regardless of which path served the turn: web results are external/unverified, so
every claim drawn from the web must be **cited with its source URL** and is subject to the same
non-trading `guardrails.check_output` scan as any other output.

## Tool output size control

**Not built.** No tool in `tools/` truncates a large result, persists it to a
file/artifact for later re-reading, or attaches row/line anchors. Every row-returning tool (via the shared `tools/_rows.py::fetch_rows`/
`validate_rows` helper) returns its full validated row tuple as-is; the only real size control
in the registry today is `fetch_url`'s flat 20,000-character text cap
(`tools/web_search_tools.py::_FETCH_URL_TEXT_CAP`, with a `truncated` flag on overflow) — there
is no equivalent for any market-data tool's row payload. What genuinely is enforced: every
`SourceResult`-backed tool still carries its `currentness_caveat`, and raw upstream payloads
are never surfaced verbatim (each tool validates rows into its own typed `schemas.capabilities`
model — raw-payload rules in [../../../backend/CLAUDE.md](../../../backend/CLAUDE.md) §4 and §8) — but there is no head/summary compaction, no artifact-path
handoff, and no re-read-a-slice mechanism for any tool today.

## Enforcement

- A tool runs freely; if its `data_class == sponsor` and the caller lacks the entitlement,
  the `accounts/entitlements` check returns a **masked teaser** (`{locked, required_tier,
  preview}`) instead of full data — the frontend renders it blurred/馬賽克 with an upgrade
  CTA. This check runs in the `pre_tool` hook ([10-events-hooks.md](10-events-hooks.md)).
- No tool requires approval or a confirmation token to execute.
- Tools reach data only through `dataaccess/` (the router and gateways); only `sources/` performs network I/O.
- Every dispatch (args + result summary + data_class) is written to the journal.

## MCP exposure

**All** tools are exposed over MCP (all read-only). `sponsor` tools return the masked teaser
unless the MCP principal is entitled — same rule as the API, no special-casing.
