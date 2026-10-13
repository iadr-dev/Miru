# 自選 / Watchlist (surface — not a tab)

The glanceable monitor — live-ish quote rows for holdings + observed symbols. **Not a bottom tab**
(the tab set is Home·Stock·Chat·Screener·Settings), reached three ways: (a) the zero-tap **我的自選
block on [Home](home.md)**, (b) **one tap from any tab via the top-bar ☆**, which opens this as a
**drag-snap bottom-sheet** (the primary monitor entry — mirrors web's ☆-toggles-the-right-rail), and
(c) a quick-access list on the [Stock tab](stock.md). On mobile the web's right-side rail becomes a
**bottom-sheet / full card-row list** (not a wide table), gesture-first. Parent index:
[README.md](README.md) §2. UX rationale: [ux-foundation.md](ux-foundation.md). Behaviour/state
contracts: [interaction-spec.md](interaction-spec.md). Web parity:
the web app
§7.7 (rail) + §7.8 (`StockRow`).

> Non-negotiables: 紅漲綠跌 `--up`(漲 red)/`--down`(跌 green) **never color-alone → ▲▼ + sign +
> aria-label** · `--accent` teal chrome-only · tabular-nums · currentness `<Caveat>` · 馬賽克 for
> sponsor extras (分點/主動ETF) · dark-first · zh-TW first · don't break `台積電 2330` mid-token.

## Components → backend

| Element | Component | Endpoint |
|---------|-----------|----------|
| Market group tabs (TW/US/ETF) | segmented pill row (`market`×`asset_class`) | — |
| Groups (我的持股/觀察中…) | collapsible section header + count | `/watchlists`, `/watchlists/{id}/items` |
| Quote row | `StockRow` (§7.8) | `/quote(/stream)` · `/analytics/{s}/indicators` spark · `get_company_profile` |
| Swipe actions | ☆ star · alert · remove | `PUT/DELETE /watchlists/{id}/items/{symbol}` · `/alerts` ([14](../backend/modules/14-scheduler-alerts.md)) |
| Add symbol | search sheet | `/quote` lookup + `PUT items` |

## Layout (card/row list, gesture-first)

```
┌───────────────────────────────────────┐
│ ══ (drag handle)              自選  🔍  │ ← bottom-sheet: drag handle + title + 加入 search
├───────────────────────────────────────┤
│ [ TW ]   US    ETF                  +  │ ← market tabs (segmented) + add-symbol
├───────────────────────────────────────┤
│ ▾ 我的持股                        12    │ ← group header (collapse + count)
│  ▮ 2330 台積電    1,050.00 ▲+1.45% ╱╲ │ ← StockRow; live tick → bg flash
│  ▮ 0050 元大50      195.20 ▼-0.20% ╲╱ │
│   ← swipe left:  [ ⏰ 提醒 ] [ ✕ 移除 ] │ ← swipe-reveal actions
│   → swipe right: [ ☆ ]                  │
│ ▾ 觀察中                           5    │
│  ▮ 2317 鴻海        203.00 ▲+0.5% ╱╲╱ │
│ + 新增分組                              │
├───────────────────────────────────────┤
│ ⚠ 報價僅供參考，非交易建議 · 截至08/01  │ ← required currentness Caveat
└───────────────────────────────────────┘
  (top-bar ☆ → drag-snap bottom-sheet: detents [peek · half · full], swipe-down/backdrop dismisses;
   the tab island stays visible beneath. Also openable as a full-screen pushed view from Home 更多→)
```

## Visual design

Tokens from the web app (`VISUAL_DENSITY:6`, card 16px, pill
999px). `StockRow` full spec = §7.8; rail semantics adapted to a full-width tab. **Dark-first, one
column, 2026 mobile bar** (gesture-first, glanceable type, thumb-zone spacing).

- **Frame.** Primary entry = **drag-snap bottom-sheet** from the top-bar ☆ (detents peek·half·full,
  drag handle, swipe-down/backdrop dismisses, tab island stays beneath); reduced-motion → sheet snaps
  without spring. Also openable **full-screen** (Home 更多→ / Stock tab), where back-gesture dismisses.
  `--bg` surface; **Market group tabs** (`TW / US / ETF`) = segmented pill row at the top,
  active `--accent` underline + shared-layout glide (`layoutId`), horizontal-scroll on overflow,
  `role="tablist"` + arrow/swipe nav, persisted per user. `+` add-symbol opens a **bottom-sheet**
  search (detented, drag-to-dismiss).
- **Groups** = collapsible section headers (▾ + name + count badge in `--surface-2` pill); rows within
  a group by `divide-y --border` (density without box-in-box); `+ 新增分組` ghost row.
- **Quote row** (`StockRow`, ≥44px tall) = single **candlestick cell** (紅漲綠跌 body) · `代號 名稱` (no
  mid-token break) · `SectorChip` (sector-token muted tint + hairline) · tabular price (`--font-num`)
  · **▲▼ + change + %** in `--up`(漲)/`--down`(跌) · mini sparkline (accent stroke, up/down-tinted
  fill) · category extra (多空 `ScoreBandDot` / ETF 折溢價% / 處置 `--warn` flag). Sponsor extras
  (分點/主動ETF) → **馬賽克** lock for Free.
- **Live tick flash** — on a new quote the row bg pulses `--up-weak`/`--down-weak` then fades
  (Premium `/quote/stream`, ~5s); EOD static for Free. `tabular-nums` means columns never reflow on a
  tick. Gauge/band dot **never re-animates on a tick**.
- **Gesture actions (the mobile-native replacement for hover ✕).** **Swipe-left reveals**
  `⏰ 提醒` (→ Alert rule builder, [settings.md](settings.md)) + `✕ 移除` (destructive, `--down`/red-safe
  confirm on release); **swipe-right reveals** `☆` star toggle (idempotent `PUT/DELETE items`). Actions
  are ≥44px, spring-snap open/closed; **tap-anywhere-else or scroll dismisses**. Reduced-motion →
  actions appear instantly (no track slide). Keyboard/SR fallback: row focus → action menu button.
- **Reorder** = long-press to lift a row/group (`@dnd-kit` sortable, lexorank), drag to reorder → 50%
  opacity; reduced-motion → no drag transition, keyboard reorder (focus row, Space lift, ↑/↓, Space
  drop, aria-live announce).
- **Pull-to-refresh** at the top re-fetches quotes (spring overscroll, `--accent` spinner ring);
  reduced-motion → instant refetch with a shimmer, no bounce.

### States
- **Loading** = shimmer `StockRow`s (candle ghost + text bars), never a spinner.
- **Empty group** → 「尚無股票，點 ☆ 或問 Miru 加入」; **empty tab** → server-seeded with the default
  preset (the web presets table); **cold-start** (no
  lists at all) → route to Chat.
- **Error** = inline calm retry chip per group; a stale/failed quote shows an honest as-of caveat, not
  a blank price. US Index (SPY/QQQ/DIA) group renders symbols with 「尚未支援」 until module 19 ships.

## Governance
Public user data, non-trading, ungated CRUD (soft caps only). The quote column inherits tier (EOD
Free / live-ish Premium) and carries the currentness caveat; 紅漲綠跌 + ▲▼ + tabular-nums throughout;
`--accent` never a price. No 明牌/飆股/目標價/買賣 copy ([backend/CLAUDE.md](../../backend/CLAUDE.md) §4).
