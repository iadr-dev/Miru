# Miru — Mobile Client Design Spec (index)

Design/blueprint for the **mobile-first client**. Grounded in the backend abilities
([../backend/](../backend/)) and the UX research foundation. Companion to the web dashboard
(`web/`); shares the design tokens, brand, and governance. This README is the **index** —
per-screen layout/visual detail lives in the per-screen docs below.

> **Design read:** a **chat-first companion + glanceable monitor** for TW retail investors on the
> phone — the mobile-only 存股 holder and finance-curious beginner live here, plus the active
> trader's monitoring mode. Bilingual (zh-TW first), non-trading, dark-mode-first. This is product
> UI, not marketing → **shared shadcn/ui + Tailwind token system** (or React Native equivalents if
> native). It **targets the latest (2025–2026) mobile product bar** — floating glass bottom-nav
> island (raised accent Chat center, morphing pill), gesture-first surfaces (swipe row actions,
> drag-snap bottom-sheets with detents, pull-to-refresh), content-scrolls-behind-blur bars, large
> glanceable type + thumb-zone spacing, bento grouping, calm dense-but-legible density — under
> `design-taste-frontend` anti-slop discipline (motivated motion only, full states, no AI-purple /
> generic-glass-on-everything, shape/color locks, WCAG AA). Brand: [../brand.md](../brand.md). The
> concrete visual language (palette, CJK type, glass nav island, 多空 gauge, cards, streaming chat,
> 馬賽克 teaser, motion) is shared with web in **`web/src/app/tokens.css`** —
> referenced by section, never redefined here.

**Read alongside this index:**
- **[ux-foundation.md](ux-foundation.md)** — the UX research foundation (personas, jobs, mobile intent) *(UX Researcher).*
- **[interaction-spec.md](interaction-spec.md)** — behavioural / motion / state / a11y contracts *(UX Architect).*
- **`web/src/app/tokens.css`** — the token source of truth (shared).

---

## Per-screen docs (index)

**Tab set (5, Chat centered + accented):** **首頁 Home · 個股 Stock · 對話 Chat · 選股 Screener · 設定
Settings**. Watchlist and Calendar are **surfaces**, not tabs (reached from Home blocks / pushed
views). Stock Detail is a **full-screen pushed view**, not a tab.

| # | Screen | Doc | Kind | Maps to (headline) |
|---|--------|-----|------|--------------------|
| 1 | **首頁 / Home** | [home.md](home.md) | tab | marquee + ranked blocks (大盤概況·我的自選·今日事件·AI精選·最近對話·快速提問·夥伴) — `get_market_context`, `/memory`, `/calendar`, `/analytics/rankings`, `/threads`, `/pets/progress` |
| 2 | **個股 / Stock** | [stock.md](stock.md) | tab | symbol search · recent · 我的自選 quick-access · 熱門 → pushes Stock Detail — `/quote`, `get_company_profile`, `get_top_stocks` |
| 3 | **對話 / Chat** *(center, accented)* | [chat.md](chat.md) | tab | full-screen streaming conversation, voice mic, inline rich_card, 顯示過程, pet ambient — `/agent/query` SSE, `/threads/*`, `/voice/*` ([07](../backend/modules/07-conversation-streaming.md), [13](../backend/modules/13-chat-interaction-features.md)) |
| 4 | **選股 / Screener** | [screener.md](screener.md) | tab | preset card grid → ranked **card list**, bottom-sheet gate/condition filters — `/analytics/screener/*`, `/analytics/rankings` ([20](../backend/modules/20-analytics-scoring.md) §5) |
| 5 | **設定 / Settings** | [settings.md](settings.md) | tab | hub: Alerts/Schedules · Themes · 語氣 · 夥伴 · Billing · BYOK · Usage · Alert prefs · Memory · Profile ([14](../backend/modules/14-scheduler-alerts.md), [23](../backend/modules/23-agent-personality.md), [21](../backend/modules/21-pet-gamification.md)) |
| — | **個股詳情 / Stock Detail** | [stock-detail.md](stock-detail.md) | pushed | section stack: quote/AssetHeader → 多空 gauge → 技術 → 智能摘要/SWOT🔒 → 事件 → 籌碼/新聞; floating 問Miru — `/analytics/{s}/*`, `/quote` |
| — | **自選 / Watchlist** | [watchlist.md](watchlist.md) | surface | live-ish quote rows, swipe actions, TW/US/ETF groups — Home block + pushed full view; `/memory` + `/quote(/stream)`, `/watchlists` |
| — | **行事曆 / Calendar** | [calendar.md](calendar.md) | surface | 除權息/配息/法說/股東會/結算 timeline for watchlist — Home block + Stock Detail 事件; `/calendar` ([18](../backend/modules/18-calendar-events.md)) |

---

## 1. Platform note

Two viable builds off the same backend + tokens:
- **Responsive web (<768px)** — the web client (`web/`) collapses to this spec.
- **Native (React Native / Expo)** — if a store presence is wanted; reuses the token system, the
  module-07 SSE client, and the same IA. **LINE is the primary TW channel** for notifications either
  way ([14](../backend/modules/14-scheduler-alerts.md) §4).

The IA below is identical across both; only the shell (tab bar vs responsive nav) differs.

---

## 2. Bottom-tab navigation (max 5)

Tuned to **daily mobile intent** (Sufen the 存股 holder, Xiaoyu the beginner, Kai monitoring). Chat is
centered + accented; deep analysis is compressed, not removed. The bar is a **modern floating glass
island** with a **raised accent center tab for Chat** and a **morphing active pill** — full treatment
in the shared web tokens

```
        content scrolls behind, blurred under the bar
   ╭───────────────────────────────────────────────╮
   │   ⌂        ⌁       ╭─────╮      ▽       ⚙       │   ← floating glass island (rounded, blurred,
   │  Home    Stock     │ ◉   │   Screener Settings  │      1px inner border, 12px off bottom edge,
   │                    │Chat │                      │      hides on scroll-down / reveals on up)
   │  ▁▁▁ active pill morphs under the current tab   │
   ╰────────────────────╰─────╯──────────────────────╯
              raised, --accent-filled center = Chat (thumb home)
```

Top bar (every tab): **股票搜尋** (search stocks) + **☆ 自選** (watchlist) + **🔔 notifications**
(`/notifications` inbox) + account. Glass, blurs over scrolling content.

> **Watchlist has no dedicated tab** (the 5 slots are Home·Stock·Chat·Screener·Settings) but is
> reachable in **one tap from anywhere** via the **top-bar ☆**, which opens the full watchlist as a
> **drag-snap bottom-sheet** (groups, TW/US/ETF market tabs, swipe ☆/alert/remove, live ticks,
> reorder — full spec [watchlist.md](watchlist.md)). It *also* surfaces as the zero-tap **我的自選
> glance block** on [Home](home.md) (§4) and a quick-access list on the [Stock tab](stock.md). This
> mirrors web, where ☆ toggles the right-side rail on every page
> §2). So: glance = 0 tap (Home), monitor/manage = 1 tap (top-bar ☆, any tab).

| Tab | zh / en | Icon | Doc | Rationale |
|---|---|---|---|---|
| 1 | **首頁 / Home** | house | [home.md](home.md) | at-a-glance landing; ambient value without typing |
| 2 | **個股 / Stock** | chart-line | [stock.md](stock.md) | symbol search/recent/browse → pushes Stock Detail |
| 3 | **對話 / Chat** *(center, accented)* | chat | [chat.md](chat.md) | the product's soul — center = thumb-reachable, emphasis slot |
| 4 | **選股 / Screener** | funnel | [screener.md](screener.md) | preset → ranked card list; a first-class discovery tool |
| 5 | **設定 / Settings** | gear | [settings.md](settings.md) | config hub + the overflow surfaces |

### Everything else lives here
- **Stock Detail** = full-screen pushed view (deep-linked from Home/Stock/Chat/Watchlist/Calendar
  cards), **not a tab**. Web's in-page tabs collapse to a **scrollable section stack**: quote/AssetHeader
  → 多空 score gauge → 技術 (single chart + indicator chips) → 智能摘要/SWOT (Premium teaser) → 事件 →
  籌碼/新聞. Floating **「問 Miru」** button seeds chat with the symbol. Full spec: [stock-detail.md](stock-detail.md).
- **Watchlist** is a **surface, not a tab** — reached as the **我的自選 block on Home** and a **pushed
  full view** (Home block `更多 →` / from the Stock tab). Spec: [watchlist.md](watchlist.md).
- **Calendar** is a **surface, not a tab** — reached via the **今日事件 block on Home** (`更多 →`) and
  **Stock Detail's 事件 section**. The 存股 loop. Spec: [calendar.md](calendar.md).
- **Alerts / Schedules** live under **Settings** ([settings.md](settings.md)) — a low-frequency Pro
  config surface; discoverable via a stock's detail → 「設定提醒」 and Home's Top-N block.
- **Notifications inbox** (`/notifications`) is the **bell in the top bar on every tab** — a
  receive-and-tap-through surface, not buried in Settings.
- The pet stays **ambient in the Chat tab's companion region** ([21](../backend/modules/21-pet-gamification.md) §5),
  never a tab; its collection/gacha page lives under Settings → 夥伴.
- **Voice input** = mic in the Chat composer, **Free** (STT is the beginner's on-the-go ask
  affordance — quota-bounded like text, not tier-gated); `/voice/transcribe` returns text for
  **confirm-before-send** (never auto-fires a turn — a 2330→2380 mis-hear must be catchable
  [13](../backend/modules/13-chat-interaction-features.md) §voice). **TTS read-aloud stays Premium.**

> **Why this tab set:** Chat (soul) is centered; Stock (search → detail) and Screener (discovery) are
> the two daily analysis entry points; Home is the no-typing glance; Settings holds config. Watchlist
> and Calendar are high-value but **contextual** (they live as Home blocks + pushed views), so they
> don't each claim a scarce tab slot. Screener earns a tab because discovery is a distinct daily job
> that a Settings burial would hide.

---

## 3. Web ↔ mobile divergence

**Desktop-primary (stays on web):** the multi-panel Stock Detail workbench, screener funnel tables
with factor columns, side-by-side parallel sub-agent lanes, multi-symbol comparison.

**Mobile-primary (this client):** Chat (full-screen, voice-friendly) · Watchlist quotes (glanceable,
live-ish) · Calendar / dividend timeline (the 存股 core loop) · Alerts + notifications (receive &
tap-through, **LINE delivery**) · quick quote via the Stock tab search.

Mobile compressions of shared surfaces:
- Stock Detail → **section stack** (not multi-panel) — [stock-detail.md](stock-detail.md).
- Screener → **card list** + bottom-sheet filters (not a TanStack table) — [screener.md](screener.md).
- Parallel agent lanes → **collapsed to the narration track** with a 顯示過程 disclosure — [chat.md](chat.md).
- **SSE resumability** (`Last-Event-ID`) makes flaky mobile networks a non-event — critical here.
- **Per-thread draft persistence** survives app backgrounding ([13](../backend/modules/13-chat-interaction-features.md) §3).

---

## 4. Home content blocks — ranked

Detailed layout/visual in [home.md](home.md). Persistent top: the **股票跑馬燈 (marquee)**
(the web app). Then, tuned for a single mobile column:

1. **大盤概況** — TAIEX/櫃買 + 多空氛圍 regime flag (`get_market_context`)
2. **我的自選** — watchlist quotes, live-ish for Premium (`/memory` + `/quote/stream`) → [watchlist.md](watchlist.md)
3. **今日事件** — 除權息/配息/法說 for watchlist (`/calendar`, the 存股 hook) → [calendar.md](calendar.md)
4. **AI 精選** — profile-aware top-N cards + factor chips (`/analytics/rankings`; 🛡「清單非推薦」)
5. **最近對話** — continue a thread (`/threads`)
6. **快速提問** — prompt-starter chips (seeds `/agent/query`) — the beginner's scaffold
7. **夥伴** — Sisi + streak + 抽卡, **compact & cornered** (`/pets/progress`)

Ranks 1–3 are the **ambient core** — what a mobile-only 存股 user opens for, no typing. Pet streak is
rank 7, small, subordinate by design (brand-over-mascot, [../brand.md](../brand.md) §3).

---

## 5. Onboarding (mobile-first warm start)

Register (OAuth Google/LINE) → **welcome gacha modal**, one free pull, **Sisi (藍鵲)** likely,
auto-equips ([21](../backend/modules/21-pet-gamification.md) §6, [../brand.md](../brand.md) §3a) →
first chat with `follow_up` prompt chips scaffolding the first real question → streak begins. Pet
reacts via `pet.reaction` in a **separate companion region**, never mixed with the answer/caveat area.

---

## 6. Mobile usability, a11y & TW conventions

Inherits all of web §6, with mobile emphasis:
- **紅漲綠跌** theme token (red 漲 / green 跌) + **▲▼ + sign + aria-label, never color-alone**.
- **Touch targets ≥44px**; bottom island + composer mic + swipe actions sized for **one-handed use**.
- **Tabular numerals**; CJK Traditional face, body ≥15–16px, line-height ≥1.6; don't break `台積電 2330`.
- **Dark-mode first-class**; 馬賽克 teaser (frame sharp / values blurred / one calm CTA) for sponsor data.
- **Voice confirm-before-send** mandatory; low-confidence tokens highlighted; ambiguous symbol → `ask_user`.
- **TTS read-aloud must speak the caveat** ([13](../backend/modules/13-chat-interaction-features.md) §voice, governance §8).
- **Reduced-motion** honored (gacha/pet/gauge/marquee/sheet-slide → static/instant).
- **Loading = narration** ("Miru 正在看盤面…") on generated-analysis surfaces, shaped skeletons elsewhere — never a bare spinner.

Full behavioural/state/motion contracts: [interaction-spec.md](interaction-spec.md).

## 7. Cross-cutting governance

Identical to web §8: every analytical surface carries the **non-trading + currentness caveat**; the
caveat badge + "not advice" footer are **required, non-removable** components; copy avoids
明牌/飆股/目標價/買賣/進場/停損; personality/pet layers style **tone only**. Sponsor data → 馬賽克.
See [../backend/governance.md](../backend/governance.md) §1, [../brand.md](../brand.md) §5.

> Build-time review gate: when the UI is implemented from these blueprints, the **`impeccable`** agents
> review the *shipped* pixels against this design set (per the web app) — not
> applicable at this blueprint stage.
