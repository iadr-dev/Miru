# 個股 / Stock Detail (full-screen pushed view — not a tab)

The compression of the web workbench. Deep-linked from Home/Watchlist/Chat/Calendar cards, pushed
full-screen (back-gesture to dismiss), **not** a bottom-tab. The web's 8-tab detail collapses into a
single **scrollable section stack**. Parent index: [README.md](README.md) §2. UX rationale:
[ux-foundation.md](ux-foundation.md). Behaviour/state: [interaction-spec.md](interaction-spec.md).
Web parity (compress, don't drop): [../web/stock-detail.md](../web/stock-detail.md) +
[../web/stock-detail/](../web/stock-detail/README.md) per-tab docs.

> Non-negotiables on every section: 紅漲綠跌 `--up`(漲)/`--down`(跌) **never color-alone → ▲▼ + sign +
> aria-label** · tabular-nums · non-trading `<Caveat>` on every analytical card · 馬賽克 on sponsor
> data (分點/官股/主動ETF/即時) · `--accent` chrome-only · dark-first · zh-TW first. Score = band +
> numeric + **factor breakdown**, never the bare number. No 明牌/飆股/目標價/買賣 copy.

## Section stack → backend (web 8 tabs → scrollable sections)

| # | Section | Web tab source | Backend |
|---|---------|----------------|---------|
| — | AssetHeader (sticky) | [../web/stock-detail.md](../web/stock-detail.md) §1 | `get_company_profile` · `/quote(/stream)` · `/analytics/{s}/score` |
| 1 | 多空 score gauge + factor bars | [智能摘要](../web/stock-detail/智能摘要.md) | `GET /analytics/{s}/score?profile=` |
| 2 | 技術 (single chart + indicator chips) | [技術分析](../web/stock-detail/技術分析.md) | `/analytics/{s}/indicators?set=…` + price history |
| 3 | 智能摘要 / SWOT (Premium teaser) | [智能摘要](../web/stock-detail/智能摘要.md) | `GET /analytics/{s}/summary` (Premium+) |
| 4 | 事件 (除權息/法說/股東會) | [研究圖表](../web/stock-detail/研究圖表.md) | `get_calendar_events?symbol=`, `get_dividend_schedule` |
| 5 | 籌碼 (法人/融資券; 分點🔒) | [籌碼分析](../web/stock-detail/籌碼分析.md) | 三大法人 total · `get_chip_flows` 🔒 |
| 6 | 新聞 (即時/財經) | [相關新聞](../web/stock-detail/相關新聞.md) | `get_stock_news?category=realtime\|finance` |

*Folded/deferred vs web: 基本資料/產業分析/財務分析/研究圖表 depth and the 即時 ladder collapse into the
sections above or open on demand — see the ⚑ tradeoff in README.*

## Layout (section stack, one scroll)

```
┌───────────────────────────────────────┐
│ ‹  台積電 2330 [半導體]      ☆   ⋯     │ ← sticky AssetHeader (glass, shrinks on scroll)
│ 1,050.00 ▲15.00 +1.45%  [偏多 72]      │   price · ▲▼% · 多空 band pill · ● 即時~5s
├───────────────────────────────────────┤
│ ① 多空評分                              │
│   偏多 72  ◜◝ arc ◜◝                    │ ← radial gauge, green→neutral→red sweep
│   動能▇▇▇78 趨勢▇▇71 估值▇40⚠ 品質▇82  │   factor micro-bars (always shown)
│   [swing|position]  ⚠情境參考,非建議     │
├───────────────────────────────────────┤
│ ② 技術  [MA][KD][MACD][布林] [日|週|月] │ ← single chart + indicator chips + TF
│   ╱╲╱╲ candle + volume   展開歷史 ▾     │
├───────────────────────────────────────┤
│ ③ 智能摘要  市場定位: 晶圓代工龍頭…      │ ← headline SHARP
│   ┌ SWOT ░░░░ 🔒 升級看完整 SWOT ░░░ ┐  │ ← Premium teaser (馬賽克)
├───────────────────────────────────────┤
│ ④ 事件  🔴除息08/04 · 🗣法說08/12 → cal │
│ ⑤ 籌碼  外資▲+12,450 連8買 · 分點🔒░▓░  │
│ ⑥ 新聞  [即時|財經] · 標題…             │
└───────────────────────────────────────┘
                              ╭─────────╮
                              │ 💬 問Miru│ ← floating button, seeds chat with symbol
                              ╰─────────╯
```

## Visual design

Tokens from [../web/design-system.md](../web/design-system.md); gauge = §7.1, `ChartWithDataSheet` =
§7.11, `AssetHeader` = §7.9. **Dark-first, one-column scrollable stack, 2026 mobile bar.**

- **Pushed view, not a tab.** Enters as a full-screen push (back-swipe / `‹` to dismiss); the bottom
  tab island is hidden here (this is a drill-down). A **jump-to-section** chip row can pin under the
  header for long stacks (anchors, active `--accent`) — optional, thumb-reachable.
- **AssetHeader** (sticky, glass, **collapses on scroll** to a compact one-line bar): `名稱 代號` (no
  mid-token break) + `SectorChip` · big tabular price 28px `--font-num` · **▲▼ + chg + %** in
  `--up`/`--down` · 多空 band pill `[偏多 72]` (tap → scroll to gauge) · ☆ star (→ watchlist) · `⋯`
  (分享/加入分組) · currentness dot (`● 即時~5s` Premium / `● EOD` Free). Live price flashes
  `--up-weak`/`--down-weak`; band pill **never re-animates on a tick**.
- **Sections** = full-width `--surface` cards (`1px --border`, radius 16px, pad 20px, tinted shadow),
  24px gap, each with an 18px/600 head. Bento grouping where it aids glance (e.g. 事件 + 籌碼 as paired
  compact cards). **No multi-panel** — the web's side-by-side workbench is a vertical read here.
- **① 多空 gauge** — radial arc (偏空→中性→偏多, **green→neutral→red** 紅漲綠跌 sweep), needle springs
  to value on load (**no re-anim on tick**, reduced-motion → static), big band label + numeric
  centered; **factor micro-bars** (動能/趨勢/估值/品質/籌碼) beneath, each `--chart`-tinted + tabular,
  low 估值 → `--warn` (追高 caution); **profile toggle** swing/position (segmented `--accent`); regime +
  over-extension caveat inline. 籌碼 factor's 分點 sub-signal masked for Free → 「籌碼因子部分未解鎖」
  disclosure (composite recomputed, never silently degraded).
- **② 技術** — **one** `ChartWithDataSheet` `candle` instance (the mobile compression of the web's
  full K線 workbench): candles 紅漲綠跌 bodies + volume subpane; **indicator chips** (MA/KD/MACD/RSI/
  布林, 999px pills active `--accent`) toggle overlays/subpanes; TF pills `[日|週|月]` + `1M 3M 6M 1Y`.
  MA lines use `--chart-1..6` (categorical, never signed). **展開歷史 ▾** reveals the datasheet
  (`日期·開·高·低·收·漲跌%·量`, sticky thead, tabular-nums, crosshair↔row sync via drag/keyboard).
- **③ 智能摘要 / SWOT** — 市場定位 headline stays **sharp** (16px/1.7 CJK); SWOT 4-quadrant grid; for
  Free the **SWOT body under 馬賽克** (backdrop-blur + diagonal texture + lock chip + one 升級 CTA),
  headline + frame sharp. Required "AI-generated summary of data, not advice" + currentness footer.
- **④ 事件** — compact list of upcoming 除權息/法說/股東會 (deep-links to the [calendar surface](calendar.md)); state
  pills as on the calendar surface. **⑤ 籌碼** — 法人 net-bar summary (外資/投信/自營, 紅漲綠跌 + ▲▼); 分點
  (sponsor) → **馬賽克** (券商 label sharp, values blurred). **⑥ 新聞** — 即時/財經 segmented sub-tab,
  headline list + sentiment chip, source-attributed.
- **Floating 「問 Miru」 button** (FAB, `--accent` fill, tinted shadow, ≥56px, bottom-right, thumb
  zone) — seeds a Chat turn pre-loaded with the symbol ("問我關於 台積電 2330"). Hides on scroll-down,
  reveals on scroll-up (like the island). Reduced-motion → no hide/reveal.
- **Gestures.** Pull-to-refresh re-fetches quote + score; horizontal swipe **within** the chart pans
  the timeframe (doesn't navigate away — back-gesture reserved for dismiss).

### States
- **Loading** = shaped shimmer per section (header ghost + gauge block + chart block + ghost rows) +
  narration on the analytical sections ("Miru 正在算分…"); never a bare spinner.
- **Empty** = a section with no data for this symbol/range → calm inline note keeping frame + caveat
  (coverage empty); US symbols render sections with 「尚未支援」 until module 19.
- **Error** = per-section inline calm retry; one failing section (each an `<ErrorBoundary>`) never
  blanks the stack.
- **Reduced-motion** → gauge/crosshair/FAB static or instant.

## Governance
Descriptive, not prescriptive: 偏多/中性/偏空 describes what factors show, never 買/賣/target/sizing;
score always ships its factor breakdown. A KD cross / pattern is a **描述 of the chart**, never a cue.
Analyst/external data attributed. 分點/官股 → 馬賽克 for Free. Non-trading `<Caveat>` on every
analytical section; no 明牌/飆股/目標價/買賣 copy ([../backend/governance.md](../backend/governance.md) §1,
[../brand.md](../brand.md) §5).
