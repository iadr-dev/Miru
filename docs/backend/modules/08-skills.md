# Module: Skills (`backend/src/skills/`)

Skills are **packaged, versioned analysis playbooks** — the Claude Code parallel to its
skill system. Each is a reusable, governed procedure over the tool registry (e.g. "chip-flow
review of a stock", "compare two ETFs", "earnings-quality read"). Skills make the agent's
best analyses repeatable and let users invoke them explicitly via `/skill`.

## What a skill is

A skill is a directory with a manifest + instructions + optional resources:

```
skills/chip-flow-review/
├─ SKILL.md          # frontmatter (name+description) + instructions body
└─ resources/        # optional: prompt fragments, output templates, checklists, examples
```

## Three-level progressive disclosure (token-optimal loading)

Skills load in **three tiers**, each pulled into context only when needed — the token-efficient
design (mirrors deferred *tool* loading in [03-tools.md](03-tools.md)):

| Level | Content | Loaded | Cost |
|-------|---------|--------|------|
| **1 — name + description** | one-line frontmatter per skill | **always** in the system prompt (the "menu" the classifier picks from) | ~1 line × N skills |
| **2 — body** | the `SKILL.md` instructions (tool order, structure, caveats) | **only when this skill is selected** (classifier/planner or explicit invoke) | one skill's body |
| **3 — resources** | files under `resources/` (templates, checklists, long examples) | **only when the body references one** — the agent reads it on demand via a tool | deferred; usually none |

So N skills cost only N descriptions at rest; the winner's body loads on trigger; heavy
reference material is a third tier that stays out of context unless actually used. A skill's
`tools` allowlist simultaneously pre-loads exactly its deferred tool schemas — skills and
tools use the same lazy-load principle.

```yaml
# SKILL.md frontmatter — a skill bundles matching + a tool-call procedure + rules
name: chip-flow-review
version: 1.0.0
description: Analyze institutional & margin flows for a stock over a window (三大法人/融資融券/借券/分點).
triggers: [法人, 外資, 投信, 融資, 融券, 主力, 籌碼, institutional, foreign, margin]  # keyword match (LLM fallback on miss)
design_pattern: tool_wrapper            # none | tool_wrapper | pipeline | subagent — hint to the orchestrator
inputs:
  stock_id: {type: string, required: true}
  window_days: {type: integer, default: 20}
tools: [get_canonical_context, build_conversation_package, get_source_health, get_chip_flows]  # allowlist (pre-loads these deferred tool schemas)
output_structure:                        # the sections the answer must cover
  - '外資: 5/10/20 日買賣超趨勢'
  - '投信/自營: 買賣超與作帳/避險訊號'
  - '主力/融資券: 主力進出、融資融券變化、量價異常'
  - '籌碼結論: 集中或分散 (描述，非買賣訊號)'
freshness_rule: 籌碼數據須為最近一個交易日；引用任何數字時標明資料日期。
boundaries:                              # per-skill guardrails (in addition to global guardrails)
  - 僅分析籌碼面，不提供價格目標或買賣價位。
  - 所有判斷僅為資訊參考，不構成投資建議 (non-trading).
resources: [output_template.md, chip_checklist.md]           # level-3, loaded on demand only
outputs: markdown | rich_card                                # caveated, non-trading
```

**A skill body carries four kinds of rule** (our SKILL.md frontmatter, fully specified above):
1. **A tool-call procedure** — `instructions` is a *numbered sequence of tool calls* ("1. get
   60-day price + indicators; 2. check 20-day institutional flow; 3. synthesize"), restricted to
   the `tools` allowlist. This is the "rules include tool calls" the design intends: the skill
   tells the loop **which tools, in what order, with what params**.
2. **Output structure** — the sections/`rich_card`s the answer must contain (`output_structure`).
3. **Freshness rule** — the currentness bar (`freshness_rule`) — must cite data dates.
4. **Boundaries** — per-skill guardrails (`boundaries`) layered on the global ones.

The body is **not** code that bypasses the loop — it is guidance the loop follows, so **all
global guardrails still apply**: even if a trigger keyword is 該買嗎 ("should I buy?"), the skill
**reframes to descriptive factor context** and `guardrails.check_output` still blocks any
buy/sell/target/sizing language. Level-3 `resources` load on demand via
`SkillRegistry.read_resource(name, filename)` — **there is no `read_skill_resource` tool**;
one `SKILL.md` body (`chip-flow-review`) references that name conditionally ("if
`read_skill_resource` is available"), but it is not registered in `tools/` and not reachable by
the agent as a tool call today — only `SkillRegistry` itself can call `read_resource` directly.

## Invocation

This is a **web/mobile chat** product, so skills are **not** primarily triggered by typed
slash commands (that's a CLI affordance). The real, built mechanism (`orchestrator/
classifier.py::IntentClassifier`) is a single path, not the three-tier priority list the
original design sketched:

1. **Classifier-selected (the only real path):** a cheap LLM call
   (`orchestrator/classifier.py`) reads the natural-language message + the skill menu and
   returns a `Routing` (framework + 0–4 skills + a short reason), journaled as `route.decided`.
   This is how skills run today — the user types "全面分析 2330" and the classifier assigns
   `[chip-flow-review, earnings-quality, valuation-band]` with `framework=subagent`.
2. **Keyword match is a *failure fallback only* — not a fast-path.** `_keyword_fallback` scores
   each skill's `triggers` against the message and pre-pins the single best-scoring match with
   `framework=single`, but it only ever runs when the classifier LLM call itself fails
   (transport error, unparseable JSON, an invalid verdict) — the module's own docstring is
   explicit that an unambiguous keyword hit does **not** pre-pin a skill or bypass the classifier
   while it's available. There is no keyword fast-path that races or short-circuits the LLM call.
3. **No pinned-skill hint or `/skill` shortcut.** `POST /agent/query`'s `AgentQueryRequest`
   (`api/routers/agent.py`) has no field for a skill chosen from a quick-action button, and no
   `/skill-name` parsing exists in the request path.

However triggered, the skill's `tools` allowlist is resolved to concrete tools and handed to the
lane's `AgentLoop` (`orchestrator/orchestrator.py::_tools_for`) — a tool outside the allowlist is
simply never in the loop's tool set, so it's unreachable by construction; there is no separate
runtime "refusal" check on an attempted out-of-allowlist call in the request path itself (that
predicate, `skills/registry.py::enforce_allowlist`, exists and is unit-tested but is not called
from `orchestrator/` today — the allowlist is enforced by *what tools the lane is given*, not by
intercepting a call to a disallowed name). If a skill's allowlist includes `sponsor` tools,
those results are entitlement-masked for unentitled users via `tools.base.dispatch_tool` (the
skill still runs and reports around the masked section).

## Registry & governance

- `skills/registry.py::SkillRegistry.load(directories)` discovers skills from every
  `<dir>/*/SKILL.md` given. The real composition root (`orchestrator/composition.py::
  build_skill_registry`) only ever passes the one bundled built-in directory
  (`skills/built_ins/`) — **there is no optional user skill directory wired up today**; `load`
  accepts a list so a second directory is structurally possible, but nothing in the app passes
  one. Each `SKILL.md` is validated against `skills.models.SkillFrontmatter` on load; a
  malformed manifest raises `SkillValidationError` at startup rather than being silently
  skipped, and a duplicate skill name across directories also raises.
- **Tool allowlist:** a skill may only call tools in its `tools` list — enforced *by
  construction*: `orchestrator/orchestrator.py::_tools_for` resolves only the allowlisted tool
  names into the lane's `AgentLoop`, so a disallowed tool simply isn't in the set the loop can
  dispatch. The separate `skills.registry.enforce_allowlist` predicate (raises
  `ToolNotAllowedError`) exists and is unit-tested but is not called anywhere in the request
  path — it's the pure-function half of the guarantee, not an active runtime interceptor.
  `sponsor` tools in the list are entitlement-masked per user via `tools.base.dispatch_tool`
  (no approval step).
- **Non-trading:** skill outputs pass the same `guardrails.check_output` — no skill can emit
  trading signals, regardless of its instructions.
- **Skills are versioned in their frontmatter** (`SkillFrontmatter.version`), logged once at
  registry load time (`skill_loaded` structlog line). **The per-turn journal does not record
  which skill+version produced a finding** — `agent.journal.JournalEntry` has no skill/version
  field; only `tool_name`/`agent_id`/`parent_agent_id` are journaled per Step. Skill+version
  traceability for an external handoff is therefore not yet reproducible from the journal alone.

## Starter skill set (built-in)

| Skill | Purpose | Uses sponsor data? |
|-------|---------|--------------------|
| `chip-flow-review` | 三大法人/融資融券/借券/分點 flows for a stock | 分點 (sponsor); rest public |
| `etf-compare` | Compare two ETFs on fund-level metadata + holdings/money-flow (NAV/折溢價/受益人數/fees/yield not covered — no adapter) | active-ETF holdings (sponsor) |
| `earnings-quality` | Revenue trend + balance-sheet/cash-flow-derived quality read + settled dividend results | no (public) |
| `valuation-band` | PE/PB band + yield context vs history | no (public) |
| `derivatives-positioning` | 期權 OI / P-C ratio (self-computed from raw volume) / 大額交易人 context / VIX | 大額交易人 OI + VIX (sponsor); rest public |
| `daily-context-brief` | Assemble conversation brief for a watchlist | no (public) |
| `disposition-watch` | 處置有價證券 announcement stream for one stock, plus the whole-market 處置/注意股 board with a rule-based, caveated "days until next disposition" projection | disposition stream (sponsor); board public |
| `event-study` | 事件後歷史統計: what stocks historically did over the next 5/20/60 trading days after a disposition start / release, attention listing, capital-reduction restart or buy-back start / end (`get_event_study`): counts, mean / median, return relative to the market median, by-year table and typed limitations; descriptive only, never a signal | no (public) |
| `catalyst-scan` | MOPS 重大訊息 (today only) + 董監事持股/質押 for a stock (董事會決議/庫藏股/法說會/自結 not covered — no adapter) | no (public) |
| `governance-risk` | 質押比 (董監事持股) + 資金貸與/質押 collateral balance (背書保證/訴訟裁罰 not covered — no adapter) | collateral balance (sponsor); rest public |
| `active-etf-flow` | 主動式ETF 操作日報 + cross-ETF 資金流向 (流入/流出, 加碼/減碼, 多空共識) | active-ETF holdings (sponsor) |
| `company-profile` | 個股資訊卡 (narrow): sector/industry classification + latest announced dividend, missing MOPS-sourced fields disclosed | no (public) |
| `live-snapshot-check` | Bounded, delayed TWSE MIS live-ish quote snapshot, one explicit check at a time | no (public) |

Also built-in: the report/brief skills `stock-report-tw`, `stock-report-us`, `etf-report`,
`compare-report` (side-by-side two-symbol report), `morning-brief` and `stock-financial-comparison`
(revenue/seasonality/dividend comparison for two stocks); the confirm-card proposal skills
`alert-proposal`, `schedule-proposal`, `watchlist-proposal` and `memory-proposal` (each only
proposes - the user confirms on the card, and the agent never writes); and
`self-assessed-earnings-check` (MOPS 自結損益 filing, one monthly period at a time),
`tdcc-registry-lookup` (TDCC 集保 reference datasets) and
`warrant-and-derivatives-microstructure` (per-trade futures/option/spread ticks, intraday options
snapshot, warrant branch flow, night-session institutional flow; all Sponsor). The authoritative
list is the `backend/src/skills/built_ins/*/SKILL.md` directories.

Routing note: the keyword fallback (used only when the LLM classifier fails) scores skills by how many
of their `triggers` match, ties going to the alphabetically first name. `account-overview` therefore
avoids bare one-word triggers that the propose-only skills also use (`提醒`, `通知`, `自選`, `排程`):
"提醒我 ..." must still reach `alert-proposal` and "加入自選 ..." `watchlist-proposal`
(`tests/skills/test_account_skills.py` pins this).

**No skill exists** for `analyst-consensus` as its own skill (the tool lives in `company-profile`,
`international-equity-snapshot` and `stock-report-us`) or for `theme-discovery` (flow-ranked themes).
The former web-only tools now have chat homes: see the next section.

## Catch-all skills (cover previously-orphaned tools, not in the original 20-skill starter set)

A skill's `tools` allowlist is the *only* way the runtime dispatches a tool in a lane (see the
enforcement rule above), so the following skills give the long tail of registered tools (see
[03-tools.md](03-tools.md)) a coherent, natural-language-triggerable home. `ask_user` appears in the
allowlists of the report/proposal skills and the memory/schedule/alert/watchlist `propose_*` tools
are in the orchestrator's warm-tool default (`_WARM_TOOLS`).

| Skill | Purpose | Uses sponsor data? |
|-------|---------|--------------------|
| `margin-and-short-interest` | 融資融券/借券/融資維持率/外資持股/股權分散 for one stock, plus whole-market margin/short totals on request | 股權分散 + 大盤融資維持率 (sponsor); rest public |
| `block-trade-and-broker-flow` | 鉅額交易 (plain + broker-level) + 分點進出彙總 + 八大官股買賣 for one stock | all sponsor (broker directory lookup is public) |
| `futures-options-market-data` | 期貨/選擇權 contract roster, EOD price/volume/OI, dealer volume, spread quotes, FinMind + TAIFEX settlement, asset swap | spread/settlement/asset-swap (sponsor); roster/EOD/dealer-volume/TAIFEX settlement public |
| `convertible-bond-review` | One CB's profile (roster/overview/put schedule) + trading/institutional-flow/custody picture | all sponsor |
| `macro-dashboard` | 匯率/利率/黃金/原油/美債殖利率/Fear-Greed + TW 景氣對策信號 (market-level, no per-symbol scope) | all sponsor |
| `international-equity-snapshot` | yfinance-backed non-TW options/holders/fund-holdings/market-status/sector-overview/calendars + FinMind's own international roster+price, for US/GB/JP/EU | US minute price (sponsor); rest public |
| `reference-data-lookup` | Symbol search/lookup (non-TW), TW stock/warrant roster, trading calendar, active-ETF roster, warrant-underlying mapping, source matrix | warrant-underlying (sponsor); rest public |
| `market-breadth-and-flow` | TAIEX/TPEx 5-second index/order-book snapshots, TW/US index strips, advance/decline breadth, approximate index contribution, whole-market 三大法人 totals + one-stock wide view, 報酬指數, non-TAIEX index snapshots, US/GB/JP/EU equity screener | non-TAIEX index snapshot + index contribution (sponsor); rest public |
| `corporate-actions-and-market-value` | 減資/下市/分割/面額變動 events, daily market value + market-cap-weight ranking, 當沖 eligibility, 漲跌停參考價 | market value + weight ranking (sponsor); rest public |
| `intraday-and-multi-timeframe-price` | Tick-level trades, minute K-bars, weekly/monthly bars, ten-year price line, 暫停交易 announcements for one stock | all sponsor |
| `industry-chain-money-flow` | 產業鏈資金流 whole-market aggregate (single trading day) + a stock's static 產業鏈分類 | all sponsor |
| `finmind-stock-news` | FinMind's own news dataset for one stock, one trading day at a time (distinct from the excluded RSS/GDELT/Finnhub aggregator + PTT buzz) | no (public) |
| `theme-lookup` | 主題/產業鏈/供應鏈查詢: the theme list, what theme a stock is in, what stocks are in a theme, a theme's upstream/midstream/downstream breakdown, a company's supply-chain relations and chain position, and its entity graph (full detail is Pro; other plans get a preview) — over the relational theme store (industry-chain facts vs AI-researched concept members distinguished, curated vs heuristic tiers distinguished) | no (public) |
| `stock-score` | 多因子綜合評分: one symbol's cached 5-factor composite (momentum/trend_quality/value_guard/quality/revenue_flow) + descriptive band, swing/position horizon profiles, plus the whole-TW-universe bullish/bearish leaderboard — both cache-only, daily-batch reads | no (public) |
| `stock-screener` | 篩選器: tag path (`get_tag_catalog` then `run_tag_screener`, TW end-of-day tags) and numeric path (`get_factor_catalog`, `get_screener_presets`, `run_screener` over TW/US/ETF with liquidity/price/cap/disposition gates) — descriptive filters, never a ranked recommendation | no (public) |
| `account-overview` | 我的帳戶概況: the signed-in user's OWN watchlists (optionally with latest close), alert rules / scheduled briefings / 策略盯盤 subscription, today's chat quota and usage, and recent notifications - read-only (`get_my_watchlists`, `get_my_automations`, `get_my_usage`, `get_my_notifications`; `private` tools, [03](03-tools.md)). Says plainly when signed out or a list is empty; never reveals another user's data; changes go through the propose-only skills | no (private; free at every signed-in tier) |
| `watchlist-review` | 自選股回顧: how the user's own watchlist moved lately (`get_symbol_quotes`, `get_price_history`), upcoming ex-dividend / calendar events (`get_dividend_calendar`, `get_calendar_events`) and, on request, the cached descriptive factor score (`get_stock_score`) - descriptive only, no buy/sell/target wording, at most 20 symbols per call | `get_calendar_events` CB slice (sponsor); rest public |
| `etf-overlap-and-comparison` | 3檔以上非台股ETF持股重疊比較 (`get_etf_overlap`) + 跨市場（含台股）股利再投入總報酬比較 (`get_fund_comparison`), with honest disclosure of the TW-holdings/NAV adapter gap | no (public) |

### Tools the web uses that the chat agent now reaches through skills

The chat agent only gets a skill's allowlist, so every registered tool must be listed by a skill
(or be in the warm set / web-search set / `CHAT_EXEMPT_TOOLS`) - enforced by
`backend/tests/skills/test_tool_exposure.py`. The previously orphaned tools map as follows.

| Skill | Tools added | Uses sponsor data? |
|-------|-------------|--------------------|
| `stock-screener` | `run_screener`, `get_screener_presets`, `get_factor_catalog` | no (public) |
| `market-breadth-and-flow` | TAIEX/TPEx 5-second index/order-book snapshots, TW/US index strips, advance/decline breadth, approximate index contribution, whole-market 三大法人 totals + one-stock wide view, 報酬指數, non-TAIEX index snapshots, US/GB/JP/EU equity screener | non-TAIEX index snapshot + index contribution (sponsor); rest public |
| `live-snapshot-check` | `get_live_price_quote`, `get_tick_snapshot`, `get_futures_snapshot` | tick/futures snapshots (sponsor) |
| `daily-context-brief` | `get_symbol_quotes`, `get_price_history` | no (public) |
| `disposition-watch` | 處置有價證券 announcement stream for one stock, plus the whole-market 處置/注意股 board with a rule-based, caveated "days until next disposition" projection | disposition stream (sponsor); board public |
| `catalyst-scan` | `get_calendar_events`, `get_material_announcement_feed`, `get_insider_transfer_declarations` | no (public) |
| `company-profile` | `get_dividend_result` | no (public) |
| `stock-report-us` | `get_sec_filings` | no (public) |
| `stock-score` | `get_stock_summary` | summary (sponsor) |
| `active-etf-flow` | `get_daily_signal`, `get_etfs_holding_stock` | all sponsor |
| `news-and-sentiment` (new) | `get_news_feed`, `get_ptt_board`, `get_ptt_stock_search` | no (public); third-party content, forum posts are unverified data, never advice |
| `market-rankings` (new) | `get_tw_market_rankings`, `get_us_market_rankings`, `get_price_momentum`, `get_holder_accumulation`, `get_stock_heatmap`, `get_strategy_watch` | holder accumulation (sponsor) |
| `sector-rotation` (new) | `get_sector_heatmap`, `get_sector_constituents`, `get_sector_rotation`, `get_sector_rotation_rrg` | no (public) |
| `event-study` (new) | `get_event_study`; also listed by `disposition-watch` and `corporate-actions-and-market-value`, so a disposition or capital-reduction question that routes there can reach it | no (public) |
| `us-macro-data` (new) | `get_us_macro_dashboard`, `get_us_macro_series`, `get_us_economic_releases`, `get_series_revisions`, `get_macro_calendar` | no (public) |
| `technical-trend-read` (new) | `get_technical_indicators`, `get_trend_score` | no (public) |
| `etf-market-overview` (new) | `get_etf_list`, `get_dividend_calendar` | no (public) |

**Deliberately outside the skills:** `web_search`/`fetch_url` (enablement-gated generic tools,
not skill-shaped - the agent decides when to use them, subject to the per-turn web-search toggle)
and the warm set. `CHAT_EXEMPT_TOOLS` (in the guard test) lists any UI-only tool kept out of chat,
with a reason; it is currently empty (the theme and supply-chain tools are listed by `theme-lookup`).

**Real-backing.** Skills are backed by named tools in `backend/src/tools/*.py` and don't invent
fields their tools don't return. Several skills are honestly narrower than the original design intended, and say so explicitly in their
own `SKILL.md` `boundaries`/output-structure, mirroring `company-profile`'s established
`missing_fields` pattern: `catalyst-scan` (no 董事會決議/庫藏股/法說會/自結 adapter),
`governance-risk` (no 背書保證/訴訟裁罰 adapter), `disposition-watch` (no TPEX 注意股 adapter,
no escalation forecast), `etf-compare` (no NAV/折溢價/受益人數/fees/yield adapter),
`market-breadth-and-flow` (index contribution is an approximation and always carries its caveat).
