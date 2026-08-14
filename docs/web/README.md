1	# 了然 · Miru — Web Client (Dashboard) Design Spec
2	
3	Design/blueprint for the **desktop-primary web app**. Grounded in the backend abilities
4	([../backend/](../backend/)) and the UX research foundation. This is a **spec** (IA, layout,
5	content, tokens), not code. The mobile client is [../mobile/](../mobile/).
6	
7	> **Design read:** an analytical **product dashboard** for TW retail investors + active traders,
8	> bilingual (zh-TW first), non-trading analysis companion. This is a *workbench + companion*, **not
9	> a marketing site** — so it uses a **product design system (shadcn/ui + Tailwind v4)**, not the
10	> landing-page aesthetic playbook. Dark-mode-first (traders expect it). Calm, dense-but-legible,
11	> "shows its work." Brand + positioning: [../brand.md](../brand.md).
12	>
13	> **The concrete visual language** (palette, CJK type, glass nav, 多空 gauge, cards, streaming chat,
14	> 馬賽克 teaser, `ChartWithDataSheet`, motion) lives in **[design-system.md](design-system.md)**;
15	> the **interaction / motion / state contracts** (animation catalog, SSE-driven live layer, full-state
16	> discipline, a11y) live in **[interaction-spec.md](interaction-spec.md)**; the **cache-coherence &
17	> data-freshness contract** (no cache layer may serve stale past its budget; freshness without jank)
18	> lives in **[data-freshness.md](data-freshness.md)**. All shared by web + mobile.
19	
20	> **Building from these docs? Read [DEVELOPMENT.md](DEVELOPMENT.md) first.** It is the runbook —
> setup, build order, and the **audited "what's-real ledger" (§4)**: which endpoints/tools/SSE events
> the backend actually exposes *today* vs which the page docs describe as intent. The page docs below
> flag their own gaps inline (⚑), but §4 is the consolidated, code-verified truth. When a page doc
> and the backend code disagree, the code wins — degrade the UI, don't wire a dead endpoint.

### Page docs (each page = an index `.md` + a folder of per-tab/per-view docs)
21	
22	| Page | Index | Per-tab/view folder |
23	|------|-------|---------------------|
24	| Stock Detail | [stock-detail.md](stock-detail.md) | [stock-detail/](stock-detail/README.md) — 基本資料/產業/財務/籌碼/技術/智能摘要/新聞/研究/即時 |
25	| Market 市場 | [market.md](market.md) | [market/](market/README.md) — 每日焦點/熱力圖/籌碼/產業/新聞/處置/名人 + 美股/ETF |
26	| Screener | [screener/README.md](screener/README.md) | (funnel workbench) |
27	| Chat | [chat/README.md](chat/README.md) | — |
28	| Watchlist rail | [watchlist/README.md](watchlist/README.md) | — |
29	| Settings | [settings/README.md](settings/README.md) | — (hosts 提醒/Alerts & Schedules) |
30	
31	---
32	
33	## 1. Stack & foundations
34	
35	| Concern | Choice | Why |
36	|---|---|---|
37	| Framework | **Next.js (App Router, RSC)** | streaming SSR pairs with the SSE agent turns; server components for data-heavy pages |
38	| Components | **shadcn/ui + Tailwind v4** (own-the-code) | product-UI, not marketing; customize tokens to the 藍鵲 brand — never ship default state |
39	| Data tables (screener) | **TanStack Table** | the funnel/factor tables are real data grids (skill §13: don't hand-roll) |
40	| Charts | lightweight candlestick/line lib (e.g. lightweight-charts / visx) | indicator + score visuals; must honor 紅漲綠跌 (§6) |
41	| Streaming | **SSE client** over the module-07 envelope | chat, live quotes, parallel-lane timeline |
42	| Icons | Phosphor (one family, `strokeWidth` locked) | never hand-roll SVG |
43	| Theme | **dark-first, light available**, CSS-variable tokens set once at root | §6 |
44	
45	State: server components for reads; a thin client store (Zustand) for the live SSE stream, active
46	thread, and theme. Continuous values (scroll, live price ticks) use motion values / refs, never
47	`useState` per tick.
48	
49	### Build-time review (deferred — when the UI is implemented, not now)
50	
51	These docs are the **design contract**. When the frontend is actually built from them, run the
52	**`impeccable` agents** against the *shipped UI* (they operate on built artifacts, not spec docs):
53	- **`impeccable-finish-reviewer`** — review the built screen vs this design set (design-system.md
54	  tokens + the page/tab specs + interaction-spec.md): the "does it hit the professional bar" gate,
55	  on real pixels. The non-negotiables (紅漲綠跌+▲▼, tabular-nums, dark-first, non-trading `<Caveat>`,
56	  sponsor→馬賽克, WCAG AA, reduced-motion) are the pass/fail checklist.
57	- **`impeccable-asset-producer`** — generate the 藍鵲/Sisi mascot art, gacha/pet assets, and any
58	  custom icons from approved mock references.
59	- **`impeccable-documenter`** — after a screen ships, record what actually shipped (a build-derived
60	  DESIGN.md) to reconcile against these specs.
61	
62	*(Not applicable at blueprint stage — nothing built to review/generate-from yet. The UI Designer /
63	UX Architect agents review the **design docs**; impeccable is the **build-time** quality gate.)*
64	
65	---
66	
67	## 2. Collapsible left-sidebar navigation (the core layout)
68	
69	Modern **elevated rail** (not a flat text list — see [design-system.md](design-system.md) §6.2):
70	**expanded** (icon + zh label + en sublabel) ↔ **collapsed** 64px icon rail (tooltip on hover),
71	state persists per user. Active item = **accent-tinted pill + 2px `--accent` left edge bar** with a
72	shared-layout indicator that glides on navigate. A **sticky glass top bar** (blur over scrolling
73	content) carries the **股票搜尋 command palette** (`搜尋股票 代號/名稱`, ⌘/Ctrl+K), notifications
74	bell, tier badge, account. The search box **searches stocks — not a question box** (asking Miru is the
75	Chat tab): type a 代號 or 名稱, results group **TW / ETF / US** (badged) + 主題/產業 matches, arrow-key
76	nav, `Enter` → Stock Detail, inline **☆ add-to-watchlist** per row (adapts Kestrel `StockSearch`). A
77	`follow_up`-style "問 Miru" affordance lives in Chat, not here.
78	
79	```
80	┌──────────────┬──────────────────────────────────────┬──────────────────────┐
81	│ ☰  了然·Miru  │ [🔍 搜尋股票 代號/名稱 ⌘K] 🔔 [Premium] ◍ 帳號│  ☆ ◀ (toggle rail) │
82	├──────────────┼──────────────────────────────────────┼──────────────────────┤
83	│ ▍◉ 對話 Chat  │                                      │ ● Watchlist      ✕  │  ← right rail
84	│   ▤ 市場 Market│        MAIN CANVAS (route)          │ [ TW | US | ETF ]    │    (toggleable,
85	│   ⌁ 個股 Stock│                                      │ ▾ 我的持股      12   │     persists across
86	│   ▽ 選股 Screen│                                     │  ⠿ ▮2330 台積電 ▲…  │     pages; drag to
87	│              │                                      │  ⠿ ▮0050 元大…  ▼…  │     reorder)
88	│              │                                      │ ▾ 觀察中         5   │
89	│              │                                      │  ⠿ ▮2317 鴻海  ▲…  │
90	│   ⚙ 設定       │                                      │ + 新增分組           │
91	│ ╭──────────╮  │                                      │ ─────────────────   │
92	│ │🐦 Sisi 🔥12│ │                                      │ 17 positions·拖曳排序 │
93	│ ╰──────────╯  │                                      │                      │
94	└──────────────┴──────────────────────────────────────┴──────────────────────┘
95	   left rail: primary nav              main canvas             right rail: watchlist
96	   (active = --accent-weak pill + left edge bar; collapses to 64px icon rail)
97	```
98	
99	### Primary nav items → backend
100	
101	| # | Item (zh / en) | Icon | Route | Maps to |
102	|---|---|---|---|---|
103	| 1 | **對話 / Chat** | chat | `/chat` | `/agent/query`, `/threads/*` SSE ([07](../backend/modules/07-conversation-streaming.md)) |
104	| 2 | **市場 / Market** | chart-bar | `/market` (**`/` redirects here** — the default landing) | market-wide dashboard (two-row tabs: 台股/美股/ETF × 每日焦點/熱力圖/籌碼/…) — [market.md](market.md); `/market/*`, `/derivatives/*`, `/macro` ([api-reference §4a-2](../backend/api-reference.md)) |
105	| 3 | **自選 / Watchlist** | star | **right-side rail toggle** (not a route) | `/memory` (type=watchlist, [09](../backend/modules/09-memory.md)) + `/quote`(`/quote/stream` Premium) — see [design-system §7.7](design-system.md#77-watchlist-rail-右側自選欄--drag-and-drop) |
106	| 4 | **個股 / Stock** | chart-line | `/stock/{symbol}` | `/analytics/{symbol}/*` ([20](../backend/modules/20-analytics-scoring.md)), `/quote`, `/calendar?symbol=` |
107	| 5 | **選股 / Screener** | funnel | `/screener` | funnel (gates→setup→rank) — [screener/README.md](screener/README.md); `/analytics/rankings` is **real** (top/bottom-N leaderboard, [20](../backend/modules/20-analytics-scoring.md) §5), `/analytics/screener/*` (the custom AND/OR funnel + saved screens) is **not yet built** — the only real screener today is the tag-flag `run_tag_screener`/`get_tag_catalog` agent tool pair, no REST projection yet ([api-reference.md](../backend/api-reference.md) §4a); **主題 Themes** sub-tab → the real theme-lookup REST (`/themes/{key}`, `/themes/{key}/tiers`, `/symbols/{symbol}/themes`, all `public`, [api-reference.md](../backend/api-reference.md) §4a-3) — `/analytics/themes` (LLM-discovered concept themes, Premium) is a separate, not-yet-built endpoint |
108	| — | ~~首頁 / Home~~ | — | **removed as a nav item** — there is no separate personal-dashboard destination. `/` **redirects to `/market`** (the default landing). The old Home blocks dissolve: 大盤概況/top-N live in **Market**; recent chats + prompt starters live in **Chat**; watchlist is the **right rail**; the pet streak is the sidebar Sisi footer. |
109	| — | ~~行事曆 / Calendar~~ | — | **removed as a top-level nav item** — per-stock events live in Stock Detail › 研究圖表 ([stock-detail/研究圖表.md](stock-detail/研究圖表.md)); market-wide events in Market › 每日焦點 本週行事曆 block. **No `/calendar` REST route exists yet** — calendar data is real only as the agent tool `get_calendar_events` (`tools/calendar_tools.py`, `sponsor` data_class, [18](../backend/modules/18-calendar-events.md)); a page consuming it today goes through the agent/tool path, not a dedicated REST projection — surfaced in-context, not a standalone destination. |
110	| — | ~~提醒 / Alerts~~ | — | **moved into Settings** ([settings/README.md](settings/README.md)) — alerts/schedules are a low-frequency Pro *config* surface, not a daily destination. The **notifications inbox** stays reachable via the 🔔 bell in the top bar on every page. Backed by `/alerts`, `/schedules`, `/notifications` ([14](../backend/modules/14-scheduler-alerts.md)). |
111	| — | **設定 / Settings** | gear (bottom) | `/settings/*` | hub (incl. 提醒/alerts) — see §3.3 |
112	
113	**IA rulings (from UX research, deliberate):**
114	- **Pets are NOT a primary nav item** — cosmetic, decoupled ([21](../backend/modules/21-pet-gamification.md) governance). Home for pets = ambient in chat + a Settings collection page. Sidebar shows only a small dismissible **Sisi + streak** footer.
115	- **Personality is a Settings picker** framed **「了然的語氣」**, never "choose your advisor" ([23](../backend/modules/23-agent-personality.md), protects non-trading promise).
116	- **Themes** is a Screener **sub-tab**, not top-level (a discovery lens on the same universe; Premium).
117	- **Alerts + Schedules live in Settings** (not a top-level nav item) — a low-frequency Pro config surface; the 🔔 notifications inbox stays in the top bar. (Backend keeps `/alerts` + `/schedules` endpoints separate.)
118	
119	### 2.1 Chat sub-nav (under 對話)
120	Thread list with sort/star/pin/archive/search/tags ([13](../backend/modules/13-chat-interaction-features.md) §2); "新對話" button; per-thread model + **personality pin** ([13](../backend/modules/13-chat-interaction-features.md) §3, [23](../backend/modules/23-agent-personality.md)).
121	
122	### 2.2 Stock Detail — in-page tabs (not sidebar)
123	`/stock/{symbol}` route. Full spec: [stock-detail.md](stock-detail.md). Tabs (TW stock):
124	`基本資料 · 產業分析 · 財務分析 · 籌碼分析 · 技術分析 · 智能摘要(含多空評分 score gauge + SWOT) ·
125	相關新聞(即時/財經) · 研究圖表 · 即時🔒`. **多空評分 lives inside 智能摘要** (gauge = quantitative
126	half, SWOT = narrative half). A docked **「問 Miru 關於此股」** launcher opens Chat pre-seeded with the symbol.
127	
128	### 2.3 Settings hub (nested, gear — not daily destinations)
129	| Sub-page | Backend |
130	|---|---|
131	| 帳號 / Profile | `PATCH /me` ([12](../backend/modules/12-multi-user-tiers.md)) |
132	| 連結帳號 / Linked providers | `/me/connections` |
133	| **了然的語氣 / Agent style** (穩健/白話/簡潔/數據派) | `/personality/presets`, `PATCH /me {personality_id}` ([23](../backend/modules/23-agent-personality.md)) |
134	| **夥伴 / Pets** (collection, gacha, equip, streak) | `/pets/*` ([21](../backend/modules/21-pet-gamification.md)) |
135	| 方案與帳單 / Billing (BYOK checkbox) | `/billing/*` ([15](../backend/modules/15-payments-paddle.md)) |
136	| API 金鑰 / BYOK keys | `/me/keys` |
137	| 用量 / Usage & quota | `/usage/quota` (real — resolved limits, not live consumption); `/usage/summary` not built yet ([api-reference.md](../backend/api-reference.md) §6) |
138	| 通知偏好 / Alert preferences | `/me/alert-preferences` ([14](../backend/modules/14-scheduler-alerts.md)) |
139	| 記憶 / Memory | `/memory` list + delete ([09](../backend/modules/09-memory.md)) |
140	
141	---
142	
143	## 3. Key screens (layout intent)
144	
145	### 3.1 Chat (`/chat`) — the companion, co-primary
146	Center column = the conversation over SSE; the **NL `activity` narration is the primary line**
147	("正在查 台積電 近 20 日籌碼…"), raw `thinking.delta`/tool internals collapsed behind a "顯示過程"
148	disclosure ([07](../backend/modules/07-conversation-streaming.md) §narration). `rich_card` renders
149	stock cards / score gauges inline; `caveat` badge on every analytical answer; `follow_up` chips
150	below. **`tool.locked` → inline 馬賽克 teaser + upgrade CTA** (§5). Composer: text + voice mic
151	(Premium, confirm-before-send [13](../backend/modules/13-chat-interaction-features.md)). Steering:
152	sending mid-run queues a steering message (no cancel). **Parallel sub-agent fan-out renders as
153	side-by-side lanes** on the wide canvas ([07](../backend/modules/07-conversation-streaming.md) §run-tree).
154	
155	### 3.2 Stock Detail (`/stock/{symbol}`) — the multi-panel workbench
156	The **desktop-primary moment**: on ≥1280px, chart + score breakdown + chip flows visible together;
157	Chat can dock as a right rail (ask about the symbol you're reading). Score = **偏多/中性/偏空 band +
158	numeric + always-visible factor breakdown** (never the number alone — [20](../backend/modules/20-analytics-scoring.md) §7).
159	
160	### 3.3 Screener (`/screener`) — the funnel, desktop-primary
161	Three-stage funnel UI matching [20](../backend/modules/20-analytics-scoring.md) §5: pick a **named
162	preset** (Minervini / CANSLIM / 月營收創新高…) from the bilingual catalog → **gates** applied → **ranked
163	survivors** in a TanStack table with factor columns, gate-pass chips, sparklines. Header caveat:
164	**「符合條件的清單，非推薦名單」** + regime/over-extension note. Row click → Stock Detail.
165	
166	---
167	
168	## 4. Default landing = Market (`/` → `/market`); where the old Home blocks live
169	
170	There is **no separate personal-dashboard destination** on web. `/` **redirects to `/market`**
171	([market.md](market.md)) — the market-wide dashboard is the launch surface. The persistent
172	**大盤概況 orientation** the old Home led with *is* Market › 每日焦點. The former Home blocks dissolve
173	into their natural homes rather than duplicating a landing page:
174	
175	| Old Home block | Now lives in |
176	|---|---|
177	| **大盤概況 / Market Overview** — TAIEX + 櫃買, breadth, 多空氛圍 regime | **Market › 每日焦點** ([market/每日焦點.md](market/每日焦點.md)) — the landing |
178	| **AI 精選 / Top-N** — profile-aware ranked list + factor chips | **Market › 每日焦點** Top-N block + **Screener** rank ([screener/README.md](screener/README.md)) |
179	| **今日事件 / This Week's Events** — 除權息/配息/法說/結算 | **Market › 每日焦點 本週行事曆** block + Stock Detail › 研究圖表 |
180	| **我的自選 / Watchlist** — price/%chg/spark, live-ish | the **right-side watchlist rail** (design-system [§7.7](design-system.md#77-watchlist-rail-右側自選欄--drag-and-drop)) — on every page |
181	| **最近對話 / Recent Chats** · **快速提問 / Prompt starters** | **Chat** ([chat/README.md](chat/README.md)) — thread list + prompt-starter chips |
182	| **夥伴 / Streak nudge** — Sisi + streak | the sidebar **Sisi + 🔥streak footer** (design-system §6.2) — never a landing block |
183	
184	**Persistent under the top bar on Market: the 股票跑馬燈 (stock marquee)** — a thin scrolling ticker of
185	watchlist + indices ([design-system.md](design-system.md) §7.5). Live-ish for Premium
186	(`/quote/stream`), EOD for Free; 紅漲綠跌 + ▲▼; pauses on hover; reduced-motion → static scrollable
187	strip; tap → Stock Detail. The one permitted marquee on the page.
188	
189	First-run (post-register): **gacha welcome modal** ([21](../backend/modules/21-pet-gamification.md) §6)
190	precedes the first Market view. The **watchlist rail is never cold-empty** — every new user is
191	**seeded with the preset watchlists** (半導體/權值股/US Tech/US Index/台股 ETF/債券海外 ETF — the
192	Kestrel `DEFAULT_WATCHLISTS`, [design-system §7.7](design-system.md#77-watchlist-rail-右側自選欄--drag-and-drop)),
193	so there is always live content to glance at; the route-to-Chat *cold-start* empty
194	([interaction-spec §4.2](interaction-spec.md)) applies only if a user deletes every group.
195	
196	---
197	
198	## 5. The masked-teaser upgrade moment (馬賽克)
199	
200	The core monetization surface ([12](../backend/modules/12-multi-user-tiers.md) §3,
201	[07](../backend/modules/07-conversation-streaming.md) §4). Design it as **invitation, not wall**:
202	- **Blur the detail, reveal the frame** — show the 市場定位 headline unblurred, blur the SWOT
203	  specifics; show the top-N row *count*, blur the sponsor 分點 column.
204	- **One calm CTA** ("升級 Premium 看完整籌碼分析"), inline in the flow — the answer proceeds around it,
205	  never a nag/interstitial.
206	- **Consistent mosaic treatment** across chat (`tool.locked`), Stock Detail (AI summary teaser), and
207	  top-N (masked columns) so the pattern is learnable. Upgrade → `/billing/plans` (BYOK checkbox → cheaper price).
208	
209	---
210	
211	## 6. Usability, a11y & TW conventions (non-negotiable)
212	
213	- **紅漲綠跌 — red = up, green = down** (inverted vs Western). A **theme token**; get it wrong and
214	  the product is unusable for TW users. **Never color-alone** — pair with ▲▼ + sign (a11y).
215	- **Tabular numerals** everywhere prices/%/scores appear (flicker-free on live ticks). Precision
216	  tokens: price 2dp, % 2dp, 億/萬 for volume/市值.
217	- **CJK typography:** quality **Traditional** TC face (Noto Sans TC / source-han), correct TC glyph
218	  variants (never Simplified fallback), body ≥15–16px, line-height ≥1.6, don't break 台積電 2330 mid-token.
219	- **Dark mode first-class** (likely default); tune red/green for dark-bg contrast (pure green on black fails).
220	- **Score gauge** = labeled band + numeric + factor breakdown (show the work, [20](../backend/modules/20-analytics-scoring.md) §7).
221	- **SSE a11y:** `aria-live=polite` on the answer, not on every thinking token; "顯示過程" is a proper
222	  disclosure; collapsed thinking reachable but not read by default. Resumability (`Last-Event-ID`)
223	  makes a dropped connection a non-event.
224	- **Reduced-motion:** honor `prefers-reduced-motion` for gacha/pet/gauge transitions (cosmetic → static).
225	- **WCAG AA** contrast both themes; full keyboard nav for the workbench (screener table, chart controls, thread list).
226	- **Loading = shaped skeleton, never a blank spinner.** *Generated-analysis* surfaces (chat, AI
227	  摘要/SWOT, score compute, screener run) add a **narration line** ("Miru 正在看盤面…"); a *plain data
228	  refetch* (grids/charts) shows the skeleton only — see [interaction-spec §4.1](interaction-spec.md).
229	  The pet spinner line is separate from the analysis region ([21](../backend/modules/21-pet-gamification.md) §5).
230	
231	## 7. Responsive breakpoints (shared with mobile)
232	
233	- **≥1280px:** expanded sidebar; Stock Detail multi-panel (2–3 col); Screener full table; parallel
234	  agent lanes side-by-side; chat can dock as a right rail.
235	- **768–1279px:** sidebar → **icon rail**; Detail → 2→1 col; Screener table horizontally scrollable
236	  with pinned first column; agent lanes stack, expandable.
237	- **<768px:** hands off to the **mobile client** ([../mobile/](../mobile/)) — bottom-tab nav, single
238	  column, section-stack detail, screener → card list, lanes collapsed to the narration track.
239	
240	## 8. Cross-cutting governance in the UI
241	
242	Every analytical surface (score, SWOT, screener list, top-N, chat answer, alert copy) carries the
243	**currentness + non-trading caveat** and the "AI-generated summary of data, not advice" footer
244	([../backend/governance.md](../backend/governance.md) §1; [20](../backend/modules/20-analytics-scoring.md) §7).
245	Copy never uses 明牌/飆股/目標價/買賣/進場/停損 ([../brand.md](../brand.md) §5). **The caveat badge +
246	non-trading footer are required, non-removable design-system components** on any analysis card.
247	Personality/pet layers style tone only — never introduce trading language or drop a caveat.
248	
249	---
250	
251	## 9. Visual design (applied) — what the shell actually looks like
252	
253	Concrete look for the web shell; token/component source of truth is
254	[design-system.md](design-system.md) (`VISUAL_DENSITY:6`, radius `card 16px · input 10px · pill 999px
255	· button 12px`, 4px spacing base, section gap 24–32px, card padding 20px).
256	
257	**App frame (≥1280px).** Three columns on `--bg`: left rail **240px** (`--surface`, hairline
258	`--border` divider) ↔ collapsed **64px**; main canvas fluid **12-col grid** (24px gutters, max ~1200px
259	content); right watchlist rail **300–320px** (toggleable). Sticky **glass top bar** (56px,
260	`backdrop-blur(20px) saturate(160%)`, `--surface`@70% + `1px --border`, solid fallback under
261	`prefers-reduced-transparency`). Marquee strip (§7.5) 32–36px `--surface` sits directly under it.
262	
263	**Left rail.** Items 40px tall, icon (Phosphor `regular`) + zh label (15px) + en sublabel (12px
264	`--text-muted`). Active = `--accent-weak` pill + **2px `--accent` left edge bar** + accent icon;
265	shared-layout indicator glides (`layoutId`). Groups separated by negative space + one `--border`
266	hairline, never nested boxes. Sisi + 🔥streak chip and ⚙ gear pinned bottom.
267	
268	**Card anatomy (every canvas block).** bg `--surface`, `1px --border`, radius 16px, padding 20px;
269	elevation via **tinted shadow** (tint toward `--bg` hue, low alpha — never neon/pure-black), hover
270	lifts to `--surface-2` (adapts Kestrel `.card-atmospheric`, no gradient blob in product chrome).
271	Header row: section title 18–20px/600 + optional `ⓘ` + right-aligned controls. Multi-row data
272	groups by `divide-y --border`, not individual boxes (density without clutter).
273	
274	**Type & numbers.** Page title (display) 28–32px/600 `tracking-tight`; body CJK 16px/1.7; all
275	prices/%/scores in `--font-num` with **`tabular-nums`** (flicker-free ticks, columns align); caveat
276	micro 13px `--text-muted`. Never break `台積電 2330` mid-token.
277	
278	**Non-negotiables visible in every view (design-system §4):** 紅漲綠跌 — `--up`(漲 red)/`--down`(跌
279	green), **never color-alone → always ▲▼ + sign**; `--accent` teal is chrome-only (links/active/focus),
280	never a price; categorical chart series use `--chart-1..5` (never signed); **required `<Caveat>`
281	footer** (non-trading on analytical, currentness on quote) on every analytical surface; sponsor data →
282	**馬賽克** (`backdrop-blur` + diagonal-line texture + centered lock chip, frame sharp/values blurred);
283	`prefers-reduced-motion` honored; WCAG AA both themes; **dark-first** (design both, dark primary).
284	
285	**Global states.** Loading = **shaped shimmer skeletons** (`.animate-shimmer`, block shapes matching
286	final layout), never a bare spinner; *generated-analysis* surfaces add a narration line ("Miru 正在看
287	盤面…"), plain data refetches don't. Empty has two classes: **cold-start** (no user data) → route to
288	Chat; **query/coverage** (no data for this range) → calm inline, keep frame + caveat, no chat CTA. Both
289	detailed in [interaction-spec §4](interaction-spec.md). Error → inline calm retry chip. Live tick → bg
290	flashes `--up-weak`/`--down-weak` then fades.
291	