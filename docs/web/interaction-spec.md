# 了然 · Miru — Interaction / Motion / State Spec (web)

The **connective interaction contract** for the web client: how the finalized visuals in `docs/web/`
*behave, move, and transition*. The visual language (palette, type, components, the marquee, the 多空
gauge, `ChartWithDataSheet`, motion tokens) is finalized elsewhere — this doc is the layer those docs
don't cover.

> **This doc EXTENDS [design-system.md](design-system.md) §5 (motion) + §7 (signature components) +
> §7.11 (`ChartWithDataSheet`).** design-system.md is the **single source of truth for tokens and
> visuals**; the spring/duration tokens live in §5 and are *referenced*, never redefined, here. Page
> behaviour aligns to [README.md](README.md), [market.md](market.md)+[market/](market/),
> [stock-detail.md](stock-detail.md)+[stock-detail/](stock-detail/), [chat/README.md](chat/README.md),
> [watchlist/README.md](watchlist/README.md), [settings/README.md](settings/README.md). The events
> that *drive* interactions come from the backend: SSE envelope
> [modules/07](../backend/modules/07-conversation-streaming.md), live-quote feed
> [modules/16 §2a](../backend/modules/16-infrastructure.md), voice/retry/edit/branch
> [modules/13](../backend/modules/13-chat-interaction-features.md), pet cues
> [modules/21](../backend/modules/21-pet-gamification.md), personality
> [modules/23](../backend/modules/23-agent-personality.md).

---

## 0. Non-negotiables (enforced by every contract below)

1. **Non-trading motion.** No motion may imply a signal. **No magnitude-scaled motion** (a bigger move
   is *not* a bigger animation), **no launch/celebratory motion on price/score**, and the **多空 gauge
   needle never re-animates on a quote tick** (it eases only on load / on a *new score fetch*). Motion
   is feedback + streaming life + state change — never a recommendation.
2. **紅漲綠跌** (`--up`=漲 red / `--down`=跌 green) is applied **only** to price/%/score/candles and
   **never color-alone** — always **▲▼ + sign + `aria-label`**. `--accent` teal is chrome-only.
3. **Cosmetic pet motion stays in its region** (Sisi corner / gacha modal / Settings). It never enters
   the analysis area and never colours or annotates data (module 21 §governance).
4. **Every motion above level 3 honours `prefers-reduced-motion`** (see §1.2 tiers) → instant/static.
5. **`aria-live` discipline:** `polite` on the final answer only; **`off`** on thinking, ticks,
   marquee, and re-rank churn (see §3.3).

---

## 1. Motion system

### 1.1 Tokens (consolidated reference — defined in [design-system.md §5](design-system.md))

| Token | Value | Use |
|---|---|---|
| `spring.default` | `type:spring, stiffness:120, damping:22` | shared-layout glides, gauge needle, disclosure height |
| `dur.tick` | ~140ms ease-out | live price-tick flash rise |
| `dur.tick-fade` | ~600ms ease-out | tick flash fade back to surface |
| `dur.stream` | 0.45s ease-out | streaming-token fade+rise (`.stream-chunk`) |
| `dur.rail` | 0.3s ease-out | rail collapse width tween (`transition-all duration-300`) |
| `dur.hover` | 0.2–0.3s ease | card hover lift, control hover |
| `dur.shimmer` | 1.5s ease-in-out loop | skeleton shimmer |
| `dur.flow` | 0.7s ease-out forwards | supply-chain dash-draw |
| `dur.marquee` | ~30s linear infinite | ticker scroll |
| `ease.overshoot` | `cubic-bezier(0.34,1.56,0.64,1)` | **gacha/pet only** (pop) — never on data |

### 1.2 Reduced-motion tiers (what collapses)

| Tier | Examples | reduced-motion fallback |
|---|---|---|
| 1 — essential feedback | focus ring, hover bg, tick flash *color* | **kept** (color change only, no transform/loop) |
| 2 — orientation | nav indicator morph, sub-tab glide, disclosure height | **snap** (instant, no tween) |
| 3 — streaming life | token fade+rise, activity pulse, shimmer | fade→**instant**; shimmer→static ghost block |
| 4 — ambient/cosmetic | marquee scroll, Sisi breathing, treemap breathe, flow-draw, voice viz | **stop** (static; marquee→manual-scroll strip) |
| 5 — celebratory | gacha shake/pop, pet level-up, confetti, ray-burst | **static reveal** (no shake/pop/overshoot) |

### 1.3 Animation catalog

| Animation | What it communicates | reduced-motion fallback |
|---|---|---|
| **Streaming-token reveal** (`.stream-chunk`, fade + rise 4px, `dur.stream`) | "the agent is producing this answer now" | tokens appear instantly, no rise |
| **Live price-tick flash** (bg → `--up-weak`/`--down-weak`, `dur.tick` in, `dur.tick-fade` out) | "this value just updated" — **weak** tint, never magnitude-scaled | keep the one-shot color flash; no animation loop (Tier 1) |
| **Marquee** (translateX loop, `dur.marquee` linear, pause on hover/touch) | "ambient quote stream" — display, not signal | static, manually horizontally-scrollable strip (Tier 4) |
| **Live re-rank** (row reorder via shared-layout / `rank-delta` fade) | "ranking changed" | rows snap to new order, no move tween; aria-live `off` |
| **Morphing nav indicator** (shared-layout `layoutId` pill glide, `spring.default`) | "you moved here / this is active" | indicator jumps to target instantly (Tier 2) |
| **Skeleton shimmer** (`.animate-shimmer`, `dur.shimmer`) | "content loading, shaped like the result" | static ghost blocks, no shimmer sweep |
| **Score-gauge needle** (eases to value on load / on new score fetch, `spring.default`) | "the computed 多空 score" — **never** re-animates on a quote tick | needle placed at value instantly |
| **Gacha / pet reveal** (`gacha-shake` → `gacha-pop` `ease.overshoot`, ray-burst/confetti) | cosmetic celebration, its region only | static card flip / instant reveal (Tier 5) |
| **Voice viz** (`.voice-bar` scaleY wave / dot-wave on active mic) | "listening / recording" | static mic-active icon, no bars |
| **Supply-chain flow-draw** (`.flow-line` stroke dash-draw, `dur.flow`) | "direction of the 上游→下游 relationship" | edges drawn static, offset 0 (Tier 4) |
| **Treemap breathe** (subtle scale/shadow on big movers) | "notable mover" — ambient only, no magnitude mapping | static cells |
| **Card hover** (lift to `--surface-2`, `dur.hover`) | "interactive / clickable" | bg change only, no lift/transform |
| **Disclosure** (顯示過程 / 展開歷史 height tween, `spring.default`) | "more detail available here" | height snaps open/closed (Tier 2) |

---

## 2. Component interaction contracts

Legend for state columns: **D**efault · **H**over · **A**ctive/pressed · **F**ocus · **L**oading ·
**E**mpty · **Err**or · **Live-update**. "Driver" = backend event/endpoint that changes state.

### 2.1 Nav rail (left) + collapse + mobile island

| State | Behaviour | Driver |
|---|---|---|
| D | expanded 240px (icon + zh + en); active = `--accent-weak` pill + 2px `--accent` edge bar | route |
| H | item bg → `--surface-2`; collapsed rail shows tooltip (`title`/floating, 1/item) | — |
| A | shared-layout indicator glides to target (`layoutId`, `spring.default`) | client route change |
| F | focus-visible 2px `--accent` ring; collapsed item keeps `aria-label` name | keyboard |
| Collapse | chevron toggle, `dur.rail` width tween 240↔64px, canvas reflows; state persists (localStorage + profile) | user; auto at 768–1279px |
| Live | Sisi footer chip reacts to `pet.reaction` (its region only) | `pet.reaction` |
- **Keyboard:** `Tab` order top→bottom; `Enter`/`Space` activate; toggle exposes `aria-expanded`.
- **Mobile island** (<768px): floating glass island; active pill morphs (`layoutId`); **hides on
  scroll-down, reveals on scroll-up**; raised ◉Chat center tab. reduced-motion → width/pill snap, no
  hide-on-scroll animation (island stays fixed).

### 2.2 `ChartWithDataSheet` (§7.11) — the analytical primitive

| State | Behaviour | Driver |
|---|---|---|
| D | chart + collapsed sheet; one `activeIndex` shared | initial fetch |
| Crosshair↔row sync | hover chart → dashed `--crosshair` follows pointer → highlights same date-row (`--surface-2`, `aria-current`); hover row → crosshair snaps to that x | pointer / row hover |
| Timeframe | `[日|週|月]` + range pills drive **one** refetch (persisted in URL); both chart+sheet update together | endpoint refetch |
| 展開/收合 | 展開歷史 ▾ height tween (`spring.default`); sheet virtualized | user |
| L | **shaped skeleton** (chart block + ghost rows), never spinner | pending fetch |
| E | "此區間無資料" inline, keeps frame + caveat | empty payload |
| Err | inline retry chip; source-health degrade → honest `<Caveat>`, not blank | `tool.result.source_health` / REST error |
| Live | candle/quote series flash `--up-weak`/`--down-weak` on tick; **axes/scale do not re-animate** | `quote` (module 16 §2a) |
- **Keyboard:** `←/→` move `activeIndex` on chart, `↑/↓` on sheet — one shared index; `Home/End`
  jump; `⤓` export (Premium; hidden when sponsor-masked). reduced-motion → crosshair snaps, no ease.
- **A11y:** `role="figure"` + labelled series; sheet `role="table"`, active row `aria-current`;
  visually-hidden data-table fallback for SR.

### 2.3 多空 score gauge (§7.1)

| State | Behaviour | Driver |
|---|---|---|
| D | needle at value; band label + numeric centered; factor micro-bars beneath | `/analytics/{s}/score` |
| L | skeleton arc + ghost factor bars | pending |
| Live | **no reaction to quote ticks** — needle static until a *new score* is fetched | (only) new score fetch |
| Update | needle eases to new value (`spring.default`); factor bars grow to width | new score payload |
- **Non-trading:** eases **once** on load; never re-animates on a tick; required caveat footer.
  reduced-motion → needle + bars placed at value instantly.
- **Keyboard/A11y:** gauge is `img`/`figure` with `aria-label="多空 偏多 72，動能78 趨勢71…"`; factor
  bars in a visually-hidden list.

### 2.4 StockRow / watchlist row (§7.8) — incl. drag-reorder

| State | Behaviour | Driver |
|---|---|---|
| D | grip · candle · 代號名稱 · SectorChip · price · ▲▼% · (spark hidden) | `/quote` + indicators |
| H | reveal sparkline + ✕ remove + ☆ (`opacity-0 group-hover:opacity-100`) | — |
| A/drag | `@dnd-kit` sortable; grip `⠿` lift → row 50% opacity; others reflow (`spring.default`) | drag → lexorank write |
| F | focus-visible ring; row focus enables keyboard reorder | keyboard |
| L | shimmer rows | pending |
| E | empty group "尚無股票，點 ☆ 或問 Miru 加入"; empty tab → server-seeded preset | `GET /watchlists` |
| Err | inline; failed reorder → row snaps back + toast (409/version — module 16 §10) | reorder CAS fail |
| Live | row bg flash `--up-weak`/`--down-weak` on tick | `quote` |
- **Drag-reorder:** lexorank fractional key (insert between neighbours, no rewrite); multi-device
  409 → jitter+retry. **Keyboard reorder:** focus row → `Space` lift → `↑/↓` move → `Space` drop /
  `Esc` cancel; **announced via `aria-live=polite`** ("2330 移至第 2 位"). reduced-motion → no drag
  transition, keyboard path unchanged.

### 2.5 Streaming chat message (§7.3, module 07)

| State | Behaviour | Driver |
|---|---|---|
| activity | live NL narration line, Sisi subtly pulsing | `activity`, `agent.progress.activity` |
| streaming | tokens fade+rise (`.stream-chunk`) appended in place by `node_id` | `message.delta` |
| thinking | collapsed behind 顯示過程 disclosure; **not** rendered by default, **`aria-live=off`** | `thinking.delta` |
| tool step | narration line per tool; tap node → args/result detail (opt-in track) | `tool.proposed/started/result` |
| parallel | side-by-side lanes on wide canvas, each keyed by `agent_id` | `agent.spawned/progress/done` |
| steering | mid-run send → pending chip "已排入，本輪結束後處理" (no cancel) | `steering.accepted` |
| done | `caveat` badge pinned; `follow_up` chips below; **`aria-live=polite` fires here only** | `turn.end`, `caveat`, `follow_up` |
| Err | per-lane inline error; `retryable` → retry chip | `error{retryable}` |
- **Composer keyboard:** `⌘/Ctrl+Enter` send · `↑` on empty = edit last user turn (branches via
  `switch-branch`, module 13) · `Esc` cancel/close. Voice mic (**Free** STT) → `/voice/transcribe`
  returns transcript → **confirm-before-send** (never auto-fires a turn); TTS read-aloud is Premium.
- reduced-motion → tokens instant, Sisi static, lanes still render (no entrance animation).

### 2.6 `rich_card` (module 07)

| State | Behaviour | Driver |
|---|---|---|
| enter | renders inline as `--surface` card in thread flow (fade, no bounce) | `rich_card{card_type,data}` |
| interactive | if it's a gauge/`ChartWithDataSheet`/StockRow, inherits that component's contract (§2.2–2.4) | — |
| Live | embedded quote piggybacks the same `/quote/stream` feed (tick flash) | `quote` |
- Governed: caveated, non-trading, no raw payloads. reduced-motion → no entrance animation.

### 2.7 馬賽克 teaser (§7.4, module 07 §4)

| State | Behaviour | Driver |
|---|---|---|
| locked | frame/headline/label columns **sharp**; value layer under `backdrop-blur` + diagonal texture + centered lock chip + one calm 升級 CTA | `tool.locked{required_tier,preview}` / sponsor cell |
| H | CTA hover → `--accent` hairline; blurred region does **not** un-blur on hover | — |
| unlock | on entitlement change, blur lifts (fade, `dur.hover`); no launch/celebration | entitlement change (per-tool, module 16 §10) |
- Turn proceeds around the masked section (never blocks). reduced-motion → blur lift is instant.
- **Not present in Settings** (config, not gated analysis).

### 2.8 Marquee (§7.5)

| State | Behaviour | Driver |
|---|---|---|
| D | seamless translateX loop, `dur.marquee` linear; watchlist + indices, then holdings | `/quote(/stream)` Premium, `/quote` EOD Free |
| H/touch | **pauses**; item hover shows pointer | — |
| Live | values update **in place** (flash `--up-weak`/`--down-weak`); **scroll never resets** on data update | `quote` |
| off-hours | last close, **static** (no animation) | market-closed |
| click | → Stock Detail | route |
- The **one permitted marquee** on the page. reduced-motion → static manual-scroll strip.
  Dismissible/collapsible per user. `aria-live=off` (ambient).

### 2.9 Two-row market tabs + category sub-tabs (§6.3, market.md §1)

| State | Behaviour | Driver |
|---|---|---|
| D | Row 1 market tabs + Row 2 view sub-tabs as segmented pill rows; active = `--accent` underline/pill | persisted per user (`kestrel_market_view_tab` pattern) |
| A | shared-layout glide (`layoutId`, `spring.default`) between tabs | user select |
| overflow | horizontal-scroll; active scrolls into view | — |
| panel | each panel is an `<ErrorBoundary>` — one failing view never blanks the page | REST error |
- **Keyboard:** `role="tablist"`, arrow-key nav, `aria-selected`, `Home/End`. reduced-motion → glide
  snaps. Same pattern for Stock Detail tabs + inner sub-tabs (`即時🔒` shows lock glyph pre-click).

### 2.10 DispositionBoard cards (§7.9)

| State | Behaviour | Driver |
|---|---|---|
| D | 4 buckets (風險/處置中/即將出關/自結發布) + 最快N日 projection; `--warn` framing | `get_disposition_stocks/_forecast`, `get_attention_stocks` |
| H | card lift to `--surface-2`; row → Stock Detail | — |
| L | shaped skeleton per bucket | pending |
| E | "此分類目前無個股" per bucket | empty |
- `--warn` is a state color (attention), **not** a 紅漲綠跌 price token; carries `<Caveat>`.

### 2.11 K-line crosshair (技術分析, `KLineChart`)

Instance of §2.2 with candle chart: crosshair follows pointer, syncs OHLCV sheet row; `←/→` step
bars; MA/KD/MACD/RSI/布林 panes share the x-axis + crosshair; 日/週/月 + range TF drives one refetch;
還原股價 toggle refetches. reduced-motion → crosshair snaps. Non-trading: no buy/sell markers, no
target lines.

### 2.12 Screener funnel — builder, gates, run, results

The behavioural contract for the funnel workbench; visuals/factor design live in screener/README.md.

| Element | State | Behaviour | Driver |
|---|---|---|---|
| **ConditionRow** | D | factor · operator · `FilterValueInput` (slider+chips+exact box) in `--surface-2` card | `get_factor_catalog` `fields[]` |
| | add | `+ 新增條件` → factor picker; on add, **focus moves to the new row's value input**; ≥2 rows reveal AND/OR toggle | user |
| | remove | ✕ (44px) removes row; focus falls to previous row (or `+ 新增條件` if none) | user |
| | Err | invalid/empty value → inline `--warn` on that row ("輸入數值"), run disabled until resolved | client validation |
| **AND/OR match** | D | SegmentedControl, appears only with ≥2 conditions; toggling re-probes count | user |
| **GateBar** | D | liquidity/price/市值 sliders + exclude 處置/注意/全額交割 pills; defaults from `SCREENER_GATES` | `run_screener {gate_overrides}` |
| **survivor count** | Live | "符合 N 檔" re-probes (debounced) on any gate/condition change; **`aria-live=polite` on user-driven change** (announce the new count), `off` on background re-poll | debounced count probe |
| **Run / Apply** | idle→working→done | explicit **執行篩選** button: idle → **working** (spinner-in-button + count freeze) → done; ranking (stage ⓒ) runs on Apply, not per-keystroke; results carry a **`DataFreshnessBadge`** ("最後執行 14:32") | `POST /analytics/screener/run` |
| **ScreenerTable** | D/sort | TanStack, sticky thead, per-market columns; `Enter` on header sorts; `↑/↓` row nav; row → Stock Detail | run result |
| | L | shaped shimmer rows + ghost builder rows; narration "Miru 正在跑篩選…" (analysis class §4.1) | pending run |
| | E | **query-empty** (§4.2): 「無符合條件的個股，放寬篩選?」 + "放寬 gates" chip → GateBar; **not** a chat CTA | empty result |
| | Err | inline calm retry; source-health degrade → honest `<Caveat>` | REST error |
| **SavedScreens** | save/load | name + `{conditions, match, gate_overrides}`; **dirty dot** (`--accent`) when builder diverges from loaded screen; name-validation (non-empty, ≤40, unique per market) → inline error; save → "已儲存" confirm | `/analytics/screener/screens` CRUD |
| sponsor factor | locked | 分點/官股/主動ETF row → 馬賽克 value layer for Free (frame/label sharp); entitled → live slider | entitlement |

- **Keyboard:** `Tab` builder → gates → **執行篩選** → table; SegmentedControls `role="tablist"`;
  factor picker is a listbox (type-ahead). reduced-motion → tab glide snaps, no shimmer sweep.
- **Non-trading:** header caveat pill 「符合條件的清單，非推薦名單」 pinned on results; run never
  emits 買/賣 language. US/ETF unsupported paths degrade to "尚未支援" on run (⚑ screener/README §2.1).

### 2.13 Settings and the Alert-rule builder

Settings is low-density config, but the **提醒與排程 / Alerts & Schedules** row is a real create/list/
edit/delete rule builder (moved into Settings — it's the config home, so its states must be specced).

| Element | State | Behaviour | Driver |
|---|---|---|---|
| **Settings form** | D/dirty | two-pane; edits mark the pane **dirty** → sticky 儲存/取消 bar; navigating away with unsaved → confirm | `PATCH /me`, `/personality`, etc. |
| | save | validation per field → inline error; success → "已儲存" toast (transient) | endpoint |
| | L | left list + right pane skeleton (plain-data class, no narration) | pending |
| **Alert-rule list** | E | **cold-start-ish but config**: "尚無提醒，新增第一則" + `+ 新增提醒` CTA (stays in Settings, no chat route) | `GET /alerts`, `/schedules` |
| | L | shimmer rule rows | pending |
| **Rule form** | D | 標的 symbol · 條件 (價格 ≷ / %變動 / 事件類型) · 通道 · 頻率; **threshold-direction validation** (≷ + value coherent, no empty) | user |
| | save | valid → create/update, optimistic row + "已建立" confirm; invalid → inline `--warn` | `POST/PATCH /alerts` |
| | delete | ✕ → **confirm** ("刪除此提醒?") → remove | `DELETE /alerts/{id}` |
| | Err | inline retry; never a full-page crash | REST error |

- **Keyboard/A11y:** rule form is a labelled fieldset; `Esc` cancels an open form; delete-confirm is a
  focus-trapped dialog (Esc/Enter). 44px targets. **馬賽克 never appears in Settings** (config, not gated
  analysis — [§2.7](#27-馬賽克-teaser-74-module-07-4)). reduced-motion → no form-entrance animation.

### 2.14 Treemap (熱力圖) · SupplyChainGraph · SectorStocksModal — keyboard for interactive viz

The two data-viz surfaces the brief flags for keyboard nav, plus the modal a treemap tile opens.

| Element | State | Behaviour | Driver |
|---|---|---|---|
| **Treemap tile** ([market/熱力圖.md](market/熱力圖.md)) | H | tile scale/shadow (ambient, no magnitude mapping) | pointer |
| | F | **arrow-key traversal** between tiles (spatial: ←/→/↑/↓ to nearest neighbour), focus-visible 2px `--accent` ring | keyboard |
| | A | `Enter`/click → opens `SectorStocksModal` for that 類股 | user |
| | value | tile fill = 紅漲綠跌 + **always a ▲▼/sign or % label** (never color-alone); tabular | `/market/heatmap` |
| **SupplyChainGraph** ([stock-detail/產業分析.md](stock-detail/產業分析.md)) | F | node focus ring; **arrow keys traverse edges** (上游→下游), `Enter` → that stock's detail | keyboard |
| | flow | dash-draw is ambient direction only (Tier 4 reduced-motion → static) | — |
| **SectorStocksModal** | D | dialog listing the sector's stocks (StockRow §2.4 rows) | tile/click |
| | L | shimmer rows | pending |
| | E | "此類股目前無個股" inline (query-empty §4.2, no chat CTA) | empty |
| | focus | **focus-trap**; open moves focus to first row/close; `Esc` closes → focus returns to originating tile; overlay click closes | user |

- reduced-motion → treemap tiles static (no breathe), graph edges static; keyboard paths unchanged.

### 2.15 AssetHeader (stock-detail sticky header)

The live sticky header on Stock Detail (代號名稱 · price · ▲▼% · 多空 band pill · ☆ · 問 Miru).

| State | Behaviour | Driver |
|---|---|---|
| D | sticky on scroll; price + ▲▼% (紅漲綠跌, tabular), 多空 `ScoreBandDot`, ☆ star toggle | `/quote` + `/analytics/{s}/score` |
| Live | price/% **tick-flash** `--up-weak`/`--down-weak` (inherits StockRow §2.4 tick behaviour); **band pill does NOT re-animate on tick** (gauge rule §2.3) | `quote` (module 16 §2a) |
| ☆ | idempotent watchlist toggle (optimistic, module-09 star) | `PUT /watchlists/…/items` |
| L | skeleton header bar | pending |

- Inherits StockRow live-tick + gauge band-pill (no-re-anim-on-tick) behaviour; `aria-label` carries
  direction+value. reduced-motion → tick color-flash kept (Tier 1), no transform.

### 2.16 股票搜尋 command palette (top-bar search — adapts Kestrel `StockSearch`)

The top-bar field **searches stocks, not questions** (asking Miru is the Chat tab). Opens as a
centered command palette over a blurred backdrop (only the palette stays sharp).

| State | Behaviour | Driver |
|---|---|---|
| open | click field or **`⌘/Ctrl+K`** / **`/`** → palette opens, input auto-focused, background scroll locked | user |
| D (empty query) | placeholder prompt "搜尋股票 代號/名稱"; optional recents | — |
| typing | debounced (~150ms) filter over the **stock universe** by 代號 / 名稱 / 產業; results **grouped TW → ETF → US** (badged), plus **主題 / 產業** match sections | `useStockUniverse` (client cache) / `/stocks` universe |
| nav | `↑/↓` move `selectedIndex` **in display order** (TW→ETF→US), `Enter` selects highlighted, `Esc` closes | keyboard |
| select | stock → **Stock Detail** (`/stock/{id}`); 主題 → pre-filtered screen; 產業 → refines the query | route |
| ☆ per row | inline **add/remove watchlist** (optimistic, idempotent — StockRow §2.4 / module-09) without leaving the palette | `PUT/DELETE /watchlists/…/items` |
| E | query with no match → "查無結果" inline (query-empty §4.2, no chat CTA) | empty universe filter |
| L | universe still loading → shimmer rows; never blocks typing | pending `/stocks` |

- **A11y:** `role="dialog"` + focus-trap; the input is a `combobox`, results a `listbox`, active row
  `aria-selected` + `aria-activedescendant`; `Esc` returns focus to the top-bar trigger. 44px rows.
- reduced-motion → palette fade/zoom-in becomes instant; no row motion. **Mobile:** this is the
  **Stock tab** landing surface (search + recent + browse), not a modal — see
  [../mobile/interaction-spec.md](../mobile/interaction-spec.md).

---

## 3. SSE-driven live layer

### 3.1 Event → motion map

| Source event | Motion / UI effect | Region |
|---|---|---|
| `turn.start` | thread shows pending turn shell | chat |
| `route.decided` | (detail track only) routing note | chat detail |
| `agent.spawned` | new lane column fades in | chat lanes |
| `agent.progress` | lane narration line updates in place (by `node_id`) | chat lane |
| `activity` | primary narration line, Sisi pulse | chat |
| `message.delta` | streaming-token fade+rise (`.stream-chunk`) appended by `node_id` | chat |
| `thinking.delta` | appended inside collapsed 顯示過程 (no motion, `aria-live=off`) | chat disclosure |
| `tool.proposed/started/result` | narration line + optional detail node | chat |
| `tool.locked` | inline 馬賽克 teaser | chat |
| `tool.blocked` | calm inline note (guardrail) | chat |
| `caveat` | pinned caveat badge on the answer | chat |
| `rich_card` | inline card enters (§2.6) | chat |
| `ask_user` | inline clarification prompt / option chips | chat |
| `follow_up` | pill chips below answer | chat |
| `steering.accepted` | pending steering chip | composer |
| `pet.reaction`/`.leveled`/`.pull_earned` | **cosmetic** Sisi cue / level-up / pull badge — **its region only, ignorable** | Sisi corner |
| `usage` | quiet accounting update (no motion) | — |
| `turn.end` | finalize answer, `aria-live=polite` announce, stop Sisi pulse | chat |
| `error{retryable}` | inline (per-lane if scoped) retry chip | chat |
| **`quote`** (module 16 §2a, ~5s) | live-tick flash on marquee / StockRow / AssetHeader / rich_card quote / chart series — **weak tint, one-shot, never magnitude-scaled; gauge unaffected** | data surfaces |

### 3.2 `Last-Event-ID` resume = visually seamless

- On reconnect the client sends `Last-Event-ID`; server replays events after that id from
  `stream_events` by `event_seq` (module 16 §2). Because deltas append **by `node_id`**, replayed
  `message.delta`s land in the same block — **no re-typing, no flicker, no duplicate lanes**.
- The client **suppresses entrance animation for replayed events** (they materialize instantly to the
  already-known state), then resumes live streaming animation for genuinely new events.
- `seq` is globally monotonic per turn → every parallel lane rebuilds deterministically from one
  replay (no per-lane cursor). A dropped connection is a non-event to the user.

### 3.3 `aria-live` discipline

| Region | politeness | Rationale |
|---|---|---|
| Final answer container | `polite` | announce the result once, at `turn.end` |
| activity / thinking / tool narration | `off` | streaming chatter must not spam SR |
| Live price ticks (marquee, rows, header) | `off` | high-frequency; value is in the DOM for on-demand reading |
| Re-rank / reorder churn | `off` for auto re-rank; **`polite` for user-initiated keyboard reorder** | announce only intentional user actions |
| Errors / caveats | `polite` (assertive only for a turn-terminal `error`) | surface honestly without hijacking |

---

## 4. Full-state discipline (reusable contracts)

Never ship happy-path only. Three shared components, used by every data surface.

### 4.1 `LoadingState` — skeleton always; narration only for generated analysis

Two loading classes — do **not** narrate a plain data refetch:

| Class | When | Treatment |
|---|---|---|
| **Analysis-generation** | chat turns, AI 摘要/SWOT, 多空 score compute, screener *run*, theme discovery — the agent is *producing* something | **narration line** ("Miru 正在看盤面…" / "正在跑篩選…") **+** shaped shimmer skeleton |
| **Plain data fetch** | a grid/chart/panel refetching EOD/quote data (財務/籌碼 tables, 熱力圖, 每日焦點 blocks, watchlist) | **shaped shimmer skeleton only** — no narration ("正在看盤面" on a routine table refetch is noise) |

- Skeleton (`.animate-shimmer`) block shapes always match the final layout (index-card ghosts, chart
  block, ghost rows). **No bare spinner anywhere**, either class.
- reduced-motion → static ghost blocks (no shimmer sweep).
- Per-view docs that say "loading = shimmer" are the **plain-data** class (correct as written); only
  the analysis surfaces above add the narration line.

### 4.2 `EmptyState` — two classes (do not conflate)

| Class | Meaning | Treatment |
|---|---|---|
| **Cold-start empty** | the *user* has nothing yet — empty watchlist/group, no chats, first run | calm copy + **primary action routes to Chat** ("問我任何台股問題" / prompt-starter chips); empty watchlist tab → server-seeded preset (not a blank); empty group → inline "點 ☆ 或問 Miru 加入" |
| **Query / coverage empty** | the *data* is absent for this query/range — "此區間無資料", "近期無名人事件", "暫無新聞", "近期無相關新聞", "此分類目前無個股", 研究圖表 coverage-gap, screener "無符合條件" | **calm inline message, keep the frame + `<Caveat>`, NO chat CTA**; screener empty offers a "放寬 gates" chip (its own affordance), not chat |

- The distinction is load-bearing: a builder must **not** add a "問 Miru" CTA to a "no data for this
  range" panel. Route-to-chat is *only* the cold-start class.

### 4.3 `ErrorState` — inline, retryable, honest

- **Inline + calm + retryable** (retry chip), never a full-page crash. Per-panel `<ErrorBoundary>`
  isolates one failing view.
- **Source-health degrade → honest `<Caveat>`**, not a hidden failure: `tool.result.source_health`
  or a stale snapshot surfaces as "資料來源延遲/部分不可用，以下為最後可得" — degrade, don't fabricate.
- Turn-terminal `error{retryable:true}` → retry chip; `retryable:false` → explain + suggest next step.

---

## 5. A11y interaction rules

- **Focus-visible ring:** 2px `--accent` on every interactive element, both themes; never removed
  (only `:focus-visible` scoped so mouse users don't see it).
- **Workbench keyboard nav:** **skip-links** (skip to main / to watchlist), **`/`** focuses the
  **股票搜尋 palette**, **`⌘/Ctrl+K`** opens it, **`Esc`** closes overlays / cancels drag / collapses
  disclosure. Screener table, chart controls, thread list all fully keyboard-operable.
- **Reduced-motion collapse tiers:** honour `prefers-reduced-motion` per §1.2 — Tiers 4–5 stop, Tier
  2 snaps, Tier 3 instant, Tier 1 keeps color-only feedback. **Every motion > Tier 3 must have a
  fallback** (mandatory).
- **44px minimum touch targets** for all tap/click affordances (grip, ✕ remove, ☆ star, tab pills,
  island tabs, CTAs); hover-reveal controls still meet 44px when revealed.
- **紅漲綠跌 never color-alone:** always **▲/▼ + sign** and an `aria-label` carrying direction +
  value ("上漲 1.45%"); the ~8% red-green-blind and SR users get direction without color.
- **Live-region etiquette** (see §3.3): reorder/re-rank announce **only** on user-initiated action;
  auto churn and ticks are `off`. Drag has a keyboard equivalent with `aria-live` step announcements.
- **`prefers-reduced-transparency`:** glass surfaces (nav island, top bar, teaser) fall back to
  solid `--surface` fill.

---

## 6. Cross-references

- Tokens + visuals (source of truth): [design-system.md](design-system.md) §2 (tokens), §5 (motion),
  §6 (nav), §7 (components), §7.11 (`ChartWithDataSheet`).
- IA / pages: [README.md](README.md), [market.md](market.md), [stock-detail.md](stock-detail.md),
  [screener/README.md](screener/README.md) (§2.12 contract), [chat/README.md](chat/README.md),
  [watchlist/README.md](watchlist/README.md), [settings/README.md](settings/README.md) (§2.13 contract).
- Drivers: SSE envelope [modules/07](../backend/modules/07-conversation-streaming.md), live-quote feed
  [modules/16 §2a](../backend/modules/16-infrastructure.md), voice/retry/edit/branch
  [modules/13](../backend/modules/13-chat-interaction-features.md), pet cues
  [modules/21](../backend/modules/21-pet-gamification.md), personality
  [modules/23](../backend/modules/23-agent-personality.md).
