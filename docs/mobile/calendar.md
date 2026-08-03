# 行事曆 / Calendar (surface — not a tab)

The 存股 core loop — a forward-looking **除權息/配息/法說/股東會/結算** timeline for the user's
watchlist. **Not a bottom tab** (the tab set is Home·Stock·Chat·Screener·Settings); reached via the
**今日事件 block on [Home](home.md)** (`更多 →` pushes the full calendar) and from **Stock Detail's
事件 section** ([stock-detail.md](stock-detail.md)). Web folds these into Stock Detail › 研究圖表 +
Market › 每日焦點; mobile surfaces them as a dedicated pushed view. Parent index: [README.md](README.md)
§2/§3. UX rationale: [ux-foundation.md](ux-foundation.md). Behaviour/state:
[interaction-spec.md](interaction-spec.md). Backend: `/calendar`, `get_calendar_events`,
`get_dividend_schedule` ([18](../backend/modules/18-calendar-events.md)).

> Non-negotiables: a calendar is **a schedule, not a signal** — non-trading `<Caveat>` on the surface ·
> rule-derived rows labeled **deadline markers** (state `expected`), not confirmed dates · tabular-nums
> on all dates/amounts · `--accent` chrome-only · dark-first · zh-TW first. No 明牌/飆股/目標價/買賣.

## Components → backend

| Element | Component | Endpoint |
|---------|-----------|----------|
| Event-type filter chips | segmented chip row | client filter over `type` |
| Date-grouped timeline | `EventRow` list under date headers | `get_calendar_events` (watchlist range + `type`/`state` filter) |
| 除權息/配息 detail | 現金/股票股利 · 除息日 · 基準日 · 發放日 | `get_dividend_schedule` |
| Event → detail | tap row → Stock Detail | `/analytics/{symbol}/*` |
| State pill | scheduled/expected/confirmed/released | `CalendarEvent.state` ([18](../backend/modules/18-calendar-events.md)) |

## Layout (date-grouped timeline)

```
┌───────────────────────────────────────┐
│ ‹ 行事曆         🔔  ◍   本週 ▾         │ ← pushed view: back + glass top bar + range selector
├───────────────────────────────────────┤
│ 全部  除權息  配息  法說  股東會  結算   │ ← event-type filter chips (segmented, scroll)
├───────────────────────────────────────┤
│ ── 08/04 (一) ─────────────────────────│ ← sticky date header
│  🔴 除息  2412 中華電   現金 4.83  已確認│ ← EventRow: type chip · 代號名稱 · amount · state
│  🗣 法說  2330 台積電   14:00      已排定│
│ ── 08/06 (三) ─────────────────────────│
│  📄 財報  多檔 Q2 截止   期限        期限 │ ← deadline marker (state expected)
│  ⚙ 結算  台指期 8月       結算日      規則 │ ← rule-derived (期貨結算 = 3rd Wed)
│ ── 08/12 (二) ─────────────────────────│
│  💰 配息  0056 元大高股息 除息 0.72  已確認│
│  🏛 股東會 2317 鴻海                已排定│
├───────────────────────────────────────┤
│ ⚠ 事件時程僅供參考，非投資建議 · 截至…  │ ← required non-trading Caveat
└───────────────────────────────────────┘
  (pushed full-screen view; bottom tab island hidden — back-gesture dismisses)
```

## Visual design

Tokens from [../web/design-system.md](../web/design-system.md) (card 16px, pill 999px,
`VISUAL_DENSITY:6`). Adapts the web `MarketCalendar` block ([../web/market/每日焦點.md](../web/market/每日焦點.md))
to a **full-tab, date-grouped, one-column timeline**. **Dark-first, 2026 mobile bar.**

- **Frame.** Pushed full-screen view (back-gesture / `‹` dismisses; bottom tab island hidden). `100dvh`
  on `--bg`; **glass top bar** (content scrolls behind blur) with a **range selector** (本週 / 本月 /
  自訂) as a right-aligned bottom-sheet trigger. Filter chip row sits under it, sticky.
- **Event-type filter** = segmented chip row (全部/除權息/配息/法說/股東會/結算…), 999px pills, active
  `--accent`, horizontal-scroll on overflow, `role="tablist"`; multi-select allowed. Chips persist
  per user.
- **Timeline** = grouped by date with **sticky date headers** (`--text-muted`, 13px, 週幾 in zh);
  rows within a day by `divide-y --border`. Each **`EventRow`** (≥44px): event-type **chip** (icon +
  label, distinct per type — 除息/配息/法說/股東會/結算/財報) · `代號 名稱` (no mid-token break) ·
  key datum (現金/股票股利 amount, 時間, or 截止 for deadlines) in `--font-num` tabular · **state pill**.
- **State pill** (muted, [18](../backend/modules/18-calendar-events.md) state model): `已排定
  scheduled` / `期限 expected` / `已確認 confirmed` / `已發布 released` — a `expected` deadline marker
  reads visually as a **window, not a fixed date** (dashed/`--text-muted`), so 月營收/財報 aren't
  mistaken for a confirmed day. Late/early filing surfaces as an honest note, not a cue.
- **Amounts.** 現金/股票股利, 殖利率 in `--up`/`--down` only where signed (e.g. 折溢價), otherwise plain
  `--font-num`; a dividend amount is **not** a price move, so **no 紅漲綠跌 tint** on the payout figure
  itself — reserved for actual price/%chg. Tabular-nums throughout.
- **Tap a row → Stock Detail** ([stock-detail.md](stock-detail.md)); a 除權息 row can expand in place to
  the full `get_dividend_schedule` sub-timeline (除息日 → 基準日 → 停止過戶 → 發放日) via a height tween.
- **Gestures.** **Pull-to-refresh** re-fetches the range; **swipe a row** → `⏰ 設定提醒` (seeds an
  event alert, [settings.md](settings.md)). Reduced-motion → instant, no track slide.

### States
- **Loading** = shimmer date-groups (ghost date header + ghost rows), never a spinner.
- **Empty** = no events in range → calm inline 「本週無重要事件」 keeping the frame + caveat (a
  **coverage** empty, not cold-start — no chat CTA); cold-start (empty watchlist) → route to Chat.
- **Error** = inline calm retry; a scraper-sourced row (index rebalance/macro) carries a source +
  as-of caveat if its feed is stale ([18](../backend/modules/18-calendar-events.md) governance).
- **Reduced-motion** → no expand tween, static.

## Governance
Non-trading: an events list, not a signal. Rule-derived markers labeled as **deadlines**, not
confirmed dates; scraped dates (index/macro) carry source + as-of. Non-trading `<Caveat>` on the
surface; no 明牌/飆股/目標價/買賣 copy ([../backend/governance.md](../backend/governance.md) §1,
[18](../backend/modules/18-calendar-events.md) governance).
