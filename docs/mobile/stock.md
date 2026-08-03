# 個股 / Stock (tab 2 — the symbol landing)

The **entry point to any symbol**: search · recent · quick-access · trending. A lightweight landing
that **pushes the full-screen [Stock Detail](stock-detail.md)** on selection. New in the mobile tab
set (Home·Stock·**Chat**·Screener·Settings) — it replaces the web's global symbol-search-in-top-bar
with a first-class thumb-reachable tab. Parent index: [README.md](README.md) §2. UX rationale:
[ux-foundation.md](ux-foundation.md). Behaviour/state: [interaction-spec.md](interaction-spec.md).

> Non-negotiables: 紅漲綠跌 `--up`(漲)/`--down`(跌) **never color-alone → ▲▼ + sign + aria-label** ·
> `--accent` teal chrome-only · tabular-nums on all quotes · dark-first · zh-TW first · don't break
> `台積電 2330` mid-token · no 明牌/飆股/目標價/買賣 copy. This is a **navigator**, not an analysis
> surface — quote previews carry the currentness caveat; no scores/signals rendered here.

## Components → backend

| Element | Component | Endpoint |
|---------|-----------|----------|
| Search field (autofocus-optional) | symbol/name search box | `/quote` lookup + `get_company_profile` (name/sector match) |
| Recent symbols | `MiniQuoteChip` list | client history + `/quote` batch |
| 我的自選 quick-access | compact `StockRow` (→ [watchlist.md](watchlist.md)) | `/memory` + `/quote(/stream)` |
| 熱門 / trending | ranked chips/rows | `get_top_stocks` (漲跌幅/成交值) |
| Result row → detail | tap → push [stock-detail.md](stock-detail.md) | — |

## Layout (search + browse landing)

```
┌───────────────────────────────────────┐
│ 🔍 搜尋代號或名稱 (2330 / 台積電)    ✕  │ ← search field (glass top region, content scrolls behind)
├───────────────────────────────────────┤
│ 最近查詢                          清除 │
│  2330 台積電 1,050 ▲+1.4% · 2454 …  →  │ ← MiniQuoteChip row (horizontal scroll)
├───────────────────────────────────────┤
│ 我的自選                    看全部 →   │ ← quick-access (pushes watchlist surface)
│  ▮2330 台積電   1,050.00 ▲+1.45% ╱╲╱  │
│  ▮0050 元大50     195.20 ▼-0.20% ╲╱╲  │
├───────────────────────────────────────┤
│ 熱門  [漲幅|成交值|量]                  │ ← trending, segmented; NOT a recommendation
│  ▮2379 瑞昱   ▲+3.2% · ▮3661 世芯 ▲+2.9│
├───────────────────────────────────────┤
│ (while typing) ─────────────────────── │
│  2330 台積電 [半導體] 1,050 ▲+1.4%  →  │ ← live result rows, tap → push Stock Detail
│  2331 精英   [電腦]     42.5 ▼-0.6%  →  │
└───────────────────────────────────────┘
        ⌂  ⌁  ◉Chat  ▽  ⚙
     Home Stock Chat Screener Settings   (Stock = tab 2)
```

## Visual design

Tokens from [../web/design-system.md](../web/design-system.md) (`VISUAL_DENSITY:6`, card 16px, pill
999px, input 10px). `StockRow`/`MiniQuoteChip` = §7.8. **Dark-first, one column, 2026 mobile bar.**

- **Frame.** `100dvh` on `--bg`; the **search field rides the top** in a glass region (content scrolls
  behind blur), radius 10px `--surface` + `1px --border`, focus-visible `--accent` ring, ✕ clear.
  Tapping it can raise the keyboard without leaving the tab. Bottom floating island (§6.1) present
  (this **is** a tab, unlike the pushed Stock Detail).
- **Sections** = `--surface` cards / `divide-y --border` groups, 24px gap, each an 18px/600 head:
  **最近查詢** (horizontal-scroll `MiniQuoteChip` row — code+name+▲▼%), **我的自選** quick-access
  (compact `StockRow`s, `看全部 →` pushes the [watchlist surface](watchlist.md)), **熱門 trending**
  (segmented 漲幅/成交值/量 via `get_top_stocks`).
- **Result rows** (while typing) = `StockRow`-lite: `代號 名稱` (no mid-token break) + `SectorChip` +
  tabular price + **▲▼ +%** in `--up`/`--down`. Debounced search; match on code **or** zh/en name.
  Tap → **push full-screen [Stock Detail](stock-detail.md)** (back-gesture returns here). Long-press →
  quick actions bottom-sheet (☆ 加入自選 · 💬 問 Miru · ⏰ 設定提醒).
- **紅漲綠跌 + ▲▼ + tabular-nums** on every quote preview; `--accent` never a price. **熱門/trending is
  explicitly framed as activity (漲跌/成交), not a pick** — no 多空 score, no 「推薦」 language.
- **Gestures.** Pull-to-refresh re-fetches quick-access + trending; horizontal swipe on recent chips.

### States
- **Loading** = shimmer chips/rows, never a spinner.
- **Empty (cold-start — resolved).** With no recent history, the tab **leads with 熱門 trending +
  a search prompt** ("搜尋代號，或問 Miru 任何台股問題") — never a blank screen. (Watchlist is rarely
  empty — seeded presets, [watchlist.md](watchlist.md) — so the 我的自選 quick-access usually has
  content too.) The search field is available but **not auto-focused** (avoids popping the keyboard on
  a glance-open); 熱門 is the default content, ask-Miru is the secondary prompt.
- **No search match** → calm 「找不到符合的個股，試試代號或問 Miru」 + a Chat CTA.
- **Error** = inline calm retry; a stale quote shows as-of, not a blank price.
- **Reduced-motion** → no chip-scroll momentum easing; instant.

## Governance
A **navigator**, non-trading: quote previews carry the currentness caveat (EOD Free / live-ish
Premium); trending is descriptive activity, never a recommendation; no scores/signals rendered on this
surface (they live in the pushed [Stock Detail](stock-detail.md), each caveated). No
明牌/飆股/目標價/買賣 copy ([../backend/governance.md](../backend/governance.md) §1, [../brand.md](../brand.md) §5).
