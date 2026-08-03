# 了然 · Miru — Interaction / Motion / Gesture / State Spec (mobile)

The **mobile interaction contract** for the 了然·Miru phone client — the mobile counterpart to
[../web/interaction-spec.md](../web/interaction-spec.md). It specifies the layer web doesn't cover:
**touch gestures, the native shell, offline / flaky-network / app-lifecycle, haptics, and push**.
Blueprint — no code.

> **This doc EXTENDS the web contract; it never redefines shared behaviour or tokens.**
> **Inherited (referenced, not restated):** the motion tokens + reduced-motion tiers + animation
> catalog ([../web/interaction-spec.md §1](../web/interaction-spec.md#1-motion-system)); the SSE-driven
> live layer + `Last-Event-ID` resume + `aria-live` discipline ([web §3](../web/interaction-spec.md#3-sse-driven-live-layer));
> the full-state discipline — `LoadingState`/`EmptyState`/`ErrorState` classes ([web §4](../web/interaction-spec.md#4-full-state-discipline-reusable-contracts));
> the component contracts (`ChartWithDataSheet`, 多空 gauge, StockRow, streaming message, marquee,
> 馬賽克 teaser — [web §2](../web/interaction-spec.md#2-component-interaction-contracts)); the non-negotiables
> ([web §0](../web/interaction-spec.md#0-non-negotiables-enforced-by-every-contract-below)). Tokens live in
> [../web/design-system.md](../web/design-system.md) §5 (motion) + §6.1 (mobile island) + §7 (components) —
> **referenced, never duplicated.** The cache-coherence contract is [../web/data-freshness.md](../web/data-freshness.md);
> mobile inherits it and adds the offline/lifecycle layer (§6 below).
> **Adds (mobile-only):** touch-gesture catalog, native navigation shell, per-screen mobile
> contracts, offline/flaky-network/lifecycle, haptics, push deep-linking, mobile a11y.

> **Targets the 2026 mobile interaction bar.** The shell + gestures below reflect current (2025–2026)
> native patterns: **gesture-first** (swipe row actions, pull-to-refresh tied to *background-revalidate*
> not hard reload, **drag-snap bottom-sheets with detents** over modal stacks / deep menus), a
> **floating glass nav island** with shared-element / morphing active pill + hide-on-scroll,
> **spring-based physics** motion (not linear tweens), **haptics as first-class feedback**,
> safe-area + dynamic-type / large-font, **content-behind-blur**, and **edge-swipe back**. Held to the
> anti-slop bar: every motion > Tier 3 has a reduced-motion fallback; non-trading throughout
> (no magnitude-scaled / celebratory motion on price/score; the 多空 needle never re-anims on a tick);
> shared tokens are referenced, never redefined. (Grounded in shipped Kestrel + the web interaction-spec —
> no external pattern library.)

Companion docs: [README.md](README.md) (IA), `ux-foundation.md` (UX Researcher — flows/personas),
and the per-screen visual docs the UI Designer is writing: [home.md](home.md), [watchlist.md](watchlist.md),
[chat.md](chat.md), [calendar.md](calendar.md), [settings.md](settings.md), [stock-detail.md](stock-detail.md),
[screener.md](screener.md).

---

## 0. Non-negotiables (mobile — inherit [web §0](../web/interaction-spec.md#0-non-negotiables-enforced-by-every-contract-below) verbatim, plus)

1. **Non-trading motion** on every touch surface too: no magnitude-scaled or celebratory motion on
   price/score; the **多空 gauge needle never re-animates on a quote tick** (or on a pull-to-refresh);
   **haptics are never magnitude-scaled** — a bigger move is not a stronger buzz.
2. **紅漲綠跌 + ▲▼ never color-alone; tabular-nums; dark-first** — unchanged from web.
3. **Reduced-motion fallback on every motion > Tier 3** ([web §1.2](../web/interaction-spec.md#12-reduced-motion-tiers-what-collapses)):
   the tab island stops hiding-on-scroll (stays fixed), gestures keep function but drop the tween,
   gacha/pet collapse to static.
4. **`prefers-reduced-transparency`** → glass island / top bar / bottom-sheet fall back to solid
   `--surface`. **Dynamic-type / large-font** must not clip the island labels or caveat.
5. **Cosmetic pet motion + haptics stay in the Chat companion region** — never on the analysis area,
   never a confirm/error buzz standing in for data.

---

## 1. Platform stance

Two viable builds off one backend + token system ([README §1](README.md#1-platform-note)):

| | Responsive web (<768px) | Native (React Native / Expo) |
|---|---|---|
| Shell | the web client collapses to the island via CSS breakpoint | native tab navigator + native stack |
| Nav transitions | CSS/Framer shared-layout | native stack push/pop + `react-navigation`; shared-element for the pill |
| Gestures | pointer/touch events + CSS scroll-snap | `react-native-gesture-handler` + `reanimated` worklets |
| Haptics | (none — web has no reliable haptics) | `expo-haptics` (§7) |
| Safe-area | CSS `env(safe-area-inset-*)` | `react-native-safe-area-context` |
| Push | Web Push / falls back to LINE | native push token + LINE ([14](../backend/modules/14-scheduler-alerts.md)) |
| SSE | `EventSource` | module-07 SSE client over `fetch`/RN polyfill; same `Last-Event-ID` resume |

**The contract below is written platform-agnostically** (states + drivers + reduced-motion), and
**RN-native specifics are called out inline** where behaviour must differ (native nav, gesture
handlers, haptics, safe-area, push, lifecycle).

### 1.1 RN token / motion mapping (do not fork the source of truth)

The CSS-var tokens ([design-system §2/§5](../web/design-system.md#2-color-tokens-dark-first-one-accent))
remain the **single source of truth**; native mirrors them without a second definition:

| Web token | RN mapping | Rule |
|---|---|---|
| CSS custom props (`--accent`, `--up`, `--surface`, radii, spacing 4px base) | one generated theme object consumed by `StyleSheet` / a token provider | generated **from** the same token file — never hand-copied |
| `spring.default` (`stiffness:120, damping:22`) | `reanimated` `withSpring({stiffness:120, damping:22})` | identical numbers; one shared motion-config module |
| `dur.tick` / `dur.tick-fade` / `dur.stream` / `dur.rail` | `withTiming(duration, easing)` | same ms + easing |
| `ease.overshoot` (gacha/pet only) | `Easing.bezier(0.34,1.56,0.64,1)` — **pet region only** | never on data |
| `prefers-reduced-motion` | `AccessibilityInfo.isReduceMotionEnabled` + listener | drives the same Tier collapse (§0.3) |
| `prefers-reduced-transparency` | `AccessibilityInfo.isReduceTransparencyEnabled` (iOS) | glass → solid `--surface` |
| light/dark/system | `Appearance` / theme provider | dark-first default |

> **Animation library (mobile):** native = **`reanimated`** + `react-native-gesture-handler`
> (worklet-driven, runs on the UI thread); responsive-web fallback = **`motion/react`** (the web
> default, [../../web/CLAUDE.md](../../web/CLAUDE.md) §1). **GSAP does NOT apply to the native app** —
> it's DOM/SVG-only, so it can appear only on the responsive-web build, never in RN. The `spring.default`
> / duration tokens above are the shared contract; each platform maps them to its own engine.

✓ **Shared token-export** (CSS-vars → generated RN theme object, single source, no drift) is specified
as a build rule in [../../web/CLAUDE.md](../../web/CLAUDE.md) §2.

---

## 2. Navigation shell contract

Legend for state columns: **D**efault · **P**ressed/touch · **F**ocus (SR/keyboard) · **L**oading ·
**E**mpty · **Err**or · **Live**. "Driver" = backend event/route that changes state. Gesture rows
add **Haptic** + **Reduced-motion** where relevant.

### 2.1 Bottom-tab floating glass island (design-system [§6.1](../web/design-system.md#61-mobile--floating-glass-island-raised-accent-center))

Order: **Home · Stock · ◉Chat · Screener · Settings** (Chat raised, accent-filled, thumb-home).
Only the **5 labels change** from the old set (Home/Watch/Chat/Cal/Settings) — the island morph /
hide-on-scroll / safe-area / raised-Chat-center treatment is **unchanged**. **Watchlist** and
**Calendar** are **no longer tabs** — they are surfaces reached from Home blocks / pushed views (§5.2,
§5.4). **Stock** (symbol search/recent/browse landing) and **Screener** (was under Settings) are the
two new/promoted tabs.

| State | Behaviour | Driver |
|---|---|---|
| D | floating rounded glass island, 12px off bottom + safe-area inset; active tab shows morphing pill + label, inactive icon-only | route |
| P | tab press → icon scale-down feedback (Tier 1); raised Chat tab depresses | user tap |
| A (switch) | active pill **morphs** to the new tab (`layoutId` / RN **shared-element**, `spring.default`); label of the target fades in | client route change |
| hide/reveal | **hide-on-scroll-down / reveal-on-scroll-up** on the active scroll view (translateY + fade, `spring.default`); island never covers the last row (content pads by island height) | scroll direction |
| F | SR focus order: Home→Stock→Chat→Screener→Settings, each an accessible tab with `aria-label`/`accessibilityRole="tab"` + selected state | SR nav |
| Live | Chat tab may carry an unread-thread dot; Settings a pending-alert dot (glyph + count, not color-alone) | `turn.end` bg / alert fire |

- **Safe-area:** island bottom = `env(safe-area-inset-bottom)` / `useSafeAreaInsets().bottom`; top bar
  = top inset. Island sits **above** the home-indicator, never under it.
- **Reduced-motion:** island **stays fixed** (no hide-on-scroll), pill **snaps** to target (Tier 2), no
  press-scale. Reduced-transparency → solid `--surface` fill.
- **Haptic:** light selection tick on tab switch (§7); **none** on the raised Chat tab beyond the tick.
- ✓ Unread/pending badge counts served by `GET /notifications/badge`
  ([../backend/api-reference.md](../backend/api-reference.md) §7) — glyph + count, never color-alone.

### 2.2 Top bar (every tab)

股票搜尋 + **☆ 自選** + notifications bell ([README §2](README.md#2-bottom-tab-navigation-max-5)). Glass,
blurs over scrolling content; **collapses/condenses on scroll-down** (title shrinks), reveals on up.
Bell shows unread notification dot (glyph + count). `aria`/SR: search is a labelled button opening the
search sheet; ☆ opens the watchlist; bell opens the Notifications inbox (`/notifications`).
Reduced-transparency → solid.

- **☆ Watchlist (the no-tab entry, one tap from anywhere):** tapping the top-bar ☆ opens the full
  watchlist as a **drag-snap bottom-sheet** (detents peek·half·full — §3 gesture catalog), tab island
  visible beneath; swipe-down / backdrop / ☆-again dismisses. State/contents contract = §5.2. The ☆
  carries an optional live "movers" dot when a watched symbol crosses a user alert (glyph, not
  color-alone). Reduced-motion → sheet snaps (no spring); SR → labelled button, sheet is a focus-trapped
  dialog with `Esc`-equivalent dismiss. Mirrors web ☆-toggles-the-right-rail ([../web/interaction-spec.md](../web/interaction-spec.md) §2.1).

### 2.3 Stock tab (landing) → Stock Detail (pushed full-screen view)

The **Stock tab** is a landing surface (symbol search box + recent + browse), **not** the detail
view. Tapping a symbol **pushes** Stock Detail onto the Stock tab's stack.

| Surface | State | Behaviour | Driver |
|---|---|---|---|
| **Stock landing** | D | search box (focuses the keyboard) + recent symbols + browse (sector/index lists); each result row → push Detail | `/search`, recent (local) |
| | L | shimmer result rows | pending |
| | E (cold) | "搜尋台股代號或名稱" + recent-empty hint | new user |
| | Err | inline retry on search | REST error |
| **Stock Detail** (pushed) | enter | **native stack push** (slide-in from right, `spring.default`); back button + **edge back-swipe** to pop | row tap / deep-link |
| | back-gesture | iOS interactive edge-swipe / Android system back pops the stack; the tab island stays put underneath | user |
| | deep-link | push/LINE/marquee/Home-card tap opens Stock Detail **on top of the current tab's stack** (preserves the tab you were in); a cold deep-link opens Home→push Detail so back lands somewhere sane | notification tap / route |

- **Reduced-motion:** push uses a cross-fade, no slide; back-swipe still functions (no parallax).
- **Section-stack scroll** inside Detail = §5.6.

### 2.4 Tab-switch & horizontal gestures

- **No horizontal swipe between the 5 tabs** (it fights back-swipe and the marquee; tab changes are
  tap-only). **Horizontal swipe is reserved** for: in-section segmented controls (技術 timeframe
  日/週/月, §5.6) and the Calendar month pager (§5.4, on the pushed Calendar surface), each an explicit
  pager, not a global tab swipe.
- Sub-tab / segmented rows inside a screen inherit the web [§2.9](../web/interaction-spec.md#29-two-row-market-tabs--category-sub-tabs-63-marketmd-1)
  tablist contract (arrow/swipe, `layoutId` glide, persisted per user).

---

## 3. Gesture catalog

Each gesture: **trigger → feedback → haptic → a11y → reduced-motion**. Ties to data-freshness where a
gesture touches cache. Native = `gesture-handler` + `reanimated`.

| Gesture | Trigger · feedback | Haptic | A11y | Reduced-motion |
|---|---|---|---|---|
| **Pull-to-refresh** | pull past threshold on a scroll surface → spinner-in-header → **background revalidate** of that surface's keys (NOT a hard reload — [data-freshness §6](../web/data-freshness.md#6-freshness-without-jank--performance--state-management-posture)); last-good value stays, updates in place | **light** tick at threshold-cross only | SR: `accessibilityRole` refresh control; announce "已更新" on completion (polite) | keep function; no rubber-band overscroll animation, spinner static |
| **Swipe row actions (watchlist)** | horizontal swipe on a StockRow reveals ☆ star / 🔔 alert / 🗑 remove; short-swipe reveals, full-swipe triggers primary | **light** on reveal, **success** on commit, **warning** on destructive | actions also reachable via long-press menu + SR actions (`accessibilityActions`); 44px each | actions snap open (no slide tween); functional |
| **Swipe thread-list row** | swipe a chat thread → 🗑 delete / 📌 pin | light reveal / success commit | SR custom actions | snap open |
| **Bottom-sheet** | drag-up handle or trigger opens sheet (screener filters, sort, symbol quick-actions); snap points (peek / half / full); drag-down or backdrop-tap dismisses | light on snap-point cross; none on dismiss | **focus-trap** inside sheet; open moves SR focus to sheet title; **back-button / swipe-down = Esc-equivalent**; backdrop `accessibilityViewIsModal` | sheet appears at target snap instantly (no spring); backdrop fade kept (Tier 1) |
| **Long-press (context)** | long-press a StockRow / marquee item / message → context menu (加入自選 / 設提醒 / 問 Miru / 複製) | **medium** on menu open | SR: exposed as `accessibilityActions` on the element (long-press is not discoverable to SR alone) | menu appears instantly |
| **Long-press mic (Chat)** | press-and-hold mic = push-to-talk; release → transcribe → **confirm-before-send** (§7) | light on start, light on release | SR: mic is a labelled button; tap-toggle alternative for motor a11y | voice-viz static, function unchanged |
| **Horizontal pager** | 技術 timeframe swipe / Calendar month swipe (§2.4) | light on page-commit | `role=tablist`/pager `accessibilityRole`; arrow-equivalent | page snaps, no slide |
| **Section-stack scroll** (Stock Detail) | vertical scroll through stacked sections; sticky AssetHeader condenses | none | headings are SR landmarks (section order §5.6) | sticky condense is a snap, not a tween |

- **Pull-to-refresh surfaces:** Home blocks, Watchlist, Calendar, Stock Detail, Screener results,
  Notifications inbox. **It does not force-refresh a value inside its freshness budget** — a class
  already fresh returns a cheap `304` via `as_of`/`ETag` ([data-freshness §3](../web/data-freshness.md#3-cache-key--versioning-alignment)),
  so the gesture never causes a refetch storm or resets scroll.
- **Live-stream surfaces (Chat, marquee, live quotes) are not pull-to-refresh targets** for the
  stream itself — SSE is the freshness; pull only revalidates the underlying cold data.

---

## 4. Full-state legend & inheritance

Every per-screen contract below uses the **shared** `LoadingState` / `EmptyState` (cold-start vs
query-empty) / `ErrorState` classes verbatim from [web §4](../web/interaction-spec.md#4-full-state-discipline-reusable-contracts):
- **Loading** = shaped skeleton always, **no bare spinner**; narration line **only** on
  analysis-generation ([web §4.1](../web/interaction-spec.md#41-loadingstate--skeleton-always-narration-only-for-generated-analysis)).
- **Empty** — **cold-start** (user has nothing → routes to Chat / prompt chips) vs **query-empty**
  (data absent → calm inline + keep frame + `<Caveat>`, **no chat CTA**) ([web §4.2](../web/interaction-spec.md#42-emptystate--two-classes-do-not-conflate)).
- **Error** — inline, calm, retryable; source-health degrade → honest `<Caveat>`, never blank
  ([web §4.3](../web/interaction-spec.md#43-errorstate--inline-retryable-honest)).
- **`aria-live`** discipline inherited from [web §3.3](../web/interaction-spec.md#33-aria-live-discipline)
  (polite on final answer only; off on ticks/streaming/re-rank churn).
- **Offline** adds a fourth axis to every data surface (§6): last-good cached value + stale `<Caveat>`,
  **never blank**.

---

## 5. Per-screen interaction contracts

Tight state tables for the mobile-unique surfaces (the UI Designer documents visuals per screen).
Component-level behaviour (gauge, chart, StockRow, streaming message, teaser) is **inherited** from
[web §2](../web/interaction-spec.md#2-component-interaction-contracts) — referenced, not restated.

### 5.0 Stock tab (search / recent / browse landing → pushes Stock Detail)

The **Stock tab** landing (full nav-shell contract in §2.3). Symbol-first entry point that replaces
the old Watchlist tab slot; the detail view is **pushed**, not the tab itself.

| State | Behaviour | Driver |
|---|---|---|
| D | search box (type-ahead symbol/name) + **recent symbols** + **browse** (sector / index / 熱門 lists); result/recent row tap → **push Stock Detail** (§2.3, §5.6) | `/search`, recent (local) |
| typing | debounced type-ahead results; `aria-live=polite` on result-count change | search endpoint |
| L | shimmer result / browse rows | pending |
| E (cold) | "搜尋台股代號或名稱" prompt + empty-recent hint (no chat CTA — it's a tool surface) | new user |
| E (query) | "查無此代號" calm inline, keep the search frame | empty search |
| Err | inline retry on the search field | REST error |
| refresh | pull-to-refresh revalidates browse lists (§3) | user pull |

- ✓ Symbol-search type-ahead served by `GET /search?q=&market=` (代號/名稱/產業 → `{symbol, name,
  market, asset_class, sector}`, [../backend/api-reference.md](../backend/api-reference.md) §4).

### 5.1 Home tab (marquee + ranked blocks — [home.md](home.md), [README §4](README.md#4-home-content-blocks--ranked))

| State | Behaviour | Driver |
|---|---|---|
| D | sticky 股票跑馬燈 marquee under top bar (inherits [web §2.8](../web/interaction-spec.md#28-marquee-75)); single-column ranked blocks (大盤概況 → 我的自選 → 今日事件 → AI精選 → 最近對話 → 快速提問 → 夥伴) | `get_market_context`, `/memory`, `/calendar`, `/analytics/rankings`, `/threads` |
| L | per-block shaped skeleton (plain-data class); AI精選 ranked cards use analysis-narration if computing | pending fetch / `data.updated` |
| E (cold) | first-run → prompt-starter chips route to Chat; empty watchlist block → server-seeded preset | new user |
| E (query) | a block with no data → calm inline, keep frame + caveat (no chat CTA) | empty payload |
| Err | per-block `<ErrorBoundary>` + retry chip; one block failing never blanks Home | REST error |
| Live | marquee + 我的自選 tick-flash `--up-weak`/`--down-weak` (Premium stream); off-hours → static last-close | `quote` (module 16 §2a) |
| refresh | pull-to-refresh → background revalidate all visible blocks (§3) | user pull |

- 夥伴 block = cosmetic Sisi + streak, **compact/cornered**, its region only.

### 5.2 Watchlist surface — top-bar ☆ bottom-sheet / embedded (NOT a tab) ([watchlist.md](watchlist.md))

Watchlist is **no longer a tab** but reachable **one tap from anywhere**. Three entries: (a) **top-bar
☆ → drag-snap bottom-sheet** (§2.2 — the primary monitor entry, any tab, island stays beneath); (b)
the zero-tap **我的自選 block on Home** (§5.1), whose "查看全部" can also **push** a full-screen view;
(c) quick-access on the Stock tab. The contract below is the full surface; the Home block is a capped
preview of it.

| State | Behaviour | Driver |
|---|---|---|
| enter (☆) | top-bar ☆ → **bottom-sheet** opens to the half detent (`spring.default`), drag to full/peek; swipe-down/backdrop/☆-again dismisses; reduced-motion → snap | ☆ tap (any tab) |
| enter (push) | alt: "查看全部" **pushes** a full-screen view onto the stack (slide-in, edge-back to pop §2.3) | Home 更多 tap |
| D | grouped StockRows (inherit [web §2.4](../web/interaction-spec.md#24-stockrow--watchlist-row-78--incl-drag-reorder)); price · ▲▼% · SectorChip; `DataFreshnessBadge` (EOD vs 即時) | `/memory` + `/quote(/stream)` |
| swipe | row swipe → ☆/🔔/🗑 (§3); **long-press = reorder** (drag handle lift, others reflow `spring.default`) — mobile substitute for web drag-grip | lexorank write |
| L | shimmer rows | pending |
| E (cold) | empty group → "點 ☆ 或問 Miru 加入"; empty view → server-seeded preset (not blank) | `GET /watchlists` |
| Err | reorder 409/`version` CAS → row snaps back + toast ([web §2.4](../web/interaction-spec.md#24-stockrow--watchlist-row-78--incl-drag-reorder)); star is idempotent optimistic | reorder CAS fail |
| Live | row bg tick-flash; needle/score dots do **not** re-animate on tick | `quote` |
| Offline | last-good quotes + stale `<Caveat>`; queued star/alert reconcile on reconnect (§6) | offline |

- **Keyboard/SR reorder:** focus row → activate → move → drop, announced `aria-live=polite` (inherit
  web). Live ticks `aria-live=off`.

### 5.3 Chat tab (streaming + voice — [chat.md](chat.md), module [07](../backend/modules/07-conversation-streaming.md)/[13](../backend/modules/13-chat-interaction-features.md))

| State | Behaviour | Driver |
|---|---|---|
| activity | live narration line; Sisi subtly pulsing (companion region) | `activity`, `agent.progress` |
| streaming | tokens fade+rise (`.stream-chunk`) appended by `node_id` (inherit [web §2.5](../web/interaction-spec.md#25-streaming-chat-message-73-module-07)) | `message.delta` |
| thinking | collapsed behind **顯示過程** disclosure; not rendered by default; `aria-live=off` | `thinking.delta` |
| parallel lanes | **collapsed to a single narration track** on mobile (not side-by-side); each parallel agent adds a line; **顯示過程** disclosure expands the per-agent detail | `agent.spawned/progress/done` |
| voice | mic (long-press §3 or tap) → `/voice/transcribe` → transcript in composer → **confirm-before-send** (§7), never auto-fire | module 13 §voice |
| draft | composer text is a **per-thread draft** that survives backgrounding/kill (§6) | local persist |
| steering | mid-run send → "已排入，本輪結束後處理" chip (no cancel) | `steering.accepted` |
| done | caveat badge pinned; `follow_up` chips; **`aria-live=polite` fires here only** | `turn.end` |
| Err | per-track inline error; `retryable` → retry chip | `error{retryable}` |
| Live/resume | dropped connection → `Last-Event-ID` resume, replayed events materialize instantly (no re-type, no dup lanes) — [web §3.2](../web/interaction-spec.md#32-last-event-id-resume--visually-seamless) | reconnect |

- **rich_card** inside a thread inherits [web §2.6](../web/interaction-spec.md#26-rich_card-module-07); an embedded
  gauge/chart/StockRow inherits its component contract.
- **Reduced-motion:** tokens instant, Sisi static, narration lines still render.

### 5.4 Calendar surface — pushed / embedded (NOT a tab) ([calendar.md](calendar.md), module [18](../backend/modules/18-calendar-events.md))

Calendar is **no longer a tab**. It appears via: (a) the **今日事件 block on Home** (§5.1) — tap "查看行事曆"
→ **pushes** the full timeline; (b) the **事件 section** on Stock Detail (§5.6, symbol-scoped subset).
The contract below applies to the pushed full timeline.

| State | Behaviour | Driver |
|---|---|---|
| enter | **pushed** onto Home's stack (edge-back to pop §2.3) | "查看行事曆" tap |
| D | vertical timeline of 除權息/配息/法說 for watchlist; month pager (horizontal swipe §2.4) | `/calendar` |
| L | shimmer timeline rows | pending |
| E (query) | "近期無事件" calm inline + caveat (no chat CTA) | empty range |
| Err | inline retry | REST error |
| tap | event → Stock Detail (pushed §2.3) or event detail sheet | route |
| refresh | pull-to-refresh revalidates the visible month | user pull |

- 存股 core loop; `資料日期` shown where backend exposes it.

### 5.5 Notifications inbox (top-bar bell, `/notifications`)

| State | Behaviour | Driver |
|---|---|---|
| D | list of received alerts/pushes; unread styling (glyph, not color-alone) | `/notifications` |
| tap | **deep-links into the target screen** (§7 push) — a price alert → that Stock Detail | tap-through |
| E (cold) | "尚無通知" | new user |
| Live | new alert arrives → row inserts (no celebratory motion); bell dot updates | alert fire |

### 5.6 Stock Detail section-stack ([stock-detail.md](stock-detail.md), [README §2](README.md#everything-else-lives-here))

Full-screen pushed view (§2.3); **scrollable section stack**, sticky AssetHeader:
`quote → 多空 gauge → 技術 (single chart + indicator chips) → 智能摘要/SWOT teaser → 事件 → 籌碼/新聞`.

| Section | State / behaviour | Driver |
|---|---|---|
| **AssetHeader** (sticky) | inherits [web §2.15](../web/interaction-spec.md#215-assetheader-stock-detail-sticky-header): price + ▲▼% tick-flash; 多空 band pill does **NOT** re-animate on tick; ☆ optimistic; condenses on scroll | `/quote` + `/analytics/{s}/score` |
| **多空 gauge** | inherits [web §2.3](../web/interaction-spec.md#23-多空-score-gauge-71): needle eases **once** on load / new score fetch, never on a tick or a pull-refresh; caveat footer | `/analytics/{s}/score` |
| **技術** | single `ChartWithDataSheet` ([web §2.2](../web/interaction-spec.md#22-chartwithdatasheet-711--the-analytical-primitive)) + indicator **chips** (not multi-pane); **crosshair via touch-drag** on chart syncs the data row; 日/週/月 + range = horizontal pager (§2.4), one refetch | endpoint refetch |
| **智能摘要 / SWOT** | 馬賽克 teaser for Free (inherit [web §2.7](../web/interaction-spec.md#27-馬賽克-teaser-74-module-07-4)); frame sharp, value blurred, one calm 升級 CTA | entitlement |
| **事件** | mini timeline (subset of Calendar §5.4) | `/calendar` |
| **籌碼 / 新聞** | 籌碼 tables compressed to cards; sponsor factors (分點/官股) → 馬賽克 if Free; 新聞 list | chip/news endpoints |
| L / E / Err | per-section skeleton / query-empty inline+caveat / inline retry (§4) | — |
| 問 Miru | floating button seeds chat with the symbol → jumps to Chat tab with prefilled composer draft | route |

- **Touch crosshair** replaces hover: press-drag on the chart moves `activeIndex`, syncs the data
  row; reduced-motion → crosshair snaps, no ease. Non-trading: no buy/sell markers.

### 5.7 Screener tab — card-list (now a PRIMARY tab, was under Settings — [screener.md](screener.md), [README §2](README.md#everything-else-lives-here))

Screener is now a **top-level tab** (promoted from the Settings hub). Mobile compresses the web funnel
table ([web §2.12](../web/interaction-spec.md#212-screener-funnel--builder-gates-run-results)) to a
**preset-pick → ranked card list**; filters live in a **bottom-sheet**, not a wide builder.

| State | Behaviour | Driver |
|---|---|---|
| D | preset chips (存股/成長/…) → ranked **cards** (not table rows); each card: 代號名稱 · score band · factor chips; `DataFreshnessBadge` "最後執行 14:32" | `POST /analytics/screener/run` |
| filters | **bottom-sheet** (§3): conditions + gates + sort; **執行篩選** applies (idle→working→done), ranking on Apply not per-keystroke | `run_screener` |
| survivor count | "符合 N 檔" re-probes debounced; `aria-live=polite` on user change, `off` on bg | count probe |
| L | shaped shimmer cards + narration "Miru 正在跑篩選…" (analysis class) | pending run |
| E (query) | "無符合條件的個股，放寬篩選?" + "放寬 gates" chip → reopens sheet (**not** a chat CTA) | empty result |
| Err | inline calm retry; source-health degrade → `<Caveat>` | REST error |
| card tap | → Stock Detail (pushed) | route |
| sponsor factor | 分點/官股/主動ETF → 馬賽克 card value layer for Free | entitlement |

- **Non-trading:** header caveat 「符合條件的清單，非推薦名單」 pinned; US/ETF unsupported → "尚未支援" on run.
- ✓ `SavedScreens` CRUD → `/analytics/screener/screens` ([../backend/api-reference.md](../backend/api-reference.md) §4a).

---

## 6. Offline / flaky-network / lifecycle (the mobile-critical layer)

Extends [data-freshness.md](../web/data-freshness.md) with the mobile realities web doesn't face:
network loss, backgrounding, kill/relaunch. **One value = one owner** and the monotonic
`as_of`/`event_seq`/`version` discard rule ([data-freshness §3](../web/data-freshness.md#3-cache-key--versioning-alignment))
carry over unchanged.

| Situation | Behaviour | Grounding |
|---|---|---|
| **SSE connection drop** | a non-event: reconnect sends `Last-Event-ID`, server replays by `event_seq`; replayed deltas append by `node_id`, **entrance animation suppressed** → no re-type, no flicker, no dup lanes | [data-freshness §4](../web/data-freshness.md#4-invalidation--push-not-just-ttl) / [web §3.2](../web/interaction-spec.md#32-last-event-id-resume--visually-seamless) |
| **Offline read** | show **last-good cached values** (RSC/TanStack persisted cache) + a stale `<Caveat>` "離線，顯示最後可得"; **never blank a panel**; `DataFreshnessBadge` reflects age | [data-freshness §8](../web/data-freshness.md#8-freshness-ux) |
| **Per-thread draft persistence** | composer text is persisted per `thread_id` locally; survives app background/kill/relaunch; restored on thread open (module 13 §3) | [README §3](README.md#3-web--mobile-divergence) |
| **Queued actions offline** | star/alert/reorder apply **optimistically**, queue while offline, **reconcile on reconnect** with `version` CAS → 409 → snap back + toast; idempotent star (`PUT … ON CONFLICT DO NOTHING`) | [data-freshness §6](../web/data-freshness.md#6-freshness-without-jank--performance--state-management-posture) |
| **Background → foreground** | on resume, **revalidate only visible surfaces** (background revalidate, keep last-good, update in place) — **no refetch storm**, no blank; reopen the SSE with `Last-Event-ID` | [data-freshness §6](../web/data-freshness.md#6-freshness-without-jank--performance--state-management-posture) |
| **Prolonged background** | live SSE released to save battery; on foreground, resume by `Last-Event-ID` (forward-only, can't rewind state) | data-freshness §4 |
| **Market-closed** | live layer idle (poller stops, module 16 §2a); marquee/quotes show **static last-close**, badge = EOD — never a frozen "live" value | [data-freshness §8](../web/data-freshness.md#8-freshness-ux) |
| **Flaky / slow network** | single-flight dedupe per key; skeleton **only** on true cold load, background revalidate shows no spinner | data-freshness §6 |

### 6.1 Live-quote battery / data-saver policy (mobile extension of the demand-driven poller)

The backend live feed is a **shared demand-driven poller** — it runs while there is a subscriber and
auto-stops on the last disconnect (module 16 §2a). Mobile is the client that must be a good citizen:

| Condition | Live-layer posture | Grounding |
|---|---|---|
| **App backgrounded** | **pause / release the `/quote/stream` subscription** (drops the client off the shared poller → contributes to auto-stop, saves battery + data); no ticks paint while hidden | module 16 §2a demand-driven |
| **Foreground resume** | reopen the stream with `Last-Event-ID`; **background-revalidate only visible surfaces** (keep last-good, update in place, flash the tick) — **no refetch storm**, no blank | [data-freshness §6](../web/data-freshness.md#6-freshness-without-jank--performance--state-management-posture) |
| **Data-saver / metered cellular** | **throttle to a slower cadence or fall back to EOD** (`DataFreshnessBadge` states which); the surface degrades honestly to a static last-value, never fakes 即時 | data-freshness §8 (honest degrade) |
| **Not-visible surfaces** | only the on-screen surfaces subscribe; scrolled-off rows don't hold a tick subscription | one-value-one-owner (data-freshness §6) |

- Detected natively via `AppState` (background/active) + `@react-native-community/netinfo`
  (`isConnectionExpensive` / metered). **Non-trading:** throttling/EOD-fallback changes *cadence*,
  never the value's meaning; the gauge/needle rule (no re-anim on a tick) is unaffected by cadence.
- ✓ **Data-saver cadence tier** — the poller accepts a `cadence: normal|saver` subscribe hint
  (~20–30s on metered/low-power), distinct from the ~5s default
  ([../backend/modules/16-infrastructure.md](../backend/modules/16-infrastructure.md) §2a); a
  backgrounded app unsubscribes entirely.

- ✓ **Offline-queue reconcile** — each queued client action carries an `op_id` (idempotency) + the
  entity `version` it was based on; the client replays in order, server dedupes by `op_id` and applies
  the existing per-entity CAS (409 surfaces as a per-op conflict to re-read)
  ([../backend/modules/16-infrastructure.md](../backend/modules/16-infrastructure.md) §10). Queue lives
  client-side; no new server primitive.

---

## 7. Voice + haptics + push

### 7.1 Voice (module [13](../backend/modules/13-chat-interaction-features.md) §voice)

| Step | Behaviour |
|---|---|
| capture | mic (tap or long-press push-to-talk §3); voice-viz bars animate on active mic (Tier 4 → static icon under reduced-motion) |
| transcribe | audio → `/voice/transcribe` → transcript into composer |
| **confirm-before-send** | **never auto-fires a turn**; transcript is editable; **low-confidence tokens highlighted**; a **2330→2380 mis-hear must be catchable** before send |
| ambiguous symbol | resolver unsure → `ask_user` clarification chips inline (not a silent guess) |
| TTS read-aloud | if the answer is spoken, **the caveat MUST be spoken too** (governance §8) — TTS never reads the analysis without the non-trading/currentness caveat |

### 7.2 Haptic map (subtle, non-trading, never magnitude-scaled)

| Event | Haptic | Rule |
|---|---|---|
| tab switch / sheet snap / pull threshold | **selection / light** | orientation feedback only |
| action confirm (star added, alert created, draft sent) | **light success** | one-shot |
| destructive commit (remove, delete) | **warning** | with the confirm affordance |
| error (failed send, 409 snap-back) | **error** | pairs with the inline error, not a substitute |
| **price/score tick** | **none** | a tick is **never** a haptic (would imply a signal / magnitude) |
| gacha/pet celebration | cosmetic pop haptic | **Chat companion region only**, honors reduced-motion |

Reduced-motion / system-haptics-off → all non-essential haptics suppressed; nothing depends on
haptic alone (always paired with visual + SR).

### 7.3 Push / LINE deep-linking (module [14](../backend/modules/14-scheduler-alerts.md))

| Step | Behaviour |
|---|---|
| delivery | **LINE is the primary TW channel**; native push (APNs/FCM) where a store app is installed |
| tap-through | notification tap **deep-links into the right screen** — price/event alert → that **Stock Detail** (pushed §2.3, symbol from payload); a thread reply → that Chat thread; a digest → Home |
| cold tap | app launched from a notification opens the target with a sane back-stack (Home underneath) |
| inbox | every push is also in the Notifications inbox (§5.5) — receive-and-tap-through, not fire-and-forget |

**Notification re-entry is a MODE, not a new tab** (per `ux-foundation.md`'s "Wei" re-entry user):
the tap resolves a payload `{route, symbol, thread_id}` to a landing target within the existing IA —
no dedicated screen.

| Re-entry step | Behaviour |
|---|---|
| resolve | map payload `{route, symbol?, thread_id?}` → target: `stock_detail`→push Detail(symbol) · `thread`→open Chat(thread_id) · `alert`/`digest`→Home; unknown/expired → Home + toast, never a crash |
| **cold-land (app was killed)** | while the target's data loads, render the **shaped skeleton for that screen + last-good cached value if present** (§4 loading, §6 offline) — **never a blank screen or a spinner-only splash**; the AssetHeader/gauge/chart hydrate in place as fetches resolve |
| warm-land (backgrounded) | target already has cached data → show last-good instantly, background-revalidate (§6.1 foreground resume) |
| back from cold-land | back lands on Home (sane stack §2.3), not a dead-end |
| reduced-motion | push→pop uses cross-fade, no slide (§2.3); no celebratory entrance on the landed alert |

- ✓ **Push-token registration** — `POST/DELETE /me/devices {platform, token}`; payload deep-link
  `{route, symbol?, thread_id?}` resolved to the target screen; native APNs/FCM is a `DeliveryChannel`
  alongside LINE ([../backend/modules/14-scheduler-alerts.md](../backend/modules/14-scheduler-alerts.md) §4,
  [../backend/api-reference.md](../backend/api-reference.md) §7).

---

## 8. A11y (mobile)

Inherits web [§5](../web/interaction-spec.md#5-a11y-interaction-rules) + [§3.3](../web/interaction-spec.md#33-aria-live-discipline);
mobile emphasis:

- **≥44px touch targets**, one-handed reach: tab island cells, composer mic, swipe actions, sheet
  handle, chips, CTAs. Hover-reveal patterns become **swipe/long-press** (discoverable to SR via
  `accessibilityActions`, since a swipe/long-press is invisible to a screen reader otherwise).
- **Screen-reader order:** tab island Home→Stock→Chat→Screener→Settings (`accessibilityRole="tab"` +
  selected); bottom-sheets **focus-trap** (`accessibilityViewIsModal`), open moves focus to the sheet
  title, **back-button / swipe-down = Esc-equivalent**, dismiss returns focus to the trigger. VoiceOver
  (iOS) + TalkBack (Android) both verified paths.
- **`prefers-reduced-motion` tiers** ([web §1.2](../web/interaction-spec.md#12-reduced-motion-tiers-what-collapses)):
  island stops hide-on-scroll + pill snaps (Tier 2); gestures keep function, drop tween; gacha/pet
  static (Tier 5). Driven natively by `AccessibilityInfo.isReduceMotionEnabled`.
- **`prefers-reduced-transparency`** → glass island / top bar / bottom-sheet fall back to solid
  `--surface` (iOS `isReduceTransparencyEnabled`).
- **Dynamic-type / large-font:** layouts reflow (single-column, wrapping chips); the island labels,
  caveat footer, and ▲▼ signs must not clip or truncate the direction indicator.
- **紅漲綠跌 never color-alone:** ▲/▼ + sign + `accessibilityLabel` carrying direction+value
  ("上漲 1.45%") — the ~8% red-green-blind and SR users get direction without color.
- **`aria-live` etiquette:** polite on the final chat answer only; **off** on ticks / streaming /
  auto re-rank; **polite** on user-initiated reorder + pull-refresh completion (inherited).
- **Haptics never sole channel:** every haptic pairs with a visual + SR cue (§7.2).

---

## 9. Cross-references

- **Web contract this extends:** [../web/interaction-spec.md](../web/interaction-spec.md) §1 (motion),
  §2 (components), §3 (SSE live layer), §4 (full-state), §5 (a11y).
- **Tokens + visuals (source of truth):** [../web/design-system.md](../web/design-system.md) §5 (motion),
  §6.1 (mobile island), §7 (components).
- **Cache coherence (inherited + extended §6):** [../web/data-freshness.md](../web/data-freshness.md).
- **Mobile IA + per-screen visuals:** [README.md](README.md); `ux-foundation.md` (UX Researcher);
  [home.md](home.md), [watchlist.md](watchlist.md), [chat.md](chat.md), [calendar.md](calendar.md),
  [settings.md](settings.md), [stock-detail.md](stock-detail.md), [screener.md](screener.md).
- **Drivers (backend):** SSE envelope [modules/07](../backend/modules/07-conversation-streaming.md),
  live-quote feed [modules/16 §2a](../backend/modules/16-infrastructure.md), voice/drafts/branch
  [modules/13](../backend/modules/13-chat-interaction-features.md), LINE/alerts push
  [modules/14](../backend/modules/14-scheduler-alerts.md), calendar
  [modules/18](../backend/modules/18-calendar-events.md), pet cues
  [modules/21](../backend/modules/21-pet-gamification.md), governance
  [governance.md](../backend/governance.md).

### Backend capabilities this doc depends on (now specified)

All resolved in the backend spec:
1. ✓ **Push-token registration + deep-link payload** — `POST/DELETE /me/devices`, payload
   `{route, symbol?, thread_id?}` ([../backend/modules/14-scheduler-alerts.md](../backend/modules/14-scheduler-alerts.md) §4,
   [../backend/api-reference.md](../backend/api-reference.md) §7) — §7.3.
2. ✓ **Offline-queue reconcile** — client `op_id` idempotency + per-entity `version` CAS (composes
   existing primitives; queue is client-side) ([../backend/modules/16-infrastructure.md](../backend/modules/16-infrastructure.md) §10) — §6.
3. ✓ **Badge count source** — `GET /notifications/badge` `{unread_notifications, unread_threads, pending_alerts}`
   ([../backend/api-reference.md](../backend/api-reference.md) §7) — §2.1.
4. ✓ **Shared token-export** — single `:root` token source → generated RN theme, no drift
   ([../../web/CLAUDE.md](../../web/CLAUDE.md) §2) — §1.1.
5. ✓ **`SavedScreens` CRUD** — `/analytics/screener/screens` ([../backend/api-reference.md](../backend/api-reference.md) §4a) — §5.7.
6. ✓ **Data-saver poller cadence** — `cadence: normal|saver` subscribe hint
   ([../backend/modules/16-infrastructure.md](../backend/modules/16-infrastructure.md) §2a) — §6.1.
7. ✓ **Symbol-search endpoint** — `GET /search?q=&market=`
   ([../backend/api-reference.md](../backend/api-reference.md) §4) — §5.0.
