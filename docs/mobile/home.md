# 首頁 / Home (tab 1)

The glanceable landing — ambient value **without typing**. The mobile-only 存股 holder (Sufen) and
beginner (Xiaoyu) open the app here; ranks 1–3 are the ambient core loop. Parent index:
[README.md](README.md) §4. UX rationale: [ux-foundation.md](ux-foundation.md). Behaviour/state
contracts: [interaction-spec.md](interaction-spec.md). Web parity (compress, don't drop):
the web app.

> Non-negotiables on every block: 紅漲綠跌 `--up`(漲 red)/`--down`(跌 green) **never color-alone →
> always ▲▼ + sign + aria-label** · `--accent` teal chrome-only, never a price · tabular-nums ·
> `<Caveat>` on every analytical/quote block · 馬賽克 on sponsor cols · dark-first · zh-TW first.

## Components → backend

| Block | Component | Endpoint | Caveat |
|-------|-----------|----------|--------|
| 股票跑馬燈 marquee (persistent top) | the web `StockMarquee` | `/quote/stream` (Premium) · `/quote` EOD (Free) | currentness |
| 1 大盤概況 | `IndexCard` ×2 + `FearGreedGauge` | `/market/overview`, `get_market_context` regime | non-trading (市場情境) |
| 2 我的自選 (更多 → [watchlist.md](watchlist.md)) | `StockRow` list (§7.8) | `/memory` + `/quote(/stream)` | currentness |
| 3 今日事件 (更多 → [calendar.md](calendar.md)) | `EventRow` list | `/calendar` watchlist-filtered ([18](../backend/modules/18-calendar-events.md)) | currentness |
| 4 AI 精選 top-N | `RankCard` (card, not table) + factor chips | `/analytics/rankings` | **non-trading** 「清單非推薦」 |
| 5 最近對話 | `ThreadRow` list | `/threads?sort=updated` | — |
| 6 快速提問 | prompt-starter chips → seeds `/agent/query` | ([07](../backend/modules/07-conversation-streaming.md)) | — |
| 7 夥伴 compact | `SisiChip` + streak + 抽卡 badge | `/pets/progress` ([21](../backend/modules/21-pet-gamification.md)) | — |

## Layout (single mobile column, scrollable)

```
┌───────────────────────────────────────┐
│ 🔍 搜尋股票 代號/名稱   ☆  🔔  ◍ 帳號    │ ← glass top bar (search · ☆自選 · notif · account)
├───────────────────────────────────────┤
│ 加權 23,187 ▲188 +0.82% · 2330 …  →    │ ← 股票跑馬燈 (§7.5), taps → Stock Detail
├───────────────────────────────────────┤
│ ① 大盤概況                              │
│ ┌ 加權 23,187 ▲188 ┐┌ 櫃買 261 ▼1.2 ┐ │ ← two IndexCards side-by-side
│ │ 多空氛圍: 偏多      ││ Fear-Greed ◔68 │ │   + regime flag + mini F-G gauge
│ └───────────────────┘└─────────────────┘ │
├───────────────────────────────────────┤
│ ② 我的自選                    看全部 →  │
│  ▮2330 台積電  1,050.00 ▲+1.45% ╱╲╱     │ ← StockRow, live-tick flash (Premium)
│  ▮0050 元大50    195.20 ▼-0.20% ╲╱╲     │
├───────────────────────────────────────┤
│ ③ 今日事件                     行事曆 →  │
│  🔴除息 2412 中華電 · 現金2.5   → detail │ ← EventRow, event-type chip, taps → detail
│  🗣法說 2330 台積電 14:00       → detail │
├───────────────────────────────────────┤
│ ④ AI 精選  🛡清單非推薦                 │
│ ┌ 2379 瑞昱  ▲+2.1% [偏多74] ────────┐ │ ← RankCard: factor chips 動能/趨勢/籌碼
│ │ 動能▇▇▇ 趨勢▇▇ 籌碼▇ [半導體]        │ │   sponsor factor col → 馬賽克
│ └──────────────────────────────────────┘ │
├───────────────────────────────────────┤
│ ⑤ 最近對話   台積電近期籌碼…    繼續 →  │
├───────────────────────────────────────┤
│ ⑥ 快速提問  [大盤怎麼看?][我的持股如何?] │ ← chips seed Chat
├───────────────────────────────────────┤
│ ⑦ 🐦 Sisi 🔥12  你有一次抽卡           │ ← compact, cornered, low rank
└───────────────────────────────────────┘
        ⌂  ⌁  ◉Chat  ▽  ⚙   (floating glass island, §6.1)
     Home Stock Chat Screener Settings
```

## Visual design

Tokens/radius/spacing from the web app (`VISUAL_DENSITY:6`,
card 16px, pill 999px, 4px base, section gap 24px, card pad 20px). **Dark-first, one column.**

- **Frame.** `100dvh` scroll region on `--bg`; sticky **glass top bar** (56px, `backdrop-blur(20px)
  saturate(160%)`, `--surface`@70% + `1px --border`, solid fallback under
  `prefers-reduced-transparency`) carries 股票搜尋 + **☆ 自選** (opens the watchlist bottom-sheet,
  [watchlist.md](watchlist.md)) + 🔔 bell + account. **Marquee strip**
  (32–36px `--surface`, hairline bottom) sits directly under it, above the feed. Bottom
  **floating glass island** (§6.1) clears the last block via safe-area + 12px inset padding.
- **Blocks** = full-width `--surface` cards (`1px --border`, radius 16px, pad 20px, tinted shadow —
  never neon/pure-black), stacked with 24px section gap; each with an 18px/600 section head and an
  optional right-aligned `看全部 →` / deep-link. **No wide tables** — everything is a row list or
  a card. Ranks 1–3 render **above the fold**; 4–7 on scroll.
- **① 大盤概況** — two `IndexCard`s side-by-side (each ~half width): name 13px `--text-muted` + value
  22px `--font-num` + **▲▼ pts +%** in `--up`/`--down`; 多空氛圍 regime flag pill; compact
  `FearGreedGauge` (radial green→red sweep, needle eases on load, **no re-anim on tick**,
  reduced-motion → static).
- **② 我的自選** — `StockRow`s (§7.8) by `divide-y --border`: `代號 名稱` (no mid-token break) · tabular
  price · **▲▼ +%** in `--up`/`--down` · mini sparkline · 多空 `ScoreBandDot`. Live-tick → row bg
  flashes `--up-weak`/`--down-weak` for Premium; EOD static for Free. Tap → Stock Detail; block header
  `更多 →` pushes the full [watchlist surface](watchlist.md) (watchlist is no longer a tab).
- **③ 今日事件** — `EventRow`s grouped for today: event-type chip (除息/配息/法說…) + 代號名稱 → tap
  Stock Detail. The 存股 hook; block header `更多 →` pushes the full [calendar surface](calendar.md)
  (calendar is no longer a tab).
- **④ AI 精選** — `RankCard`s (**card list, not the web TanStack table**): 代號名稱 · ▲▼% · 多空 band
  pill · `--chart-1..5` factor micro-bars + factor chips · `SectorChip`. Header `--warn` caption
  「清單非推薦」. Sponsor factor col (分點/官股) → **馬賽克** for Free (frame sharp, values blurred, one
  calm 升級 CTA).
- **⑤ 最近對話** — `ThreadRow` (title + snippet + relative time), tap → resume that thread in Chat.
- **⑥ 快速提問** — pill chips (999px, `--surface-2`, hover `--accent` hairline) seeding `/agent/query`
  — the beginner's scaffold, 44px targets.
- **⑦ 夥伴** — compact `SisiChip` (avatar + 🔥streak + 「你有一次抽卡」), cornered and low-rank by
  design (brand-over-mascot, [../brand.md](../brand.md) §3); tap → Settings › 夥伴. Reduced-motion →
  static avatar.

### States ([interaction-spec.md](interaction-spec.md) §4)
- **Loading** = shaped shimmer matching each block (index-card ghosts, ghost rows, RankCard blocks) —
  never a bare spinner. Analytical blocks (AI 精選) may add a narration line ("Miru 正在整理…").
- **Empty** = cold-start (no watchlist) → the 我的自選 + 今日事件 blocks collapse to a single calm CTA
  routing to **Chat** ("問我任何台股問題"); first-run shows the gacha welcome modal (§5 README) before
  the feed.
- **Error** = per-block inline calm retry chip; one failing block never blanks the feed (each block is
  its own boundary). Source-health degrade → honest caveat, not a crash.
- **Reduced-motion** → marquee becomes a static horizontally-scrollable strip; gauge/pet static.

## Governance
Home is a **personal glance**, not a signal board. AI 精選 carries 「清單非推薦」 + the non-trading
footer; quote blocks carry the currentness caveat (Free = EOD, Premium = ~5s delayed). No
明牌/飆股/目標價/買賣 copy ([../brand.md](../brand.md) §5). Pet/streak is cosmetic, never crowds or
colors analysis ([backend/CLAUDE.md](../../backend/CLAUDE.md) §4).
