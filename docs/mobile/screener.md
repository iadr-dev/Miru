# 選股 / Screener (tab 4)

The mobile screener — **promoted to a primary tab** (Home·Stock·Chat·**Screener**·Settings). The
web's TanStack funnel table compresses into a **preset card grid → ranked card list**, with
**bottom-sheet condition/gate filters** (no wide tables on a phone). Parent index:
[README.md](README.md) §2. UX rationale: [ux-foundation.md](ux-foundation.md). Behaviour/state:
[interaction-spec.md](interaction-spec.md). Web parity (compress, don't drop):
the web app.

> **Governance (load-bearing, [20](../backend/modules/20-analytics-scoring.md) §7):** a result is a
> **filtered list matching criteria** — 「符合條件的清單，非推薦名單」 — never a buy list. **Liquidity is a
> gate, never a weighted factor.** Sponsor (分點/官股/主動ETF) factors → **馬賽克** for Free. 紅漲綠跌 +
> ▲▼ + aria-label (never color-alone) · tabular-nums · dark-first · zh-TW first · no 明牌/飆股/目標價/
> 買賣 copy · Backtest/回測 out of scope.

## Components → backend

| Element | Component | Endpoint |
|---------|-----------|----------|
| Market tabs (TW/US/ETF) | segmented pill row (`MarketProfile`) | [19](../backend/modules/19-data-access-router.md) |
| Preset card grid | `PresetLibrary` cards | `get_screener_presets` / `GET /analytics/screener/factors?market=` |
| Condition/gate filters | **bottom-sheet** builder (detented) | `POST /analytics/screener/run {conditions[],match,gate_overrides,count_only}` |
| Ranked result | `ResultCard` list (**not** a table) | `get_top_stocks` · `GET /analytics/rankings` |
| Themes (discovery) | `ThemeCards` 🔒 | `GET /analytics/themes` (Premium+) |

## Layout (preset grid → ranked card list; filters in a bottom-sheet)

```
┌───────────────────────────────────────┐
│ 選股   [ 台股 ]  美股  ETF        主題🔒│ ← market tabs + Themes (Row-2)
├───────────────────────────────────────┤
│ 通過 47 檔   [ ⚙ 篩選 ]  [ ↕ 排序 ]     │ ← FunnelMeter count + sheet triggers
├───────────────────────────────────────┤
│ 選一個策略                              │ ← PresetLibrary card grid (2-col)
│ ┌ Minervini 趨勢 ▲┐ ┌ 月營收創新高 ▲ ┐ │ ← preset cards, directional edge bar
│ │ 8 條件 · 趨勢     │ │ 成長            │ │
│ └───────────────────┘ └────────────────┘ │
│ ┌ Piotroski F≥7   ┐ ┌ 投信連N買  ▲   ┐ │
│ └───────────────────┘ └────────────────┘ │
├───────────────────────────────────────┤
│ ⚠ 符合條件的清單，非推薦名單            │ ← required header caveat
│ ── 排名結果 (ranked card list) ────────│
│ ┌ 1  2379 瑞昱 [半導體]  ▲+2.1%       ┐ │ ← ResultCard: rank · 代號名稱 · ▲▼%
│ │ 動能▇▇▇ 趨勢▇▇ 品質▇ 籌碼🔒░  [偏多74]│ │   factor bars + 多空 dot + gate chips
│ │ ✓流動性 ✓市值  ╱╲╱ spark             │ │
│ └──────────────────────────────────────┘ │
│ ┌ 2  3661 世芯-KY …                    ┐ │
└───────────────────────────────────────┘
        ⌂  ⌁  ◉Chat  ▽  ⚙
     Home Stock Chat Screener Settings   (Screener = tab 4)
```

### Filter bottom-sheet (drag-snap detents — the mobile ConditionBuilder + GateBar)

```
        ╭───── drag handle ─────╮   ← detents: peek (count only) · half · full
        │ 篩選條件        47 檔 │
        │ [ 訊號 | 條件 ]        │   ← sub-mode segmented
        │ ⓐ 硬篩 GATES           │
        │  流動性 ●───○  市值 ●─○ │   ← gate sliders (liquidity = gate, not a factor)
        │  排除 [處置][注意][全額] │
        │ ⓑ 條件 (AND | OR)       │
        │  ROE      ≥ 15% [>15][>20]│  ← ConditionRow: factor·op·FilterValueInput
        │  月營收YoY > 20%         │
        │  外資連買 ≥ 5日  🔒 sponsor│  ← sponsor factor → 馬賽克 in the sheet
        │  + 新增條件              │
        │ [ 重設 ]   [ 套用 47 檔 ]│   ← apply CTA (live count)
        ╰────────────────────────╯
```

## Visual design

Tokens from the web app (`VISUAL_DENSITY:6`, card 16px / input
10px / pill 999px, 4px base). Component semantics = the web screener page
(`PresetLibrary`, `ConditionBuilder`, `GateBar`, `FunnelMeter`) recomposed for touch. **Dark-first,
one column, 2026 mobile bar.**

- **Frame.** `100dvh` on `--bg`; **glass top bar** with **market tabs** (`台股/美股/ETF` from
  `MarketProfile`, segmented `--accent` underline + shared-layout glide, per-user persisted) + **主題
  Themes 🔒** (Row-2 mode). Bottom floating island present (this is a tab).
- **FunnelMeter (compact).** A thin `通過 N 檔` count bar under the tabs (`--font-num` tabular, active
  stage `--accent`) — the mobile take on the web's three-segment meter; the full `2000 → 412 → 47`
  breakdown lives inside the filter sheet.
- **PresetLibrary card grid** = 2-col `--surface` cards (radius 12px, `1px --border`): preset name +
  one-line 描述 + a **2px directional left-edge bar** (`--up` bull / `--down` bear / none) + matching
  ▲/▼ glyph — a subtle 紅漲綠跌 cue, **never a recommendation**. Category chip row + search above.
  Tap a card → **loads its conditions into the filter sheet** (see-and-tweak, not a black box);
  active = `--accent` hairline + `--accent-weak` tint + `aria-pressed`.
- **Filter bottom-sheet** (the mobile `ConditionBuilder` + `GateBar`) — a **drag-snap sheet with
  detents** (peek → half → full), triggered by `⚙ 篩選`. **ⓐ Gates** (liquidity/price/市值 sliders +
  exclude 處置/注意/全額 toggle pills) are visually separated at the top to make structural that
  **liquidity is a gate, never a scored factor**. **ⓑ Conditions**: 訊號/條件 sub-mode (SegmentedControl);
  `ConditionRow` = factor · operator (`> ≥ < ≤ ↔ = ∈`) · **`FilterValueInput`** (labeled slider bounded
  by the field's min/max/step + one-tap preset chips + exact box, human units 100億/5%/12x); AND/OR
  `match` appears at ≥2 conditions. **Sponsor factors** selectable but value + resulting column render
  the **馬賽克** lock for Free. `套用 N 檔` CTA shows the live survivor count (debounced `count_only`
  probe, `aria-live=off`). Drag-to-dismiss; reduced-motion → sheet appears instantly (no slide).
- **Sort bottom-sheet** (`↕ 排序`) — profile (swing/position) + sort (composite/momentum/value/quality/
  flow) + top-N; posts `GET /analytics/rankings?profile=&sort=&n=`; the composite carries the regime +
  over-extension caveat as a note.
- **Ranked `ResultCard` list** (**the card-list compression of the web TanStack table**) — one
  `--surface` card per survivor: **rank #** · `代號 名稱` (no mid-token break) + `SectorChip` · **▲▼ +
  漲跌%** in `--up`/`--down` · 收盤/成交量 tabular · **`--chart-1..5` factor micro-bars** (動能/趨勢/估值/
  品質/籌碼, mirroring the 多空 gauge — shows the work) · **符合 gate/preset chips** (`--accent` hairline)
  · 多空 `ScoreBandDot` · sparkline · sponsor factor (分點/官股/主動ETF) → **馬賽克** (label sharp,
  value blurred, one 升級 CTA). Per-market column emphasis follows the web (TW 籌碼 / US 分析師目標價
  attributed / ETF 折溢價%±). Tap a card → push [Stock Detail](stock-detail.md).
- **主題 Themes** (Premium) → `ThemeCards`; a chip loads theme members as a pre-filtered screen. Theme
  is a **tag, not a scored factor**.

### States ([interaction-spec.md](interaction-spec.md))
- **Loading** = shaped shimmer `ResultCard`s + narration "Miru 正在跑篩選…"; never a bare spinner.
- **Empty** = 「無符合條件的個股，放寬篩選?」 with a **放寬 gates** chip that reopens the sheet at the
  gate detent; cold-start (no preset picked) → the preset grid **is** the empty state (pick to begin).
- **Error** = inline calm retry; source-health degrade → honest `<Caveat>`. **US phased** → factors
  degrade to 「尚未支援」 (module 19).
- **Reduced-motion** → tab glide + sheet slide snap, no shimmer sweep.

## A11y
Full keyboard/SR: market tabs `role="tablist"` + arrow nav; result cards focusable, factor bars have
text values, 漲跌% carries an `aria-label` with direction + value ("上漲 2.1%"); sheet is a modal
dialog (focus-trapped, `Esc`/drag dismiss, focus returns to `⚙ 篩選`); ≥44px targets. WCAG AA both
themes.

## Governance
A screen = **符合條件的清單，非推薦名單**: header caveat + regime/over-extension note; top-N is a
**descriptive** composite always shipping its factor breakdown. **Liquidity is a gate, never a
weighted factor.** Sponsor factor columns → 馬賽克 for Free. US analyst externals attributed. No
明牌/飆股/目標價/買賣 copy ([20](../backend/modules/20-analytics-scoring.md) §7, [../brand.md](../brand.md) §5).
