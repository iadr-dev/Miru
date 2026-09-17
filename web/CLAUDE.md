1	# web/ — Coding Standards
2	
3	Design-blueprint → real **Next.js (App Router, RSC)** frontend. The **spec lives in `../docs/web/`** —
4	read `README.md` (§1 stack, §6 non-negotiables, §7 breakpoints, §9 visual), `design-system.md`
5	(tokens, 紅漲綠跌, motion), `interaction-spec.md` (motion + state + a11y contracts), and
6	`data-freshness.md` (cache-coherence contract) **before touching any component**. **Governance rules
7	are non-negotiable and load-bearing — never relax them to make a screen ship.** See the project-root
8	[`../AGENTS.md`](../AGENTS.md) for shared guidance.
9	
10	> Resolved defaults for the frontend's likely-ambiguous choices (override if the team disagrees):
11	> **pnpm** (lockfile committed) · **Biome** (lint+format, one tool) · **Vitest** (unit/component) +
12	> **chrome-devtools MCP** (browser/e2e automation — not Playwright) · `motion/react` (Framer Motion
13	> v11+ import path) · **TanStack Query v5** · **Zustand v5** ·
14	> **lightweight-charts** for candles, **visx/recharts** for the rest · TypeScript `strict` +
15	> `noUncheckedIndexedAccess`.
16	
17	## 1. Toolchain (2026 baseline)
18	
19	- **Node LTS** (22.x); pin in `.nvmrc` + `engines`. **pnpm** is the sole package manager — commit
20	  `pnpm-lock.yaml`. **No npm/yarn** (mixed lockfiles are a review reject).
21	- **Next.js App Router (RSC-first)**, TypeScript **strict**.
22	- **Biome** — lint **and** format (replaces ESLint+Prettier: one config, fast, no plugin drift).
23	  If the team prefers ESLint flat + Prettier, that's the fallback — but pick **one**, never both
24	  formatting.
25	- **Vitest** + Testing Library (unit/component). **Browser/e2e automation via the chrome-devtools
26	  MCP** (navigate, snapshot, click/fill, console + network inspection, Lighthouse, perf traces) — used
27	  for keyboard, reduced-motion, and a11y verification against a running dev server, **not Playwright**.
28	  Deterministic: stub the SSE stream and `/quote` feed (§7).
29	- **`next/font`** for Noto Sans TC + the numeric mono — **never** a `<link>` to Google Fonts (layout
30	  shift, no self-host, privacy).
31	- **Tailwind v4** via `@tailwindcss/postcss` (CSS-first `@theme`, not the legacy `tailwind.config.js`
32	  JS plugin path); shadcn/ui components vendored into the repo (own-the-code).
33	- **Animation = `motion/react`** (Framer Motion v11+) as the **default/primary** — the entire motion
34	  contract ([../docs/web/interaction-spec.md](../docs/web/interaction-spec.md) §1: `spring.default`,
35	  duration tokens, reduced-motion tiers) is written against it, and its React-native model + spring
36	  physics fit our calm product-dashboard motion. **One animation library** — do not ship a second in
37	  the same bundle without a reason.
38	  > **⚑ Open toolchain decision — GSAP vs motion/react.** GSAP (installed as skills) is stronger for
39	  > **scroll-driven** work (ScrollTrigger pin/scrub/parallax) and Flip/SplitText, which motion/react
40	  > lacks. If a heavily scroll-choreographed surface appears, GSAP may be added **scoped to that
41	  > surface** (option: motion/react primary + GSAP for scroll sections) — but then its motion must
42	  > still honor the same tokens + non-negotiables (non-trading motion, reduced-motion fallback,
43	  > tabular-nums). **GSAP is DOM/SVG-only — it does NOT run on React Native**, so it is a **web-only**
44	  > choice; the mobile client ([../docs/mobile/](../docs/mobile/)) uses `reanimated` regardless
45	  > ([../docs/mobile/interaction-spec.md](../docs/mobile/interaction-spec.md) §1.1). Default stays
46	  > motion/react until a concrete scroll-heavy need justifies the second lib.
47	
48	### Commands
49	```bash
50	pnpm install                    # from committed lockfile (CI: --frozen-lockfile)
51	pnpm dev                        # next dev
52	pnpm lint                       # biome check (lint + format verify)
53	pnpm typecheck                  # tsc --noEmit
54	pnpm test                       # vitest run  (pnpm test:e2e → playwright)
55	pnpm build                      # next build (must pass typecheck + lint clean)
56	```
57	
58	### Config stanzas
59	```jsonc
60	// tsconfig.json — compilerOptions
61	{
62	  "strict": true,
63	  "noUncheckedIndexedAccess": true,   // array/record access is T | undefined
64	  "noImplicitOverride": true,
65	  "verbatimModuleSyntax": true,
66	  "moduleResolution": "bundler",
67	  "plugins": [{ "name": "next" }]
68	}
69	```
70	```jsonc
71	// biome.json — key rules (load-bearing)
72	{
73	  "linter": { "rules": {
74	    "suspicious": { "noExplicitAny": "error" },
75	    "a11y": "recommended",                       // labels, roles, alt, tabindex
76	    "nursery": { "useSortedClasses": "warn" }    // Tailwind class order
77	  }},
78	  // RSC-safety: forbid client-only imports (useState/useEffect/motion/zustand) in files
79	  // without a top-of-file "use client" — enforced by the react-server-components lint rule
80	  // (or eslint-plugin-react-server-components in the ESLint fallback). A server component
81	  // importing a hook is a build-time reject.
82	}
83	```
84	
85	## 2. Type & style
86	
87	- TypeScript **strict**, `noUncheckedIndexedAccess`. **No `any`** — isolate unavoidable 3rd-party
88	  edges (chart libs, some SDK/browser types) in a typed adapter with `// biome-ignore … — <reason>`;
89	  `any` never crosses a module seam.
90	- **Zod at every boundary** — validate API responses, the SSE `tool`-envelope, and URL params where
91	  they enter the app (the frontend mirror of the backend's pydantic role). Parse-don't-trust: a
92	  malformed payload fails loud at the seam, never silently renders.
93	- **Shared domain contracts live in `web/types/` as Zod schemas; the TS type is `z.infer`'d — never
94	  hand-written alongside** (one source, so validator and type can't drift). Each shared contract
95	  **mirrors the backend** ([../docs/backend/api-reference.md](../docs/backend/api-reference.md) + the
96	  cited module): the SSE module-07 envelope, `quote`/`score`/screener shapes, the `(capability,
97	  market, symbol)` key, `FRESHNESS` classes, tier/entitlement. **Hand-written now, migrated to
98	  generated-from-OpenAPI** once the backend serves `/openapi.json` — a backend contract change must
99	  then become a **frontend compile error**, not a silent mismatch. Each file cites the backend module
100	  it mirrors so reconciliation is mechanical. **Feature-only types stay co-located**
101	  (`app/<feature>/types.ts`) — don't dump them in `types/`. Frontend-invented shared shapes (UI/theme
102	  state) also live in `web/types/` but need no backend citation.
103	- **Phosphor icons only** — one family, `weight="regular"`, `strokeWidth` locked globally
104	  ([design-system §5](../docs/web/design-system.md)). Never hand-roll SVG icons; **no emoji** in code
105	  or product chrome.
106	- **CSS-variable design tokens only — never a hardcoded hex.** Every color reads from the tokens set
107	  once at `:root` ([design-system §2](../docs/web/design-system.md)): `--bg/--surface/--surface-2/`
108	  `--border/--text/--text-muted/--accent/--up/--down/--warn/--chart-1..6/--grid/--crosshair` + sector
109	  tokens. A literal `#…` or an ad-hoc `rgb()` in a component is a review reject.
110	- **Single token source, exported — web + native must not drift.** The `:root` CSS-var set is the
111	  **one source of truth**; the shared mobile client ([../docs/mobile/](../docs/mobile/)) consumes the
112	  *same* palette. Define tokens once (a `tokens.css` / `@theme` block) and **generate** the RN theme
113	  object (StyleSheet/reanimated constants) from it via a build step — never hand-maintain a second
114	  copy. A token added/changed in one place must propagate to both; a divergent native hex is a reject.
115	- **shadcn/ui own-the-code** — customize the vendored component to the 藍鵲 tokens; **never ship the
116	  default shadcn skin** (README §1). One design system: no mixing MUI/Chakra/Ant in.
117	- **Naming:** components `PascalCase.tsx`, hooks `useXxx.ts`, one component per file matching the
118	  export; route segments/folders lowercase; `'use client'` leaves suffixed by intent
119	  (`*.client.tsx` optional but consistent). `--font-num` + `tabular-nums` on **every** price/%/score.
120	
121	### 2.1 TypeScript language rules (avoid → prefer)
122	
123	Grounded in the official TS [Do's and Don'ts](https://www.typescriptlang.org/docs/handbook/declaration-files/do-s-and-don-ts.html)
124	+ the `typescript-eslint` **recommended-type-checked** set (Biome maps the equivalents). All are
125	**errors**, not warnings.
126	
127	**Types & values — avoid → prefer:**
128	- **`any` → `unknown`** (then narrow). `any` disables checking and silently infects callers; `unknown`
129	  forces a guard. The only `any` is a `// biome-ignore … — <reason>` at a 3rd-party edge (§2), never
130	  across a seam. Banned by extension: `no-unsafe-{assignment,call,member-access,return,argument}`.
131	- **Boxed wrappers `Number`/`String`/`Boolean`/`Symbol`/`Object` → primitives** `number`/`string`/
132	  `boolean`/`symbol`; use `object` (or a precise shape) not `Object`, and avoid the empty `{}` type
133	  (it means "any non-null value"). Prefer `Record<K,V>` / an explicit interface.
134	- **`enum` → union of string literals** (`type Band = '偏多'|'中性'|'偏空'`) or `as const` object.
135	  TS `enum` emits runtime code, has surprising numeric semantics, and isn't a plain type; unions are
136	  erasable and exhaustively checkable. (If an enum is ever unavoidable, `const enum` is banned —
137	  breaks under `isolatedModules`/bundlers.)
138	- **`namespace` / `module X {}` / triple-slash `///` → ES modules** (`import`/`export`). No
139	  `require()` in app code.
140	- **Non-null `!` → optional chaining `?.` + nullish coalescing `??`** (`a.b?.c ?? fallback`). `!`
141	  asserts a lie the compiler can't check. Likewise **`||` for defaults → `??`** (so `0`/`''`/`false`
142	  aren't swallowed).
143	- **`as`-casts → type guards / `satisfies`.** Avoid `as` (esp. `as any`, double-assert `as unknown
144	  as T`); validate with Zod at boundaries (§2) and use **`satisfies`** to check a literal against a
145	  type without widening it. `as const` for literal tuples/config is fine and encouraged.
146	
147	**Functions, async, control flow:**
148	- **Explicit return types on all exported functions/hooks** — don't rely on inference across a module
149	  boundary (catches accidental contract drift; mirrors the backend's "type all public surfaces").
150	- **No floating promises** (`no-floating-promises`): every Promise is `await`ed, `void`-marked, or
151	  `.catch()`-handled — a dropped rejection is an invisible bug in an SSE/quote path. **No async
152	  function passed where a `void`/sync callback is expected** (`no-misused-promises`) — e.g. an
153	  `onClick={async …}` that isn't awaited.
154	- **No `throw` of non-Errors** (`only-throw-error`); reject with `Error` instances.
155	- **Discriminated unions + exhaustive `switch`** for the SSE envelope event types, score bands, market
156	  tabs — a `default: assertNever(x)` arm makes a new variant a **compile error**, not a silent miss.
157	- **Prefer `interface` for object shapes that may be extended/implemented; `type` for unions,
158	  tuples, mapped/conditional types.** Don't mix arbitrarily.
159	
160	**Imports & modules:**
161	- **`import type { … }`** for type-only imports (`verbatimModuleSyntax` is on) — keeps types out of the
162	  runtime bundle and keeps the RSC client/server boundary clean.
163	- **No default exports for components/utilities** (named exports only) — better refactors, grep-ability,
164	  and auto-import; Next.js `page.tsx`/`layout.tsx`/`route.ts` default exports are the sanctioned
165	  exception the framework requires.
166	- **No `// @ts-ignore`** — use `// @ts-expect-error — <reason>` (fails when the error is fixed, so it
167	  self-cleans); `ban-ts-comment` is on.
168	
169	**SonarCloud gate (CI, in addition to Biome/tsc):**
- **No global-regex `.replace()` for a literal substring `→` `String#replaceAll()`** (`S7781`) —
  `.replace(/_/g, " ")` → `.replaceAll("_", " ")`.
- **No nested ternary `→` extract to a named function or if/else-if chain** (`S3358`) — a
  ternary whose branch is itself a ternary is a review reject; pull the inner branch into a
  small named function (e.g. `upgradeDowngradeColor(action)`) or use `let x; if (...) ... else if
  (...) ...` instead.
- **Duplication-density gate** (`new_duplicated_lines_density`, must stay ≤3%) — a byte-identical
  block of markup/logic shared across two components (e.g. a calendar month-grid) trips this even
  with zero open Sonar issues. Extract the shared visual/logic contract into one component in
  `components/ui/` (data plumbing stays local to each caller) rather than duplicating it — see
  `components/ui/month-grid-calendar.tsx` for the pattern.

**Consequences of the strict flags (§1):** `noUncheckedIndexedAccess` means `arr[i]` / `record[k]` is
170	`T | undefined` — **handle the `undefined`**, don't `!` it away. `exactOptionalPropertyTypes` (enable
171	if not on): don't pass `undefined` to an optional prop; omit it.
172	
173	## 3. Architecture invariants → code rules
174	
175	- **RSC boundary rule.** Server Components render the static, data-heavy layout (page shells, EOD
176	  panels, tables, analytics reads). Anything using **motion / scroll / pointer / SSE / a browser
177	  API** is an **isolated `'use client'` leaf** — push the `'use client'` boundary as deep as
178	  possible; never mark a whole page/layout client to unlock one interactive child
179	  ([data-freshness §5](../docs/web/data-freshness.md), design-taste-frontend §3.A).
180	- **Data-layer ownership — one value, one owner** ([data-freshness §5–§6](../docs/web/data-freshness.md)):
181	  - **RSC server-fetch** for cold reads (page data, EOD panels, financials/chip tables, daily/weekly
182	    analytics) — `next: { revalidate: <class boundary> }` + `revalidateTag(...)`, never
183	    `cache:'no-store'` on a once-a-day value.
184	  - **TanStack Query** for client-interactive reads (screener re-probe, chart TF refetch, watchlist
185	    quotes) — `staleTime`/`gcTime` from the shared `FRESHNESS[class]` map.
186	  - **Zustand thin store ONLY** for the live SSE layer, active thread, and theme. Live `quote` ticks
187	    write here (or to motion values), **never** into the TanStack cache.
188	- **Query keys mirror the backend key** `(capability, market, symbol[, date])` — e.g.
189	  `['score','TW','2330']`. A symbol crossing screener ↔ watchlist ↔ detail ↔ chat `rich_card`
190	  resolves to **one** coherent entry ([data-freshness §3](../docs/web/data-freshness.md)).
191	- **SSE client consumes the module-07 typed envelope.** Deltas append **by `node_id`**; resume via
192	  **`Last-Event-ID`** replayed by `event_seq` — replayed events materialize instantly (entrance
193	  animation suppressed). Never re-type, never duplicate a lane ([interaction-spec §3](../docs/web/interaction-spec.md)).
194	- **Continuous values use motion values / refs — never `useState` per tick.** Live ticks are
195	  **rAF-batched**; a watchlist of N rows ticking every ~5s must not re-render the tree N times
196	  ([data-freshness §6](../docs/web/data-freshness.md)).
197	- **Grid over flex-math** for the app frame + multi-panel workbench; **`min-h-[100dvh]`**, never
198	  `h-screen` (mobile viewport). One design system — no second component lib.
199	
200	## 4. Governance-as-code (non-negotiable)
201	
202	- **紅漲綠跌 is a token, never color-alone.** `--up`(漲 red)/`--down`(跌 green) apply **only** to
203	  price/%/score/candles, and **always** pair with **▲/▼ + sign + an `aria-label`** carrying direction
204	  + value ("上漲 1.45%"). Get it wrong and the app is unusable for TW users
205	  ([design-system §4](../docs/web/design-system.md), README §6). **`--accent` teal is chrome-only —
206	  never a price.** Categorical chart series use `--chart-1..6` (never signed).
207	- **`<Caveat>` is a required, non-removable component** on **every** analytical/quote surface (score,
208	  SWOT, screener list, top-N, chat answer, `ChartWithDataSheet`, marquee, AssetHeader): non-trading
209	  caveat on analytical, currentness caveat on quote/market ([README §8](../docs/web/README.md),
210	  [data-freshness §8](../docs/web/data-freshness.md)). No `prop` may hide it. Pair with
211	  `DataFreshnessBadge` on cached-and-possibly-stale surfaces.
212	- **馬賽克 teaser for sponsor data** — frame/headline/label columns **sharp**, value layer under
213	  `backdrop-blur` + diagonal texture + a centered lock chip + **one calm CTA**. Never a
214	  wall/interstitial; the answer proceeds around it. Consistent across chat `tool.locked`, detail SWOT,
215	  top-N/screener columns ([README §5](../docs/web/README.md), [design-system §7.4](../docs/web/design-system.md)).
216	  A `tool.locked` teaser envelope `{locked, required_tier, preview}` is handled as **HTTP 200**, not
217	  an error.
218	- **No trading language, ever.** Copy never uses 明牌/飆股/目標價/買賣/進場/停損. Analyst
219	  targets/ratings render **attributed** ("N analysts, as of <date>"), never restated as ours.
220	- **Non-trading motion** ([interaction-spec §0](../docs/web/interaction-spec.md)): no magnitude-scaled
221	  motion (a bigger move is not a bigger animation), no launch/celebratory motion on price/score, and
222	  the **多空 gauge needle never re-animates on a quote tick** — it eases only on load / on a *new
223	  score fetch*. The score gauge reads the `score` query cache, not the live layer.
224	- **Personality / pet layers style tone only** — never introduce trading language, never drop a
225	  caveat, never colour or annotate data. Cosmetic pet motion stays in its Sisi/gacha region.
226	
227	## 5. Performance & state management
228	
229	- **Cache-bust = background revalidate, never hard reload.** Keep the last good value, update **in
230	  place**, flash the tick (`--up-weak`/`--down-weak`, `dur.tick`). Never blank a panel, never reset
231	  scroll/sort/selection; skeleton is **cold-load only** ([data-freshness §6](../docs/web/data-freshness.md)).
232	- **Client single-flight** — dedupe in-flight requests per key (TanStack does this natively): N
233	  components asking for `2330@TW` = one request. The frontend mirror of the backend stampede guard.
234	- **rAF-batched SSE ticks via motion values** — presentation reads `live ?? cachedSnapshot`; the
235	  TanStack snapshot is left untouched so scroll/sort/selection/chart `activeIndex` survive live
236	  updates. **Never `useState` per tick, never re-render the tree per tick.**
237	- **No double-caching / TTL-stacking.** One value is owned by exactly one cache tier (RSC *or*
238	  TanStack). A **single shared `FRESHNESS[class]` map** is the source of truth for RSC `revalidate`,
239	  TanStack `staleTime`/`gcTime`, and the `Cache-Control` header — layers **mirror** one budget, never
240	  each pick their own ([data-freshness §5](../docs/web/data-freshness.md)). `Cache-Control` uses
241	  `stale-while-revalidate`, not a third opaque TTL.
242	- **Discard non-monotonic responses** — drop any reply whose `as_of`/`version` is not newer than what
243	  is shown; use `ETag` + `If-None-Match` so a no-change revalidate is a payload-free `304`.
244	- **Optimistic + reconcile** for user actions (watchlist star/reorder): apply instantly, reconcile
245	  with `version` **CAS → 409** (row snaps back + toast, no flicker); lexorank fractional key for
246	  reorder; star toggle idempotent.
247	- **Lean RSC payloads**; `tabular-nums` everywhere for flicker-free ticks and column alignment.
248	
249	## 6. Full-state discipline & UX
250	
251	Never ship happy-path only. Three shared components on every data surface
252	([interaction-spec §4](../docs/web/interaction-spec.md)):
253	
254	- **`LoadingState`** — shaped shimmer skeleton **always** (block shapes matching final layout), never
255	  a bare spinner. Two classes: **analysis-generation** (chat turns, AI 摘要/SWOT, score compute,
256	  screener run) adds a narration line ("Miru 正在看盤面…"); a **plain data refetch** (grids/charts)
257	  shows the skeleton **only** — no narration. A **background** revalidate shows neither.
258	- **`EmptyState`** — two classes, do not conflate: **cold-start** (user has nothing → empty
259	  watchlist/chats/first-run) routes the primary action to **Chat** ("問我任何台股問題") / server-seeded
260	  preset; **query/coverage** (no data for this range) is a **calm inline** message that keeps the
261	  frame + `<Caveat>` and has **no chat CTA** (screener empty offers "放寬 gates", not chat).
262	- **`ErrorState`** — inline, calm, **retryable** chip; per-panel `<ErrorBoundary>` so one failing view
263	  never blanks the page. Source-health degrade → honest `<Caveat>` ("資料來源延遲…以下為最後可得"),
264	  never a silent fabrication.
265	- `:active` tactile feedback on pressables; button/label contrast meets AA in **both** themes;
266	  radius/shape/color locks from [design-system §5](../docs/web/design-system.md) applied everywhere
267	  (`card 16px · input 10px · pill 999px · button 12px`).
268	
269	## 7. A11y & i18n (TW conventions)
270	
271	- **WCAG AA** contrast in **both** themes. **Full keyboard nav** for the workbench: screener table
272	  (sort on `Enter`, arrow row-nav), chart controls (`←/→` chart, `↑/↓` sheet), thread list, treemap
273	  tiles (spatial arrow-nav), SupplyChain nodes (edge traversal); **focus-trap** modals (`Esc` closes,
274	  focus returns to opener).
275	- **Focus-visible 2px `--accent` ring** on every interactive element, both themes, never removed
276	  (scope to `:focus-visible`). **44px minimum touch targets** (grip, ✕, ☆, tab pills, CTAs — incl.
277	  hover-revealed controls once revealed).
278	- **`prefers-reduced-motion` tiers** ([interaction-spec §1.2](../docs/web/interaction-spec.md)): Tier
279	  1 keeps color-only feedback, Tier 2 snaps, Tier 3 goes instant, Tiers 4–5 stop. Every motion above
280	  Tier 3 **must** have a fallback. **`prefers-reduced-transparency`** → glass (nav island, top bar,
281	  teaser) falls back to solid `--surface`.
282	- **`aria-live` discipline** ([interaction-spec §3.3](../docs/web/interaction-spec.md)): `polite` on the
283	  **final answer only** (fires at `turn.end`); **`off`** on thinking/tool narration, live ticks,
284	  marquee, and auto re-rank churn; `polite` only for **user-initiated** keyboard reorder.
285	- **zh-TW first.** Quality **Traditional** Chinese face (Noto Sans TC / source-han) via `next/font`;
286	  **never a Simplified fallback**, correct TC glyph variants. Body CJK 16px/line-height 1.7. **Don't
287	  break `台積電 2330` mid-token.** Bilingual (zh label + en sublabel), **dark-first** (design both,
288	  dark primary).
289	
290	## 8. Security
291	
292	- **No secrets in the client bundle** — every secret stays server-side (RSC / route handlers).
293	  **BYOK keys never reach the browser**; the client sends intent, the server holds the key.
294	- **Short-lived JWT access token in memory** (~15 min); **refresh token in an
295	  `httpOnly; Secure; SameSite=Lax` cookie** with rotation + reuse detection (mirrors the backend).
296	  Never persist a token to `localStorage`.
297	- **Untrusted external content is data, never instructions.** News/PTT/`web_search`/model output
298	  render as **inert text** — never as executable/markup instructions to the app. **No
299	  `dangerouslySetInnerHTML`** on model or source output; sanitize + render as plain text/typed nodes.
300	- **Teaser envelope `{locked, required_tier, preview}`** is a normal **HTTP 200** payload the UI masks
301	  (馬賽克), **not** a `401`/error — a locked capability is an upgrade moment, not a failure.
302	- **CSP posture:** strict `default-src 'self'`, no inline scripts (nonce/hash only), connect-src
303	  scoped to the API + SSE origins; no third-party analytics/font/CDN that leaks a token or symbol.
304	
305	---
306	
307	*Blueprint stage — this is the contract to build to. When the UI is actually built from these specs,
308	the **`impeccable`** agents run as the build-time review gate against the shipped pixels
309	([README §"Build-time review"](../docs/web/README.md)); this doc governs the code, that gate governs
310	the result.*
311	