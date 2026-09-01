# 了然 · Miru — Design System (shared by web + mobile)

The concrete visual language behind [web/](README.md) and [mobile/](../mobile/README.md). This is
what makes the product **rich, modern, and beautiful** rather than just structurally correct.
Grounded in the anti-slop rules of the `design-taste-frontend` skill: **dark-first, one accent,
real depth (not neon glow), tabular numbers, no AI-purple, glass used with intent + fallback.**

> **Design read:** a modern **financial-analysis product** — calm, precise, trustworthy, with the
> warmth of the 藍鵲 companion. The reference feel is *Linear/Raycast structure × a fintech data
> surface × a touch of Taiwanese craft*. Dense-but-legible, never a Bloomberg cockpit, never a toy.

---

## 1. Dial reading

`DESIGN_VARIANCE: 5` · `MOTION_INTENSITY: 5` · `VISUAL_DENSITY: 6`
— restrained variance (trust), purposeful motion (feedback + streaming life, not decoration),
higher density than a marketing site (it's a data product) but with generous breathing room so it
never feels like a cockpit.

---

## 2. Color tokens (dark-first, one accent)

Semantic CSS variables set **once** at the root; light mode swaps values. **Off-black, never pure
`#000`; off-white, never pure `#fff`** (depth). One accent — a **teal-blue drawn from the 藍鵲**
(trust + clarity, and deliberately *not* AI-purple).

```css
:root[data-theme="dark"] {
  --bg:            #0B0E13;   /* off-black, cool */
  --surface:       #12161D;   /* cards / rails */
  --surface-2:     #1A1F28;   /* elevated / hover */
  --border:        #232A35;   /* hairlines */
  --text:          #E6EAF0;   /* primary */
  --text-muted:    #8A93A3;   /* secondary */
  --accent:        #2DD4BF;   /* 藍鵲 teal — links, active nav, focus */
  --accent-weak:   #123C39;   /* accent tint bg */
  /* TW market convention — RED UP / GREEN DOWN (§4) */
  --up:            #F0483E;   /* 漲 red */
  --up-weak:       #2A1512;
  --down:          #1FB574;   /* 跌 green (lifted for dark-bg contrast) */
  --down-weak:     #0E2A1E;
  --warn:          #F5A524;   /* caveats / 追高 / attention */
}
:root[data-theme="light"] {
  --bg:#F7F8FA; --surface:#FFFFFF; --surface-2:#F0F2F5; --border:#E2E6EC;
  --text:#0E1319; --text-muted:#5B6472; --accent:#0E9E8E; --accent-weak:#DCF5F1;
  --up:#D42A20; --up-weak:#FCE9E7; --down:#0E9E6B; --down-weak:#E3F6EE; --warn:#B26A00;
}
```

- **One accent, locked** across the whole app (no blue CTA appearing in one screen, teal in
  another). The up/down reds/greens are **market-data tokens**, not accents — they only color
  price/change, never chrome.
- **Depth via tinted elevation + hairlines**, not drop-shadows. When a shadow is needed, tint it to
  `--bg` hue at low alpha — never pure-black.

**Chart-series + chart-chrome tokens** (for `ChartWithDataSheet` §7.11 — multi-line/bar overlays).
Deliberately **distinct from `--up`/`--down`** (a series must never read as 漲/跌) and from `--accent`
(chrome only). Used for MA5/10/20…, 外資/投信/自營 lines, 毛利/營益/淨利率, holder tiers.
```css
:root[data-theme="dark"] {
  --chart-1:#6AA9FF; --chart-2:#C08CFF; --chart-3:#F5A524; --chart-4:#4FD1C5; --chart-5:#E06C9F;
  --chart-6:#93A3B8;     /* 6th categorical — muted slate, for the long MA240 overlay */
  --grid:#1A1F28;        /* gridlines = surface-2, low contrast */
  --crosshair:#8A93A3;   /* text-muted, dashed */
}
:root[data-theme="light"] {
  --chart-1:#3B6FD4; --chart-2:#7C5CE0; --chart-3:#B07D00; --chart-4:#0E9E8E; --chart-5:#C64C7E;
  --chart-6:#64748B;
  --grid:#E2E6EC; --crosshair:#5B6472;
}
```
- **Signed vs categorical rule:** `--up`/`--down` are reserved for *signed* series (net bars,
  折溢價, %change candles — always with ▲▼). `--chart-1..6` label *categorical* overlays (never signed);
  `--chart-6` is the extra slot for the 6-line MA set (MA5/10/20/60/120/**240**).
- **Sector-badge tokens** (category chips only — muted tint bg + solid-hue text, never price):
  `--sector-semi/ai/finance/biotech/traditional/shipping/electronics/steel` (per [market.md §5](market.md) `SectorChip`).

---

## 3. Typography (bilingual, tabular numbers)

```css
--font-sans:  "Noto Sans TC", "Inter", system-ui, sans-serif;   /* Traditional glyphs; NEVER Simplified fallback */
--font-num:   "IBM Plex Mono", ui-monospace, monospace;          /* tabular — all prices/%/scores */
```

| Role | Spec |
|---|---|
| Display (page title) | 28–32px, weight 600, `tracking-tight` |
| Section head | 18–20px, 600 |
| Body (CJK) | **16px, line-height 1.7** (CJK needs air; no inter-word spaces) |
| Numbers | `--font-num`, **`font-variant-numeric: tabular-nums`** everywhere — flicker-free on live ticks, columns align |
| Micro / caveat | 13px, `--text-muted` |

Never rely on weight alone for hierarchy (many TC weights render thin) — use size + color. Don't
break `台積電 2330` mid-token. Emphasis within a headline = bold/italic of the **same** family.

---

## 4. 紅漲綠跌 — the non-negotiable TW rule

**Red = up (漲), Green = down (跌)** — inverted from Western markets. A theme token (`--up`/`--down`),
applied **only** to price, %change, score bands, candlesticks. **Never color-alone** — always pair
with **▲ / ▼ + sign** for accessibility and for the ~8% who can't distinguish red/green. Get this
wrong and the app is unusable for a TW user — it is the single highest-stakes visual rule.

---

## 5. Shape, spacing, motion

- **Radius scale (locked):** `card 16px · input 10px · pill/chip 999px · button 12px`. One system,
  applied everywhere.
- **Spacing:** 4px base; section gap 24–32px; card padding 20px. Dense but breathing.
- **Motion (purposeful only):** spring, not linear — `type:spring, stiffness:120, damping:22`.
  - Streaming answer tokens fade+rise 4px (the "it's thinking" life).
  - Active nav indicator **morphs** between items (shared-layout, `layoutId`).
  - Score gauge needle eases to value on load; live price flashes `--up-weak`/`--down-weak` on tick.
  - Everything `> level 3` honors `prefers-reduced-motion` → instant/static (gacha, pet, gauge).
- **Icons:** Phosphor, one family, `weight="regular"` (1.5–2px), locked globally.

---

## 6. Navigation — modern, not the old flat tab bar

### 6.1 Mobile — floating glass island (raised accent center)

Not a flat full-width 5-cell bar. A **floating, rounded, blurred island** hovering above content
with a **raised accent center tab for Chat** and a **morphing active pill**:

```
        content scrolls behind, blurred under the bar
   ╭───────────────────────────────────────────────╮
   │   ⌂        ⌁       ╭─────╮      ▽       ⚙       │   ← floating glass island
   │  Home    Stock     │ ◉   │  Screener Settings   │     (rounded-2xl, backdrop-blur,
   │                    │Chat │                      │      1px inner border, sits 12px
   │  ▁▁▁ active pill morphs under the current tab   │      off the bottom edge)
   ╰────────────────────╰─────╯──────────────────────╯
              raised, --accent-filled, casts a soft tinted shadow
```

- **Glass:** `backdrop-blur(20px) saturate(160%)`, `--surface` at ~70% alpha, `1px --border` +
  `inset 0 1px 0 rgba(255,255,255,.06)` for an edge highlight. **Solid-fill fallback** under
  `prefers-reduced-transparency`.
- **Chat center tab** is a **raised circular accent button** (`--accent` fill, tinted shadow) — the
  emphasis slot + thumb home, unmistakably the product's heart.
- **Active state morphs** — a pill/underline slides between tabs via shared-layout animation
  (`layoutId`), label fades in only for the active tab (icon-only when inactive keeps it clean).
- **Hides on scroll-down, reveals on scroll-up** (more content, still one thumb-reach away).
- Order: **Home · Stock · ◉Chat · Screener · Settings** (Chat centered, [../mobile/](../mobile/README.md) §2).

### 6.2 Web — collapsible left rail with depth

Not a flat text list. An **elevated rail** (`--surface` on `--bg`, hairline divider) with:
- **Active item** = accent-tinted background pill (`--accent-weak`) + a **2px `--accent` left edge
  bar** + accent icon; a shared-layout indicator glides between items on navigate (`layoutId`).
- **Grouping by negative space + one hairline**, not boxes-in-boxes.
- Bottom of rail: the **ambient Sisi + 🔥streak** chip (compact, dismissible) and the gear.
- Top bar above the canvas is **sticky, glass** (blur over scrolling content) carrying the **股票搜尋
  command palette** (`搜尋股票 代號/名稱`, ⌘/Ctrl+K — searches *stocks*, not a question box; asking Miru
  is Chat), notifications bell, tier badge, account.

**Collapse mechanics** (adapts Kestrel `Sidebar.tsx` — `w-60 ↔ w-20`, `transition-all duration-300 ease-out`):

| | Expanded | Collapsed |
|---|---|---|
| Width | **240px** (`w-60`) — icon + zh label + en sublabel | **64–80px** (`w-16/20`) — icon only |
| Label | inline text | **hover tooltip** (`title=` / floating), 1 per item |
| Logo | full 了然·Miru wordmark | mark only, centered |
| Active | full pill + edge bar + label | icon tinted `--accent` + edge bar (no label) |
| Sisi chip | avatar + streak count | avatar only, centered |
| New-chat / primary CTA | full-width button + label | icon-only square button |

- **Toggle** = a chevron button in the rail header; **`transition-all duration-300 ease-out`** on
  width so the canvas reflows smoothly. **State persists per user** (localStorage + server profile).
- **Auto-collapse** at the 768–1279px breakpoint (tablet) → icon rail; **<768px** → the rail is
  replaced by the mobile bottom-tab island entirely (README §7).
- **Reduced-motion:** width snaps (no 300ms tween); tooltips + active states unchanged.
- **A11y:** `role="navigation"`; toggle has `aria-label` + `aria-expanded`; collapsed items keep an
  accessible name via `aria-label` even when the visible label is hidden; focus-visible ring
  (2px `--accent`) on every item.

### 6.3 In-page tab convention (category sub-tabs — the shared pattern)

Adapts Kestrel's market `page.tsx`. Data-dense pages organize by a **consistent tab hierarchy**, not
one long scroll:

- **Top tabs** = the primary partition (Market: 台股/美股/ETF · Stock Detail: 基本資料/產業/財務/籌碼/…).
- **Nested category sub-tabs** = within a top tab, group related views (e.g. 籌碼 → 外資/融資券/主力/官股;
  Market 台股 → 每日焦點/熱力圖/籌碼/產業/新聞/處置/名人). Rendered as a **segmented pill row** below
  the top tab bar; active = `--accent` underline/pill with a shared-layout glide (`layoutId`).
- **Persist the active sub-tab per user** (like `kestrel_market_view_tab`); horizontal-scroll on
  overflow; `role="tablist"` + `aria-selected` + arrow-key nav; each panel is an `<ErrorBoundary>`
  so one failing view never blanks the page.

Used by: Market ([market.md](market.md) §1), Stock Detail tabs + inner sub-tabs
([stock-detail.md](stock-detail.md)), Screener presets.

---

## 7. Signature components (the "awesome" surface)

### 7.1 多空 Score Gauge — the hero data-viz
Not a number in a box. A **radial arc gauge**, 偏空 → 中性 → 偏多 sweeping green→neutral→red (TW
convention), needle springing to value, the band label + numeric centered, and the **factor
breakdown as horizontal micro-bars** beneath (momentum / trend / value / quality / 籌碼) each tinted
+ labeled. Always shows the *work*, never the bare score ([20](../backend/modules/20-analytics-scoring.md) §7).

```
        偏多  72        ← big tabular number, red (漲) tint, band label above
      ╭─────────────╮
      │   ◜◝ arc ◜◝  │   radial arc, needle at 72, green→neutral→red sweep
      ╰─────────────╯
   動能 ▇▇▇▇▇▇▁▁  78     ← factor micro-bars, tabular values, each tinted
   趨勢 ▇▇▇▇▇▁▁▁  71
   估值 ▇▇▇▁▁▁▁▁  40  ← value guard low = 追高 warn tint
   品質 ▇▇▇▇▇▇▇▁  82
   籌碼 ▇▇▇▇▇▇▁▁  75
   ⚠ 情境參考，非投資建議 · 資料截至 08/01     ← required caveat footer
```

### 7.2 Stock card (rich card / watchlist row)
`名稱 + 代號` (no mid-token break) · big tabular price · **▲/▼ + %change in `--up`/`--down`** ·
inline sparkline (accent stroke, up/down-tinted fill) · optional score-band dot. Grouped by
`divide-y --border`, not individual card boxes (density without clutter).

### 7.3 Streaming chat
The `activity` narration is a **live, animated line** with the Sisi avatar subtly pulsing while it
works ("正在查 台積電 近 20 日籌碼…"); tokens fade+rise as they stream; tool steps collapse behind a
"顯示過程" disclosure; `rich_card`s (score gauge, stock card) render **inline** in the thread; the
`caveat` badge is a pinned chip on every analytical answer; `follow_up` chips are pill buttons below.

### 7.4 馬賽克 teaser (the upgrade moment)
**Blur the value, reveal the frame.** The 市場定位 headline stays sharp; the SWOT detail sits under a
`backdrop-blur` + a subtle diagonal-line texture + a centered **lock chip** and one calm CTA
("升級 Premium 看完整籌碼分析"). Feels like *seeing through frosted glass at something real*, not a
paywall slammed in the face. Same treatment across chat `tool.locked`, detail SWOT, top-N columns.

### 7.5 Stock marquee (股票跑馬燈) — the ticker strip
A horizontally scrolling quote ticker — a **genre convention** TW users expect. A thin strip that
rides **above the dashboard content** (web: under the top bar; mobile: under the top bar, above the
Home feed).

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 加權 23,187 ▲188 +0.82% · 台積電2330 1,050 ▲15 +1.45% · 0050 195.2 ▼0.30 … →│  ← seamless loop
└──────────────────────────────────────────────────────────────────────────┘
```

- **Content = the user's watchlist + key indices** (加權/櫃買) first, then their top holdings;
  falls back to major indices for logged-out / empty-watchlist. Each item: `名稱代號 · price · ▲▼ +chg%`.
- **Data:** the **demand-driven live-quote feed** ([../backend/modules/16-infrastructure.md](../backend/modules/16-infrastructure.md) §2a) for Premium
  (`price_intraday`, ~5s SSE), **EOD snapshot for Free** (`/quote`, [../backend/modules/12-multi-user-tiers.md](../backend/modules/12-multi-user-tiers.md)).
  The marquee is a *subscriber* to the same feed the watchlist uses — no extra polling.
- **Color = 紅漲綠跌 tokens + ▲▼** (never color-alone); **tabular numerals**; values update in place
  (flash `--up-weak`/`--down-weak` on tick) — the scroll never resets on a data update.
- **Motion:** seamless CSS marquee (duplicated track, `transform: translateX`, linear), **pauses on
  hover / touch**, and **honors `prefers-reduced-motion` → becomes a static, manually
  horizontally-scrollable strip** (no auto-scroll). This is the **one permitted marquee on the page**
  (skill: marquee-max-one).
- **Tap/click an item → Stock Detail.** Height ~32–36px, `--surface` bg, hairline bottom border.
- **Non-trading:** it's a *quote display*, not a signal; carries the delayed-snapshot currentness
  understanding of the feed (Free = EOD, Premium = ~5s delayed) — no targets, no buy/sell.
- **Dismissible / off-hours:** collapsible per user; outside market hours it shows last close (靜態),
  no animation.

### 7.7 Watchlist rail (右側自選欄) — drag-and-drop
A **right-side collapsible rail** (toggle from the top bar `☆`, persists across pages) — not a nav
route. Adapts Kestrel `WatchlistPanel.tsx`. Backed by the first-class watchlist backend
([../backend/modules/09-memory.md](../backend/modules/09-memory.md) + `watchlists`/`watchlist_items`
tables, [../backend/adr/0027-first-class-watchlist.md](../backend/adr/0027-first-class-watchlist.md)).

```
┌── ● Watchlist ─────────────── ✕ ─┐
│ [  TW  |  US  |  ETF  ]           │  ← market tabs (segmented)
│ ▾ 我的持股                    12  │  ← group (expand/collapse, count)
│  ⠿ ▮ 2330 台積電   1,050 ▲+1.4% │  ← StockRow (§7.8): grip · candle · code+name · price · ▲▼%
│  ⠿ ▮ 0050 元大50    195.2 ▼-0.2% │     hover → sparkline + ✕ remove; drag ⠿ to reorder
│ ▾ 觀察中                       5  │
│  ⠿ ▮ 2317 鴻海      203.0 ▲+0.5% │
│ + 新增分組                        │
│ ─────────────────────────────────│
│ 17 positions          拖曳排序    │  ← footer
└───────────────────────────────────┘
```

- **Market tabs** = `TW / US / ETF` (segmented control); groups scoped per tab. Backend models this
  as `market`×`asset_class` (not a 3-value enum): **TW**=`(TW,stock)` · **ETF**=`(TW,etf)` ·
  **US**=`(US,*)` ([ADR-0027](../backend/adr/0027-first-class-watchlist.md)).
- **Groups/sections** — named, expand/collapse, count badge; `+ 新增分組`; **drag-to-reorder** groups
  and items (lexorank ordering, backend §3). Endpoints: `/watchlists`, `/watchlists/{id}/items`.
- **Rows** = `StockRow` (§7.8). Live quotes via `/quote(/stream)` fan-out (same feed as marquee).
- **Empty group** → "尚無股票，點 ☆ 或問 Miru 加入"; empty tab → **seed with the preset below**.
- **Motion:** `@dnd-kit` sortable — grip handle `⠿`, drag row → 50% opacity; hover-reveal ✕ remove
  (`opacity-0 group-hover:opacity-100`). Reduced-motion → no drag transition, keyboard reorder
  (focus row, `Space` lift, `↑/↓`, `Space` drop, announced via aria-live).

**Default presets — copied verbatim from the Kestrel backend `DEFAULT_WATCHLISTS`**
(`kestrel-backend .../endpoints/kestrel/user.py:89`), **server-seeded when a user's list for a
market is empty** (backend ADR-0027 seeds these on first `GET /watchlists?market=`):

| Tab | Preset group | Symbols |
|-----|--------------|---------|
| **TW** | **半導體** | 2330 台積電 · 2454 聯發科 · 3711 日月光投控 · 2303 聯電 |
| **TW** | **權值股** | 2317 鴻海 · 2382 廣達 · 2881 富邦金 · 2882 國泰金 · 2412 中華電 |
| **US** | **US Tech** | NVDA · AAPL · TSLA · MSFT |
| **US** | **US Index** | SPY · QQQ · DIA |
| **ETF** | **台股 ETF** | 0050 元大台灣50 · 0056 元大高股息 · 00878 國泰永續高股息 · 00919 群益台灣精選高息 |
| **ETF** | **債券/海外 ETF** | 00679B 元大美債20年 · 00713 元大台灣高息低波 · 00757 統一FANG+ |

Presets are a **starter seed only** — fully editable/removable; a returning user's own groups
override them. Seeded server-side (single source of truth), not hardcoded in the client. US Index
(SPY/QQQ/DIA) is reserved until the multi-market US phase ships (module 19); the group renders with
its symbols but quote data shows "尚未支援" until then.

### 7.8 StockRow — category-aware default info
The atomic row used in the watchlist rail, market tables, peer lists, and screener results. Adapts
Kestrel `StockRowVisual.tsx` / `SortableStockRow`. **Every row shows a default info set; category
adds specifics.**

```
⠿  ▮  2330 台積電  [半導體]      1,050.00  ▲+15.0 +1.45%   ╱╲╱ spark   ✕
   │   │                        │          │              │
  grip candle  code+name+chip   price      ▲▼% (紅漲綠跌)  sparkline  hover-remove
```

**Default info (always, all categories):** grip handle · single **candlestick cell** (latest OHLC)
· `代號 名稱` (no mid-token break) · **`SectorChip`** (§PART5) · tabular price · **▲▼ + change +
%** in `--up`/`--down` · mini **sparkline** (recent closes) · hover ✕ remove + ☆ star state.
Data: `/quote(/stream)` + `/analytics/{symbol}/indicators` (spark) + `get_company_profile` (name/sector).

**Category-specific extras** (shown by asset kind — mirrors the per-asset-kind tab sets in
[stock-detail.md](stock-detail.md)):

| Category | Adds to the row / expanded state |
|----------|----------------------------------|
| **TW 個股** | 多空 `ScoreBandDot` (偏多/中性/偏空) · 月營收YoY chip · 法人連買 streak badge (sponsor→mosaic for 分點) |
| **TW ETF** | 折溢價 % · 殖利率 · 受益人數 trend arrow · (主動ETF: 操作日報 badge, sponsor→mosaic) |
| **US** | after-hours mark (later) · analyst 目標價 chip (attributed, yfinance) |
| **處置/注意** | a `--warn` flag chip (處置中 / 注意 / 全額交割) from `get_disposition_stocks` |

All extras degrade gracefully (absent → omitted, not a blank slot); sponsor extras render the
馬賽克 lock for Free; 多空/analyst data carry the non-trading + attributed framing (governance §1).

### 7.9 Data-rich component index (from the source→viz gap analysis)
Additional components that visualize catalog data not yet in the base set. ✓ = tool exists (build
now) · ⚑ = **backend tool/endpoint to be defined first** (see [../backend/data-catalog.md](../backend/data-catalog.md) + module 03).

| Component | Spec | Backing tool | Surfaces in |
|-----------|------|-------------|-------------|
| `IndustryFlowChart` 🔒 | 產業鏈資金流 in/out bars, 紅漲綠跌 | `get_industry_money_flow` | 產業分析 tab · dashboard |
| `EtfHoldersCard` | "被 N 支 ETF 持有": 比重/張數 +增減, ETF-type filter | `get_etfs_holding_stock` | 基本資料/籌碼 |
| `RealtimeTab` 🔒 | best-5 bid/ask ladder + tick/分K + 當沖率, live-flash | `get_realtime_snapshot`, `get_intraday_kbars` | 即時 tab |
| `MaterialAnnouncementsCard` | classified 重大訊息+董事會決議, sentiment chips | `get_material_announcements`, `get_board_resolutions` | 基本資料 |
| `AnalystRatings/Estimates/Growth/EarningsHistory` | 目標價/評等 · EPS/營收估計 · 成長 · 財報意外史 · 升降評 — **attributed** | `get_analyst_consensus` | 研究圖表 |
| `EsgRiskCard` | Sustainalytics risk, coverage-gated (hidden when null) | `get_esg_score` | 基本資料/產業 |
| `FigureEventsFeed` | 名人 avatar + linked events, non-analysis framed | `get_figure_events` | 相關新聞 · dashboard |
| `InstitutionalRanking` | top net-buy/sell stocks, 紅漲綠跌 | whole-market 三大法人 | dashboard |
| `MarketNews` | news feed w/ **即時新聞 / 財經新聞 segmented tabs** (fast RSS vs curated) + sentiment | `get_stock_news?category=realtime\|finance` | dashboard · 相關新聞 tab |
| `DispositionBoard` | 4-bucket 處置王 (風險/處置中/即將出關/自結發布) + 最快N日 projection, `--warn` | `get_disposition_stocks`/`_forecast`, `get_attention_stocks` | new 處置 board (Market Row-2 view, 台股) |
| `DerivativesPanel` ⚑ | 台指期 OI · PC-ratio dial · VIX line · 期權三大法人 · 大額交易人 — non-trading | §2.4 tools (⚑ TBD) | new 衍生性 screen |
| `MacroBoard` ⚑ | 景氣對策信號 燈號 + rates/FX/commodities/Fear-Greed history | `/macro/*` ⚑, `TaiwanBusinessIndicator` ⚑ | new 總經 screen |
| `MarginMaintenanceGauge` ⚑ | 大盤融資維持率 risk gauge, `--warn` bands; **needle eases on load / on new fetch, never re-anims on a tick; reduced-motion → placed instantly** (gauge rule, interaction-spec §2.3) | `TaiwanTotalExchangeMarginMaintenance` ⚑ | dashboard |
| `FearGreedGauge` | 市場氛圍 radial (恐懼→貪婪, green→red sweep) + labeled ends (貪婪/恐懼) + numeric — never color-alone; needle eases on load only | `get_market_context` sentiment | 每日焦點 · dashboard |
| `AssetHeader` | Stock Detail sticky header: 代號名稱 · price · ▲▼% · 多空 `ScoreBandDot` · ☆ · 問 Miru; live tick-flash (band pill no-re-anim-on-tick) — contract interaction-spec §2.15 | `/quote` + `/analytics/{s}/score` | stock-detail (all tabs) |
| **`ChartWithDataSheet`** | **the analytical primitive** (§7.11) — chart + synced datasheet all views compose | — (composes endpoints) | everywhere |
| `KLineChart` | candle + MA5/10/20/60/120/240 + volume/KD/MACD/RSI/布林 panes + 日週月·range TF + 還原股價 + OHLCV sheet | `/analytics/{s}/indicators?set=…` + price history | 技術分析 |
| `InstitutionalFlowSheet` | 三大法人 買賣超 net-bar + price overlay + daily 外資/投信/自營 sheet | 三大法人 total | 籌碼分析›法人 |
| `MarginChangeSheet` | 融資/融券 增減 net-bar + 餘額/資券比 + history sheet | margin data | 籌碼分析›融資券 |
| `DispersionTrend` | 大戶/中實戶/散戶 人數 lines + 增減 sheet | `get_shareholder_dispersion` | 籌碼分析›股權分散 |
| `DividendPolicy` | 現金/股票股利 grouped bar + 殖利率/配息率 sheet | `get_company_profile`/`get_dividend_schedule` | 財務分析›股利 |
| `RevenueTrend` | 月營收 bar + YoY% dual-line + MoM/累計 sheet | `get_company_profile` | 財務分析›營收 |
| `ProfitabilityTrend` | 三率 lines + EPS bar + quarterly sheet | `get_company_profile` | 財務分析›獲利 |
| `CashFlowChart` | 營業/投資/融資 CF net-bar + 自由現金流 line + sheet | `get_company_profile` | 財務分析›現金流 |
| `EtfPremiumHistory` | 折溢價% diverging-bar + 股價/淨值 dual-line + sheet | `get_etf_nav_premium` | ETF›折溢價 |
| `ActiveEtfDaily` 🔒 | 操作日報: 資金流入/出 cards + 交易明細 sortable table + 跨ETF 買賣統計 | `get_active_etf_report`, `get_etf_money_flow` | ETF›持股/操作 |
| `IndexContributionTable` | 正/負貢獻點數 two-column diverging ranked table | `/market/index-contribution` | dashboard |
| `SectorBar` | 產業別漲跌 bar + 上市/上櫃 toggle + sheet | `/market/sectors` | dashboard 類股 |
| `AdvanceDeclineHistogram` | 漲跌分佈 bucket histogram + 家數 + sheet | `/market/advance-decline` | dashboard breadth |
| `IndexIntraday` | 加權/櫃買 intraday line + volume + 即時/貢獻/K線/籌碼/外資 sub-tabs | `/market/overview` + `/quote(/stream)` | dashboard top |
| `StrengthRanking` | 強勢股 / 大戶加碼股 ranked lists + market + period tabs | `get_top_stocks`, `get_shareholder_dispersion` | dashboard 熱門股 |

**New screens** (beyond the current nav): **衍生性/期權** (`DerivativesPanel`, ⚑ needs tools) ·
**總經/Macro** (`MacroBoard`, ⚑) · **處置王 board** (Market Row-2 view 台股, tools exist) · **名人事件/主題
discovery** (`FigureEventsFeed`, tool exists). All non-trading, 紅漲綠跌, sponsor→mosaic, dark-first.

**Deliberately NOT visualized:** PTT Beauty/Gossiping buzz (`get_trending_buzz`) — mascot/whimsy only,
never analysis; raw 分點/tick dumps (summarized top-5 only, mosaic); insider names (de-identified risk
flag, not naming-and-shaming); order-book depth beyond the ~5s snapshot (out of scope). **Out of
scope — no data source**: 股東會紀念品 gift tracker (needs a new backend scraper; flagged, product
decision). *(Backtest/回測 explicitly dropped — not in current scope.)*

### 7.11 `ChartWithDataSheet` — the analytical primitive
Every data-rich view (K線, 三大法人, 折溢價, 財務, 大盤 breadth…) is one instance of this shell:
**a chart region on top + a synced scrollable data sheet of the *same* series below, one shared
timeframe control, one caveat footer.** Single fetch feeds both (one source of truth). See per-view
tables in [stock-detail.md](stock-detail.md) + [market.md](market.md).

```
┌ {title} ⓘ                    {legend chips}   [日|週|月] [1M 3M 6M 1Y YTD 5Y] [⤓]│ ← shared ControlBar
│ CHART  (candle | net-bar | diverging-bar | line | dual-line | histogram)         │
│   crosshair ┆ follows pointer → highlights same date-row in the sheet            │
├─ 展開歷史 ▾ ─────────────────────────────────────────────────────────────────────┤
│ 日期      欄1       欄2      買賣超      ← sticky thead, tabular-nums, virtualized  │
│ 08/01    ▲+12,450  ▲+2,180  ▲+14,290    ← 紅漲綠跌+▲▼, hovered row = crosshair sync │
├──────────────────────────────────────────────────────────────────────────────────┤
│ ⚠ {non-trading caveat} · 單位:{張/億/%} · 資料截至 08/01           [Free:🔒馬賽克]  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- **Chart kinds:** `candle` (lightweight-charts: candle + volume/KD/MACD/RSI panes + MA overlays) ·
  `net-bar` (signed, 紅漲綠跌 above/below zero) · `diverging-bar` (± around center: 折溢價/貢獻/breadth) ·
  `line` (multi-series, `--chart-1..5`) · `dual-line` (left+right axis, e.g. 營收+YoY%) · `histogram`
  (漲跌分佈 buckets). Non-candle = recharts / the `NetBarsChart`+`ChartFrame` SVG pattern.
- **Rules (all non-negotiable):** signed cells `--up`/`--down` **+ ▲▼**, never color-alone; categorical
  series `--chart-1..5`; **tabular-nums** everywhere; **crosshair ↔ sheet-row sync** (hover either,
  keyboard ←/→ chart, ↑/↓ sheet, one `activeIndex`); **展開/收合** height tween; **timeframe drives
  both** (single refetch, persisted in URL); **`⤓` CSV export** (Premium, hidden when sponsor-masked);
  **loading = shaped skeleton** (chart block + ghost rows), never spinner; **reduced-motion** →
  crosshair snaps, no ease; **sponsor → 馬賽克** on value cells (date/label columns stay sharp);
  **required `<Caveat>` footer** (non-trading on analytical, currentness on quote/market) + unit +
  `DataFreshnessBadge`.
- **A11y:** `role="figure"` chart + labelled series; datasheet `role="table"`, active row `aria-current`;
  a visually-hidden data-table fallback gives SR users full access to the series.

### 7.10 Sisi companion
A small animated 藍鵲 avatar in a **dedicated corner region** (chat) — idle breathing, reacts to
`pet.reaction` (thinking / done / a watched symbol moved), celebrates `pet.leveled`. **Visually
separated from the analysis area** so mascot warmth never gets mistaken for advice
([21](../backend/modules/21-pet-gamification.md) §5). Reduced-motion → static.

---

## 8. What we deliberately avoid (anti-slop, from the skill)

No AI-purple gradients or neon outer-glows. No pure black/white. No generic glassmorphism on
*everything* (glass is for the nav island + top bar + teaser only, always with a solid fallback). No
three-equal-cards feature rows. No emoji in product chrome (Phosphor icons instead). No hand-rolled
SVG icons. Motion is motivated (feedback / streaming life / state change) — never decoration.

> **Explicit exception — `/market` page cards.** Every card on the Market dashboard is wrapped in
> an animated rainbow-gradient conic border (`.market-rainbow-border`, `RainbowBorderCard`) — a
> deliberate visual-identity choice scoped to that one page, made and confirmed with the user
> despite conflicting with the anti-gradient rule above. Do not extend this treatment to any other
> page without the same explicit confirmation.

---

## 9. Full states (never ship happy-path only)
- **Loading = narration + skeletons** shaped like the final layout ("Miru 正在看盤面…"), never a bare spinner.
- **Empty:** watchlist empty → route to Chat ("問我任何台股問題"); no chats → prompt-starter chips.
- **Error:** inline, calm, retryable; a source-health degrade surfaces as an honest caveat, not a crash.
- **Live:** price ticks flash the up/down-weak bg; SSE reconnect is invisible (`Last-Event-ID`).

All of §2–§9 apply to **both** web and mobile — one token system, one component language, two shells.
