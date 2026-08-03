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
  - 所有判斷僅為資訊參考，不構成投資建議 (non-trading, §governance).
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
buy/sell/target/sizing language. Level-3 `resources` load on demand via `read_skill_resource(name)`.

## Invocation

This is a **web/mobile chat** product, so skills are **not** primarily triggered by typed
slash commands (that's a CLI affordance). Triggering, in priority order:

1. **Classifier-selected (primary):** the orchestrator's intent classifier
   ([11-orchestration.md](11-orchestration.md)) — an **LLM call** — reads the natural-language
   message and picks 0–4 skills **+ the framework** (only the LLM can pick the framework, so it is
   the primary decision-maker). Each skill's `triggers` keywords play a supporting role via a
   **deterministic keyword matcher**: a **fast-path** (an unambiguous keyword hit can pre-pin a skill
   without waiting on the classifier) and the **fallback** (if the classifier call fails,
   keyword-match `triggers` and default the framework to `single` — see [11](11-orchestration.md)
   §1). This is how most skills run — the user types "全面分析 2330" and the classifier assigns
   `[chip-flow-review, earnings-quality, valuation-band]`.
2. **UI action chips (secondary):** the web/mobile client renders quick-action buttons
   ("Analyze chips", "Compare ETFs", "Valuation") that map to a skill — the GUI equivalent
   of a slash command, one tap instead of typed syntax. Sent as a structured hint that
   pins the skill, bypassing classification.
3. **`/skill` shortcut (optional, power-user):** if a client's input box supports it,
   `/chip-flow-review 2330` is accepted as a convenience alias — not load-bearing, and not
   required on any surface.

However triggered, the skill is loaded into context, its `tools` allowlist is enforced by
guardrails for the duration, and execution streams over the same SSE protocol. If a skill's
allowlist includes `sponsor` tools, those results are entitlement-masked for unentitled users
(the skill still runs and reports around the masked section).

## Registry & governance

- `skills/registry.py` discovers skills from a `skills/` dir (built-in) and an optional
  user dir. Each is validated against a schema on load.
- **Tool allowlist:** a skill may only call tools in its `tools` list; the runtime refuses
  any other call. `sponsor` tools in the list are entitlement-masked per user (no approval).
- **Non-trading:** skill outputs pass the same `guardrails.check_output` — no skill can emit
  trading signals, regardless of its instructions.
- Skills are versioned; the journal records which skill+version produced a finding, so an
  external handoff (conversation package) is reproducible.

## Starter skill set (built-in)

| Skill | Purpose | Uses sponsor data? |
|-------|---------|--------------------|
| `chip-flow-review` | 三大法人/融資融券/借券/分點 flows for a stock | 分點 (sponsor); rest public |
| `etf-compare` | Compare holdings/NAV/折溢價/fees/yield/受益人數 of two ETFs | active-ETF holdings (sponsor) |
| `earnings-quality` | Revenue trend + margin/ROE/FCF read from statements | no (public) |
| `valuation-band` | PE/PB band + yield context vs history | no (public) |
| `derivatives-positioning` | 期權 OI / PC ratio / 大額交易人 context | no (public) |
| `daily-context-brief` | Assemble conversation brief for a watchlist | no (public) |
| `disposition-watch` | 處置/注意股 status, disposition period & "最快 N 日後進入處置" forecast | no (public) |
| `catalyst-scan` | MOPS 重大訊息/董事會決議/庫藏股/內部人/法說會/自結 catalysts for a stock, classified | no (public) |
| `governance-risk` | 質押比/資金貸與/背書保證/訴訟裁罰 red-flag gate | no (public) |
| `news-sentiment` | Near-real-time news + PTT stock buzz + sentiment for a symbol | no (public) |
| `active-etf-flow` | 主動式ETF 操作日報 + cross-ETF 資金流向 (流入/流出, 加碼/減碼, 多空共識) | active-ETF holdings (sponsor) |
| `market-pulse` | 大盤: 加權/櫃買, 指數貢獻排行, 產業別漲跌, 個股漲跌分佈/家數 | no (public) |
| `company-profile` | 個股資訊卡: 基本資料 (產業/董座/成立/總部/官網/資本額) + 財務概況 (市值/PE/PB/營收YoY/三率/EPS) | no (public) |
| `analyst-consensus` | 目標價/評等/升降評 + earnings/revenue consensus (yfinance, large-cap; attributed third-party estimate) | no (public) |
| `market-calendar` | Upcoming events for watchlist/market (除權息/配息, 股東會, 法說會, 財報/月營收 deadlines, 期貨結算, rebalance, macro) | no (public) |
| `technical-read` | Full 技術面 read: 均線排列, KD/MACD/RSI, 布林, **量能 (volume/OBV/VWAP + 爆量/量縮)**, **支撐/壓力 (support/resistance levels + break/false-break)**, **N 波回檔 (impulse to a key level → pullback → hold)**, K-line patterns — all 描述, non-trading | no (public) |
| `multi-factor-score` | Lean 5-factor 多空 composite (momentum/RS · trend · value · quality · 籌碼-flow), horizon-aware (swing/position) + 偏多/中性/偏空 band + breakdown + regime caveat | 分點 flow sub-signal (sponsor); rest public |
| `ai-summary-swot` | AI 智能摘要 + 市場定位 + SWOT, grounded in the factor scores (attributed, caveated) | no (public); AI feature = Premium+ |
| `stock-screener` | The funnel — hard gates (liquidity/處置/…) → setup screen or named preset (Minervini/CANSLIM/Weinstein/Piotroski/Magic Formula/TW) → rank survivors | public/sponsor per criteria |
| `theme-discovery` | LLM-discovered emerging themes + 資金流向 per theme | no (public); AI feature = Premium+ |
| `live-snapshot-check` | Bounded live-ish observation | real-time snapshot (sponsor) |
