# 選股 / Screener (`/screener`)

The **funnel workbench** — a primary web nav item, and the product's pro-grade multi-market screening
tool. Backend: [modules/20](../../backend/modules/20-analytics-scoring.md) §5 (gates → setup → rank),
[modules/19](../../backend/modules/19-data-access-router.md) (MarketProfile / `supports()`),
[modules/03](../../backend/modules/03-tools.md) (`get_factor_catalog`, `run_screener`, `get_top_stocks`),
[api-reference §4a](../../backend/api-reference.md). Visual/interaction language:
[../design-system.md](../design-system.md) · [../interaction-spec.md](../interaction-spec.md).

> **Governance (load-bearing, [20](../../backend/modules/20-analytics-scoring.md) §7):** a screen result is a
> **filtered list matching described criteria** — 「符合條件的清單，非推薦名單」 — never a buy list.
> Liquidity is a **gate, never a weighted factor**. Sponsor (分點) factors → **馬賽克** for Free.
> 紅漲綠跌 + ▲▼, tabular-nums, dark-first. No 明牌/飆股/目標價/買賣 copy ([../brand.md](../../brand.md) §5).
> **Backtest/回測 is out of scope** (dropped).

---

## 1. The funnel (eliminate first, score last — not score-the-universe)

```
 ~2000 檔                                                              ← universe (per market)
   │  ⓐ HARD GATES  流動性 · 價格 · 市值 · 處置/注意/全額交割 · 蜜月期 · 資料完整度
   ▼  ~300–500 tradable                                               (binary, no mercy, no score)
   │  ⓑ SETUP       named preset  ∨  custom condition builder (AND/OR)
   ▼  ~30–80 candidates                                               (the strategy thesis)
   │  ⓒ SCORE & RANK  §2 composite (swing/position) or single factor
   ▼  top-N                                                           ← ranked survivors + breakdown
```

A live **`FunnelMeter`** rides above the results — three linked segments showing survivor counts as
gates/setup change (`2000 → 412 → 47 → 20`), each segment `tabular-nums`, the active stage `--accent`.
Maps to `run_screener` (ⓐⓑ) + `get_top_stocks` (ⓒ) — see [20](../../backend/modules/20-analytics-scoring.md) §5.

---

## 2. Market tabs (TW / US / ETF) — config-driven from `MarketProfile`

**Row-1 top tabs are rendered from the registered `MarketProfile` set** ([module 19](../../backend/modules/19-data-access-router.md)),
**not hardcoded** — exactly like [market.md §1](../market.md). Each market swaps **everything** via one
config object (`ScreenerMarketConfig`, adapts Kestrel `screener/config.ts`): its **source**, its
**available factors/gates** (only what its adapters `supports()`), and its **result columns**. Adding
JP/GB = a new `MarketProfile` + factor block — **zero UI rewrite**.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 股票跑馬燈 (marquee, design-system §7.5)                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  [ 台股 TW ]   美股 US    ETF          [篩選 Screen] 主題 Themes🔒            │  ← Row-1 market · Row-2 mode
└─────────────────────────────────────────────────────────────────────────────┘
```

| Tab | `market`×`asset_class` | Source (router) | Factor families | Sponsor→馬賽克 |
|-----|------------------------|-----------------|-----------------|----------------|
| **台股 TW** | `(TW, stock)` | FinMind + TWSE + derived | **技術 · 基本面 · 動能 · 籌碼** (full) | 分點券商進出 · 八大官股 (chip_broker sponsor) |
| **美股 US** | `(US, *)` | yfinance | **估值 · 成長 · 動能 · 分析師** (no 籌碼) | — (none; 籌碼 capability absent) |
| **ETF** | `(TW, etf)` | FinMind + issuer PCF | **折溢價 · 規模/受益人 · 持股 · 動能 · 配息** | 主動ETF 操作/資金流 (`get_active_etf_report`) |

The tab set follows the same `market`×`asset_class` model as the [watchlist rail §7.7](../design-system.md#77-watchlist-rail-右側自選欄--drag-and-drop)
(TW/US/ETF), so a symbol crosses screener↔watchlist↔detail consistently.

### 2.1 Per-market factor catalog (each maps to a real tool/capability)

Served by `GET /analytics/screener/factors?market=` (`get_factor_catalog`, bilingual). A factor
appears **only** if the market's adapters `supports()` its capability (module 19 §3) — the router's
`chip_broker: []` for US is *why* 籌碼 is absent there, not a UI decision.

**台股 TW** — the full bilingual catalog (技術/基本面/動能/籌碼):

| Family | Example factors | Backing | Class |
|--------|-----------------|---------|-------|
| 技術 Technical | MA5>20>60, 站上/跌破均線, 均線斜率, 布林突破, KD/MACD 交叉, 突破新高, 長紅/長黑, 支撐壓力/N波 | `get_indicators` (`set=ma,kd,macd,rsi,bollinger,sr,wave`) | public |
| 基本面 Fundamental | 連N月營收YoY>X%, 月營收/EPS創新高, EPS YoY, 三率(毛利/營益/淨利), ROE/ROA, 負債比, 速動比, 現金殖利率 | `get_company_profile` (financials snapshot) | public |
| 動能 Momentum | RS 相對強度, 近5/10日強勢, 急漲/急跌, 爆量 | `get_indicators` + `get_top_stocks(sort=momentum)` | public |
| 籌碼 Chips/Flow | 外資/投信 連N買·淨買超, 三大法人合計, 外資持股率變化, 融資減券增, 千張大戶比率 (`get_shareholder_dispersion`) | 三大法人 total (public) | public |
| 籌碼 (sponsor) | **分點主力進出 · 八大官股買賣超** | `get_chip_flows`, `get_government_bank_flows` | **sponsor→馬賽克** |

**美股 US** — yfinance-backed (`us`/`us_etf` query, Kestrel `screener/config.ts` `yfQueryType`):

| Family | Example factors | Backing | Class |
|--------|-----------------|---------|-------|
| 估值 Valuation | PE / forward PE, PB, PEG, EV/EBIT, 股價淨值比 | yfinance `financials`/valuation | public |
| 成長 Growth | 季/年 EPS growth, 營收 growth, 52週漲幅 | yfinance | public |
| 動能 Momentum | day gainers/losers, 近N月報酬, 市值分層 (small/large cap) | yfinance | public |
| 分析師 Analyst | 目標價, 評等, 分析師家數 (**attributed**, `null` when 0 opinions) | `get_analyst_consensus` | public (attributed) |

**ETF** — folds TW-ETF specifics; single-stock 籌碼/quality dropped (`detect_kind=tw-etf`, [20](../../backend/modules/20-analytics-scoring.md) §2c):

| Family | Example factors | Backing | Class |
|--------|-----------------|---------|-------|
| 折溢價 Premium | 折價/溢價 %, 折溢價區間 | `get_etf_nav_premium` | public |
| 規模/受益人 | AUM 基金規模, 受益人數趨勢 (retail-inflow proxy) | `get_etf_aum`, `get_etf_beneficiaries` | public |
| 持股 Holdings | 被N支ETF持有, 產業曝險, 前十大持股集中度 | `get_etfs_holding_stock` (passive PCF) | public |
| 動能 Momentum | 近N日強勢, 站上均線, 殖利率, YTD return | `get_indicators`, `get_company_profile` | public |
| 主動ETF (sponsor) | **操作日報 (新增/加碼/減碼) · 跨ETF資金流向** | `get_active_etf_report`, `get_etf_money_flow` | **sponsor→馬賽克** |

**Backend contracts (now defined — [20](../../backend/modules/20-analytics-scoring.md) §5B, [api-reference §4a](../../backend/api-reference.md), [03](../../backend/modules/03-tools.md)):**
- ✓ `GET /analytics/screener/factors?market=` returns the bilingual catalog **+ per-market `fields[]`
  descriptors** (unit/min/max/step/enum/preset-chips) the condition builder (§4) needs.
- ✓ `POST /analytics/screener/run` accepts a **custom AND/OR condition list**
  `{market, conditions:[{factor,op,value}], match, gate_overrides?, count_only?}` (`count_only` = the
  live survivor-count probe) as well as `{preset|screen_type}`.
- ✓ **US + ETF screening paths** defined via `market`: US = yfinance equity/etf query (估值/成長/動能/
  分析師, no 籌碼, **phased** → returns `not_supported`, UI degrades to "尚未支援"); ETF = `(TW,etf)`
  (折溢價/AUM/受益人/持股).
- ✓ **Saved / shared screens** (§6) persist via `/analytics/screener/screens` CRUD (`save_screen`/
  `list_screens`/`delete_screen`); a shared screen is a URL-encodable reconstruction of that state.

---

## 3. Named presets — one-tap starting points that **populate the builder**

The frameworks a trader knows, from `get_screener_presets` / [20](../../backend/modules/20-analytics-scoring.md) §5B.
Presented via **`PresetLibrary`** (adapts Kestrel `screener/PresetLibrary.tsx`):
category tabs + search + a **card grid** (name, one-line 描述, subtle bull/bear cue) — **not** a flat
wall of chips. Selecting a card **loads its conditions into the builder (§4)** so a user can *see and
tweak* the thesis — the preset is a starting point, not a black box.

| Category | Named presets (each purely descriptive / non-trading) |
|----------|-------------------------------------------------------|
| 趨勢 Trend | **Minervini Trend Template** (8 條件) · **Weinstein Stage-2** · 站上/多頭排列 |
| 品質 Quality | **Piotroski F≥7** · **Greenblatt Magic Formula** (EY+ROC) · ROE/三率 |
| 成長 Growth | **CANSLIM** (O'Neil) · **月營收創新高** · **三率三升** · EPS YoY |
| 籌碼 Chips | **投信連N買** · 外資同買 · 融資減券增 (sponsor sub-signals→馬賽克) |
| 動能 Momentum | 近5/10日強勢 · 急漲 · 爆量突破 |

- **Preset card** = `--surface`, `1px --border`, radius 12px; a **2px directional left-edge bar**
  (`--up` bull / `--down` bear / none neutral) + matching ▲/▼ glyph — a subtle 紅漲綠跌 cue, never a
  recommendation. Active = `--accent` hairline + `--accent-weak` tint + `aria-pressed`.
- **Category tabs** = pill row (999px), active `--accent`, count badge; **search** filters across all
  categories (Phosphor `magnifying-glass`, expands on focus).
- US/ETF presets swap per `MarketProfile` (US: momentum / growth-tech / undervalued-large / shorted;
  ETF: 高股息 / 折價 / 規模 top / 主動-strong).

---

## 4. Custom condition builder (`ConditionBuilder`) — the pro path

The filter builder for pro users: **pick factor → operator → value**, combine with **AND/OR**, save as
a named screen. Adapts Kestrel `screener/TwCustomFilterSidebar.tsx` + `FilterValueInput.tsx`.
A **left rail** (`--surface`, `1px --border`, radius 16px, 264px; below `md` → bottom-sheet drawer).

```
┌── 自訂條件  ConditionBuilder ───────  47 檔 ─┐   ← header: title + live count (tabular-nums)
│ [ 訊號 Signals | 條件 Conditions ]           │   ← sub-mode (SegmentedControl)
│ ▸ 已存篩選  Piotroski改  · 我的成長股  · +   │   ← SavedScreens (§6, load/rename/delete)
│ ── 條件符合  ( AND | OR ) ─────────────────  │   ← match toggle (shows when ≥2 conditions)
│ ┌ ROE            ≥  15 %      [>15][>20]  ✕ │   ← ConditionRow = factor · op · FilterValueInput
│ │ ●───────────○──────────  slider + presets │
│ ┌ 月營收YoY       >  20 %      [>10][>30]  ✕ │
│ ┌ 外資連買        ≥  5  日      🔒 sponsor    │   ← sponsor factor → mosaic teaser in builder
│ + 新增條件  (factor picker, grouped 技/基/動/籌) │   ← AddCondition (collapsible category groups)
└─────────────────────────────────────────────┘
```

- **Two sub-modes** (SegmentedControl, radius 10px): **訊號 Signals** = the approachable path — a
  grouped **checklist** of boolean catalog signals (均線多頭排列, RSI超賣, KD黃金交叉…), the newbie entry;
  **條件 Conditions** = the free-form numeric builder below. Both post to `run_screener`; ≥2 selected
  → the AND/OR toggle appears.
- **`ConditionRow`** = `factor label · operator · value`, in a `--surface-2` card (radius 12px):
  - **operator** select (`>` `≥` `<` `≤` `↔ between` `= ` `∈ in`) — numeric factors get comparison
    ops, enum factors (US region/sector) get `=`/`∈`.
  - **value** via **`FilterValueInput`** (the "1B problem" fix): a **labeled slider** bounded by the
    field's `[min,max]/step` + **one-click preset chips** (`>15` `>20`) + an exact-value box, showing
    **human units** (100億 / 5% / 12x) while reporting the raw number the API expects. `between` →
    dual-thumb range. Slider accent = `--accent`.
  - **remove** ✕ (`--text-muted` → `--down` on hover, 44px target).
- **AND / OR** = the `match` mode (`ConditionBuilder` posts `{conditions:[...], match}`); AND = must
  meet all, OR = any. Segmented, active `--accent-weak` tint + `--accent` text.
- **`+ 新增條件`** opens a collapsible **factor picker** grouped by family (技術/基本面/動能/籌碼 for TW;
  估值/成長/動能/分析師 for US) — the same `get_factor_catalog` groups, localized (zh/en).
- **Sponsor factors in the builder** (分點/官股/主動ETF): selectable, but the value input + any
  resulting column render the **馬賽克** lock chip for Free (frame/label sharp) — consistent with the
  rest of the app; entitled users get the live slider.
- **Live survivor count** in the header updates as conditions change (debounced `run_screener` count
  probe) — the same "符合 N 檔" feedback as the gate bar, `tabular-nums`, `aria-live=off` (churn).

---

## 5. Gate bar + Results table

### 5.1 Gate bar (`GateBar`) — hard elimination (stage ⓐ)

A compact bar above the table: **liquidity / price / 市值 sliders** + **exclude 處置/注意/全額交割**
toggle pills (defaults from `SCREENER_GATES`, [20](../../backend/modules/20-analytics-scoring.md) §5A).
Client sends `gate_overrides` to `run_screener`. Toggle pills radius 999px, active `--accent`; a live
survivor count ("通過 412 檔") sits inline, `tabular-nums`. **Liquidity is a gate, never a scored
factor** — the bar is visually separated from the builder to make that structural.

### 5.2 Results table (`ScreenerTable`) — TanStack, per-market columns

The ranked survivors. **TanStack Table** ([README §1](../README.md): don't hand-roll data grids),
sortable, sticky `thead`, `divide-y --border` rows, `tabular-nums` throughout, row → Stock Detail.
**Columns are per-market** (from `ScreenerMarketConfig.columns`, adapts Kestrel `config.ts`):

| Market | Default columns (beyond 名稱代號 + SectorChip) |
|--------|-----------------------------------------------|
| **TW** | 收盤 · 漲跌% (▲▼, `--up`/`--down`) · 成交量(張/萬) · 符合gate/preset chips · **factor breakdown bars** · sparkline · 多空 `ScoreBandDot` · 外資/投信連買 (sponsor→馬賽克) |
| **US** | Price ($) · Change% (▲▼) · PE · 市值(B) · EPS growth · 分析師目標價 (attributed) · sparkline |
| **ETF** | 收盤 · 漲跌% (▲▼) · 折溢價% (diverging, ± around 0) · AUM · 受益人數趨勢 arrow · 殖利率 · sparkline |

- **漲跌% / signed cells** → `--up`(漲 red)/`--down`(跌 green) **+ ▲▼ + sign**, never color-alone.
- **factor breakdown** = small `--chart-1..5`-tinted micro-bars per row (動能/趨勢/估值/品質/籌碼),
  mirroring the [多空 gauge §7.1](../design-system.md) — "shows the work", click → 智能摘要 tab.
- **符合 chips** = which gate/preset/conditions the row passed → `--accent` hairline chips.
- **sponsor columns** (分點/官股/主動ETF) → **馬賽克** for Free: header + label sharp, value blurred
  under `backdrop-blur` + diagonal texture + one 升級 CTA.
- **sparkline** = recent closes, `--accent` stroke, up/down-tinted fill (StockRow §7.8 convention).
- **inline `ChartWithDataSheet`** on row expand (optional) reuses the [§7.11](../design-system.md) primitive.

### 5.3 Rank control (stage ⓒ)

A segmented **profile** toggle (swing / position) + **sort** menu (composite / momentum / value /
quality / flow) + top-N picker → `GET /analytics/rankings?profile=&sort=&n=&screen=` (`get_top_stocks`).
The composite carries the **regime + over-extension caveats** ([20](../../backend/modules/20-analytics-scoring.md) §2b)
as a header note. This is the "AI 篩選前 N 名" — ranked **descriptively**.

---

## 6. Save / share a screen + Themes sub-tab

- **`SavedScreens`** panel (adapts Kestrel `screener/_shared.tsx`
  `SavedStrategiesPanel`): list / **load** / rename (✎) / update / delete (🗑) a user's saved condition
  sets, scoped per market. Save = name + `{conditions, match, gate_overrides}` via the
  `/analytics/screener/screens` CRUD (`save_screen`/`list_screens`/`delete_screen`); a **dirty**
  indicator (`--accent` dot) shows when the current builder diverges from the loaded screen ("另存新篩選").
- **Share** = a read-only link that reconstructs the builder state (URL-encoded conditions + market);
  the shared view is **non-trading** (a filtered list, not advice).
- **主題 Themes sub-tab** (Row-2) — LLM-discovered themes + 資金流向 (`ThemeCards`, `GET /analytics/themes`,
  Premium+). A chip → a **pre-filtered screen** (theme members loaded as a screen). Theme is a **tag,
  not a scored factor** ([20](../../backend/modules/20-analytics-scoring.md) §5C).

---

## 7. Visual design

Tokens/radius/spacing from [../design-system.md](../design-system.md) (`VISUAL_DENSITY:6`, card 16px /
input 10px / pill 999px / button 12px, 4px base, section gap 24–32px, card padding 20px). **Dark-first.**

**Layout (≥1280px).** Two regions on `--bg`: **left builder rail** 264px (`--surface`, `1px --border`
hairline, radius 16px) → **main column** = `FunnelMeter` + `GateBar` + rank control + `ScreenerTable`.
Row-1 market tabs + Row-2 mode (篩選/Themes) are **segmented pill rows** (§6.3), active `--accent`
underline with shared-layout glide (`layoutId`), `role="tablist"` + arrow-key nav, persisted per user.
768–1279px → table horizontally scrollable with **pinned 名稱代號 column**, builder → collapsible; <768px
→ hands to the mobile client (builder = bottom-sheet drawer, results = card list).

**FunnelMeter.** Three linked segments (`gates → setup → rank`) on `--surface`, width proportional to
survivor share; active stage `--accent`, passed stages `--accent-weak`; counts `--font-num` tabular.

**Builder rail.** Header: title 14px/600 + live count (`--font-num`, `--text-muted`). SegmentedControl
(訊號/條件) radius 10px. `ConditionRow` cards `--surface-2`, radius 12px, `divide-y`-free (own cards);
operator/exact-box inputs radius 10px `--surface` + `1px --border`, focus-visible `--accent` ring;
preset chips radius 999px (active `--accent-weak` + `--accent` text + `--accent` hairline, idle
`--border` + `--text-muted`); slider `accent-color:--accent`. `+ 新增條件` = `--accent` ghost row.
Sponsor rows → 馬賽克 value layer (frame sharp).

**Chips / pills / inputs (token table).**

| Element | Idle | Active/selected | Radius |
|---------|------|-----------------|--------|
| Market/mode tab | `--text-muted` | `--accent` underline + `--accent-weak` | pill row |
| Gate toggle pill | `--border` + `--text-muted` | `--accent` fill | 999px |
| Preset card | `--surface` + `1px --border` | `--accent` hairline + `--accent-weak` tint | 12px |
| Operator / exact box | `--surface` + `1px --border` | focus `--accent` ring | 10px (input) |
| Preset value chip | `--border` + `--text-muted` | `--accent-weak` + `--accent` text | 999px |
| 符合 gate/preset chip (table) | — | `--accent` hairline | 999px |

**Results table.** `--surface` card, sticky `thead` (`--surface-2`, `--text-muted` labels), rows
`divide-y --border`, hover → `--surface-2`, click → Stock Detail. **`tabular-nums` everywhere.** 漲跌% /
折溢價 cells `--up`/`--down` **+ ▲▼**; factor micro-bars `--chart-1..5`; categorical never signed.
Sort caret on header (`--accent` when active). **Header caveat pill: 「符合條件的清單，非推薦名單」**
(`--warn`-tinted, ⚠) + regime/over-extension note. Sponsor cols → 馬賽克.

**Full behavioural contract** (add/remove condition, AND/OR, run button idle→working→done, survivor-count
`aria-live`, validation, saved-screen dirty/name-validation, keyboard/focus) = [interaction-spec §2.12](../interaction-spec.md#212-screener-funnel--builder-gates-run-results).

**States** ([interaction-spec §4](../interaction-spec.md)). Loading = **shaped shimmer** table rows +
ghost builder rows (`.animate-shimmer`), never a bare spinner; narration "Miru 正在跑篩選…". Empty =
「無符合條件的個股，放寬篩選?」 with a "放寬 gates" chip (routes back to `GateBar`). Error = inline calm
retry; a source-health degrade → honest `<Caveat>`. Live = survivor count re-probes on gate/condition
change (`aria-live=off`, churn). **Reduced-motion honored** (tab glide snaps, no shimmer sweep).

**A11y.** Full keyboard: `Tab` through builder → gates → table; table is `role="table"` sortable via
`Enter` on header, arrow-key row nav, row `aria-current`; SegmentedControls `role="tablist"`;
focus-visible 2px `--accent` everywhere; 44px min targets (op selects, chips, ✕). 紅漲綠跌 cells carry
an `aria-label` with direction + value ("上漲 1.45%"). WCAG AA both themes.

---

## 8. Backend mapping (what's real · what's flagged ⚑)

| UI element | Tool / endpoint | Status |
|------------|-----------------|--------|
| Market tabs (TW/US/ETF) | `MarketProfile` set (module 19) | ✓ config-driven |
| Factor catalog / presets + `fields[]` | `get_factor_catalog(market)`, `get_screener_presets` · `GET /analytics/screener/factors?market=` | ✓ per-market `fields[]` descriptors |
| Gate bar | `run_screener {gate_overrides}` · `POST /analytics/screener/run` | ✓ |
| Named preset run | `run_screener {preset}` | ✓ |
| Custom condition run (AND/OR) + live count | `run_screener {market, conditions[], match, count_only?}` | ✓ contract defined |
| Rank survivors (top-N) | `get_top_stocks` · `GET /analytics/rankings` | ✓ |
| TW 籌碼 sponsor factors | `get_chip_flows`, `get_government_bank_flows` | ✓ sponsor→馬賽克 |
| US screening | `run_screener {market:"US"}` yfinance query | ✓ contract defined (US phased → `not_supported`) |
| ETF 折溢價/AUM/受益人 filters | `get_etf_nav_premium`, `get_etf_aum`, `get_etf_beneficiaries`, `get_etfs_holding_stock` | ✓ wired as ETF factors |
| Themes sub-tab | `get_themes` · `GET /analytics/themes` | ✓ Premium+ |
| Save / share screen | `save_screen`/`list_screens`/`delete_screen` · `/analytics/screener/screens` CRUD | ✓ endpoint defined |

## 9. Governance

A screen = **符合條件的清單, 非推薦名單** ([20](../../backend/modules/20-analytics-scoring.md) §7): header
caveat + regime/over-extension warnings; top-N is a **descriptive** composite rank, always shipping its
factor breakdown. **Liquidity is a gate, never a weighted factor.** Sponsor (分點/官股/主動ETF) factor
columns → 馬賽克 for Free. Analyst externals (US) attributed ("N analysts, as of <date>"), never
restated as ours. No 明牌/飆股/目標價/買賣 copy. 紅漲綠跌 + ▲▼ + tabular-nums throughout.
