# 了然 · Miru — Market dashboard (`/market` 大盤) — index

Overview + per-view index. Same non-negotiables as [stock-detail.md](stock-detail.md) §0 (紅漲綠跌+▲▼,
tabular-nums, `<Caveat>`, 馬賽克 for sponsor, dark-first). Adapts Kestrel market components; every block
maps to a real endpoint. **Each view has its own doc in [`market/`](market/).**

## 1. Navigation — two-row tabs (adapts Kestrel market `page.tsx`)

```
┌────────────────────────────────────────────────────────────────┐
│ 股票跑馬燈 (marquee, design-system §7.5)                          │
├────────────────────────────────────────────────────────────────┤
│  [ 台股 ]   美股    ETF                              🔍 搜尋      │  ← Row 1: MARKET tabs (config-driven)
├────────────────────────────────────────────────────────────────┤
│ [每日焦點] 熱力圖 籌碼 產業 新聞 處置 名人                        │  ← Row 2: VIEW sub-tabs (per-market)
└────────────────────────────────────────────────────────────────┘
```

- **Row 1 — Market tabs are config-driven, not hardcoded** — rendered from the registered
  **`MarketProfile` set** ([module 19](../backend/modules/19-data-access-router.md)). Today: `台股 (TW)`
  + `ETF` asset-class facet + `美股 (US, phased)`. **Adding JP/GB/… = a new MarketProfile + priority
  block + calendar rule-set — zero UI rewrite.** This is *why* the market layer is market/category-
  partitioned. Each market renders its own section; view sub-tabs are per-market (a market only shows
  views its adapters `supports()`).
- **Row 2 — View sub-tabs (per-market; TW shown):** persisted per user.

## 2. Views (each = its own doc)

| View | Doc | Backing (headline) |
|------|-----|--------------------|
| **每日焦點** (default) | [market/每日焦點.md](market/每日焦點.md) | index cards · intraday · breadth · 貢獻排行 · Top-N · 熱門股 · macro strip |
| 熱力圖 | [market/熱力圖.md](market/熱力圖.md) | 類股 treemap · 產業別漲跌 · 個股漲跌分佈 |
| 籌碼 | [market/籌碼.md](market/籌碼.md) | 三大法人 · 法人排行 · 融資券/維持率 · 外資/主力/官股🔒 |
| 產業 | [market/產業.md](market/產業.md) | 產業鏈資金流🔒 · 類股輪動 · 主題總覽 |
| 新聞 | [market/新聞.md](market/新聞.md) | 市場新聞 (即時/財經) · 名人事件 |
| 處置 | [market/處置.md](market/處置.md) | 處置王 board (風險/處置中/即將出關/自結發布) |
| 名人 | [market/名人.md](market/名人.md) | 名人事件 feed |
| **美股** (Row-1) | [market/美股.md](market/美股.md) | US indices/movers/economic — phased |
| **ETF** (Row-1) | [market/ETF.md](market/ETF.md) | 主動ETF 追蹤/操作日報🔒 · 折溢價 · 受益人數/AUM |

## 3. 每日焦點 layout (≥1280px, 12-col) — ASCII

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ 大盤概況            08/01 ● EOD   [切換 即時▸]                                     │
│ ┌ 加權 23,187 ▲188 +0.82% ┐ ┌ 櫃買 268.4 ▲1.2 +0.45% ┐ ┌ 市場氛圍 貪婪 F-G 68 ┐  │
│ 漲跌家數: 上漲▇▇▇1,024(紅) 平142 下跌▇▇587(綠) 漲停18 跌停4                        │
│ ┌ 類股熱力圖 (treemap) ┐ ┌ 資金流向 三大法人 ┐   ┌ 熱門股 ┐ ┌ 主題熱度🔒 ┐        │
│ ┌ 指數貢獻 台積電+112▇▇ ┐ ┌ 本週行事曆 ┐                                          │
│ ┌ 總經指標帶 S&P▲·Nasdaq▲·匯率·債·黃金·夜盤·F-G 68 ────────────────────────┐      │
└──────────────────────────────────────────────────────────────────────────────────┘
```
Responsive (README §7): 768–1279px → 2→1 col; <768px → mobile client. Every block = `ChartWithDataSheet`
(§7.11). Backend REST for these = [api-reference §4a-2](../backend/api-reference.md) (`/market/*`, `/derivatives/*`, `/macro`).

## 4. Governance
Every analytical block carries `<Caveat>` (non-trading / currentness); sponsor blocks (主題, 分點-level
flow, 主動ETF holdings) → 馬賽克 for Free; no 明牌/飆股/目標價/買賣 copy ([../brand.md](../brand.md) §5);
紅漲綠跌+▲▼ never color-alone. SectorChip system: [design-system §2](design-system.md) sector tokens.

## 5. Visual design (dashboard shell)

Tokens/radius/spacing from [design-system.md](design-system.md) (`VISUAL_DENSITY:6`, card 16px,
padding 20px, section gap 24px). All `ChartWithDataSheet` visuals: §5.1 below + [stock-detail.md §7](stock-detail.md).

- **Two-row tab bar.** Row 1 (market) + Row 2 (view) are **segmented pill rows** on `--surface`;
  active = `--accent` underline/pill with shared-layout glide (`layoutId`); horizontal-scroll on
  overflow; `role="tablist"` + arrow-key nav. Persisted per user (`kestrel_market_view_tab` pattern).
- **12-col grid.** Blocks are cards (bg `--surface`, `1px --border`, radius 16px, pad 20px, tinted
  shadow). 每日焦點: index cards span 4-col each; treemap/資金流 span 6; contribution/行事曆 span 6;
  macro strip full-width. 768–1279px → 2-col; <768px → mobile client.
- **Index cards** (`IndexCard`): 加權/櫃買 name (14px `--text-muted`) + big tabular value (24px
  `--font-num`) + **▲▼ + pts + %** in `--up`/`--down`; 市場氛圍 = `FearGreedGauge` radial (green→red
  sweep, needle eases on load, no re-anim on tick).
- **Breadth / contribution** = `net-bar`/`diverging-bar` around zero: 上漲 `--up` above, 下跌
  `--down` below, **always ▲▼ + count label** (never color-alone). tabular-nums; 家數 labels inline.
- **`SectorChip` / sector badges.** Muted-tint bg (sector token @ ~15% alpha) + solid-hue text +
  `1px` hairline of same hue, pill radius 999px, 12px label. Tokens: `--sector-semi/ai/finance/
  biotech/traditional/shipping/electronics/steel` ([design-system §2](design-system.md)). Category
  only — **never** colors a price.
- **States.** Loading = shaped shimmer (index-card ghosts + chart block + ghost rows); live index →
  flash `--up-weak`/`--down-weak`; sponsor blocks (主題/分點/主動ETF) → **馬賽克** (backdrop-blur +
  diagonal texture + lock chip). `<Caveat>` footer required on every block.

### 5.1 `ChartWithDataSheet` visual (shared across all market views)
ControlBar top-right: title + `ⓘ` (left), legend chips, `[日|週|月]` + range pills + `⤓` (right) —
all pills radius 999px, active `--accent`. Chart region: `--grid` gridlines, `--crosshair` dashed
follows pointer. Datasheet (展開歷史 ▾): **sticky thead**, `tabular-nums`, signed cells `--up`/`--down`
**+ ▲▼**, categorical series `--chart-1..5`, hovered/active row = `--surface-2` (`aria-current`),
crosshair ↔ row sync. Footer: `<Caveat>` + unit + `DataFreshnessBadge`.
