# web/ — Coding Standards

**Next.js (App Router, RSC)** frontend. The design docs that used to live in `docs/web/` were
removed, so the code under `web/src/` and the rules below are the source of truth; design tokens
live in `web/src/app/tokens.css`. **Governance rules
are non-negotiable and load-bearing — never relax them to make a screen ship.** See the project-root
[`../AGENTS.md`](../AGENTS.md) for shared guidance.

> Resolved defaults for the frontend's likely-ambiguous choices (override if the team disagrees):
> **pnpm** (lockfile committed) · **Biome** (lint+format, one tool) · **Vitest** (unit/component) +
> **chrome-devtools MCP** (browser/e2e automation — not Playwright) · `motion/react` (Framer Motion
> v11+ import path) · **TanStack Query v5** · **Zustand v5** ·
> **lightweight-charts** for candles, **visx/recharts** for the rest · TypeScript `strict` +
> `noUncheckedIndexedAccess`.

## 1. Toolchain (2026 baseline)

- **Node LTS** (22.x); pin in `.nvmrc` + `engines`. **pnpm** is the sole package manager — commit
  `pnpm-lock.yaml`. **No npm/yarn** (mixed lockfiles are a review reject).
- **Next.js App Router (RSC-first)**, TypeScript **strict**.
- **Biome** — lint **and** format (replaces ESLint+Prettier: one config, fast, no plugin drift).
  If the team prefers ESLint flat + Prettier, that's the fallback — but pick **one**, never both
  formatting.
- **Vitest** + Testing Library (unit/component). **Browser/e2e automation via the chrome-devtools
  MCP** (navigate, snapshot, click/fill, console + network inspection, Lighthouse, perf traces) — used
  for keyboard, reduced-motion, and a11y verification against a running dev server, **not Playwright**.
  Deterministic: stub the SSE stream and `/quote` feed (§7).
- **`next/font`** for Noto Sans TC + the numeric mono — **never** a `<link>` to Google Fonts (layout
  shift, no self-host, privacy).
- **Tailwind v4** via `@tailwindcss/postcss` (CSS-first `@theme`, not the legacy `tailwind.config.js`
  JS plugin path); shadcn/ui components vendored into the repo (own-the-code).
- **Animation = `motion/react`** (Framer Motion v11+) as the **default/primary** — the entire motion
  contract (`spring.default`,
  duration tokens, reduced-motion tiers) is written against it, and its React-native model + spring
  physics fit our calm product-dashboard motion. **One animation library** — do not ship a second in
  the same bundle without a reason.
  > **⚑ Open toolchain decision — GSAP vs motion/react.** GSAP (installed as skills) is stronger for
  > **scroll-driven** work (ScrollTrigger pin/scrub/parallax) and Flip/SplitText, which motion/react
  > lacks. If a heavily scroll-choreographed surface appears, GSAP may be added **scoped to that
  > surface** (option: motion/react primary + GSAP for scroll sections) — but then its motion must
  > still honor the same tokens + non-negotiables (non-trading motion, reduced-motion fallback,
  > tabular-nums). **GSAP is DOM/SVG-only — it does NOT run on React Native**, so it is a **web-only**
  > choice; the mobile client ([../docs/mobile/](../docs/mobile/)) uses `reanimated` regardless
  > ([../docs/mobile/interaction-spec.md](../docs/mobile/interaction-spec.md) §1.1). Default stays
  > motion/react until a concrete scroll-heavy need justifies the second lib.

### Commands
```bash
pnpm install                    # from committed lockfile (CI: --frozen-lockfile)
pnpm dev                        # next dev
pnpm lint                       # biome check (lint + format verify)
pnpm typecheck                  # tsc --noEmit
pnpm test                       # vitest run  (pnpm test:e2e → playwright)
pnpm build                      # next build (must pass typecheck + lint clean)
```

### Config stanzas
```jsonc
// tsconfig.json — compilerOptions
{
  "strict": true,
  "noUncheckedIndexedAccess": true,   // array/record access is T | undefined
  "exactOptionalPropertyTypes": true, // `x?: T` is "absent", not "undefined"
  "noImplicitOverride": true,
  "verbatimModuleSyntax": true,
  "moduleResolution": "bundler",
  "plugins": [{ "name": "next" }]
}
```
```jsonc
// biome.json — key rules (load-bearing)
{
  "linter": { "rules": {
    "suspicious": { "noExplicitAny": "error" },
    "a11y": "recommended",                       // labels, roles, alt, tabindex
    "nursery": { "useSortedClasses": "warn" }    // Tailwind class order
  }},
  // RSC-safety: forbid client-only imports (useState/useEffect/motion/zustand) in files
  // without a top-of-file "use client" — enforced by the react-server-components lint rule
  // (or eslint-plugin-react-server-components in the ESLint fallback). A server component
  // importing a hook is a build-time reject.
}
```

## 2. Type & style

- TypeScript **strict**, `noUncheckedIndexedAccess`. **No `any`** — isolate unavoidable 3rd-party
  edges (chart libs, some SDK/browser types) in a typed adapter with `// biome-ignore … — <reason>`;
  `any` never crosses a module seam.
- **Zod at every boundary** — validate API responses, the SSE `tool`-envelope, and URL params where
  they enter the app (the frontend mirror of the backend's pydantic role). Parse-don't-trust: a
  malformed payload fails loud at the seam, never silently renders.
- **Shared domain contracts live in `web/types/` as Zod schemas; the TS type is `z.infer`'d — never
  hand-written alongside** (one source, so validator and type can't drift). Each shared contract
  **mirrors the backend** ([../docs/backend/api-reference.md](../docs/backend/api-reference.md) + the
  cited module): the SSE module-07 envelope, `quote`/`score`/screener shapes, the `(capability,
  market, symbol)` key, `FRESHNESS` classes, tier/entitlement. **The schemas stay hand-written**
  (they carry the `.default()`s, Sponsor-lock unions and row-preview handling a generator can't
  know), **and are checked against the backend at compile time**: `web/openapi.json` (kept current
  by the backend's `test_openapi_contract.py`) → `pnpm gen:api` → `types/generated/openapi.ts`, and
  `types/generated/contract-checks.ts` has one line per API client asserting that the response the
  backend documents is accepted by the schema that parses it. A renamed, removed, retyped or newly
  nullable field is a `tsc` error naming the field; an added field is fine. **Adding an API client
  means adding its line there**; endpoints whose route still returns `dict[str, Any]` are listed
  at the bottom of that file until the backend documents a response model. Each file cites the
  backend module it mirrors so reconciliation is mechanical. **One file per domain, a folder per surface** —
  `types/market/{flows,quotes,rankings,sectors,…}.ts` and `types/stock/{chip,fundamentals,…}.ts`,
  each listing the routes it mirrors in its header; envelope pieces every contract shares
  (`MaskedTeaser`/`isMaskedTeaser`, `healthStatusSchema`, `rowsOutputSchema`) live once in
  `types/market/shared.ts`. Import from the module that owns the name (no barrel `index.ts`), and
  never redefine a schema another module already exports. **Feature-only types stay co-located**
  (`app/<feature>/types.ts`) — don't dump them in `types/`. Frontend-invented shared shapes (UI/theme
  state) also live in `web/types/` but need no backend citation.
- **Phosphor icons only** — one family, `weight="regular"`, `strokeWidth` locked globally
. Never hand-roll SVG icons; **no emoji** in code
  or product chrome.
- **CSS-variable design tokens only — never a hardcoded hex.** Every color reads from the tokens set
  once at `:root`: `--bg/--surface/--surface-2/`
  `--border/--text/--text-muted/--accent/--up/--down/--warn/--chart-1..6/--grid/--crosshair` + sector
  tokens. A literal `#…` or an ad-hoc `rgb()` in a component is a review reject.
- **Single token source, exported — web + native must not drift.** The `:root` CSS-var set is the
  **one source of truth**; the shared mobile client ([../docs/mobile/](../docs/mobile/)) consumes the
  *same* palette. Define tokens once (a `tokens.css` / `@theme` block) and **generate** the RN theme
  object (StyleSheet/reanimated constants) from it via a build step — never hand-maintain a second
  copy. A token added/changed in one place must propagate to both; a divergent native hex is a reject.
- **shadcn/ui own-the-code** — customize the vendored component to the 藍鵲 tokens; **never ship the
  default shadcn skin**. One design system: no mixing MUI/Chakra/Ant in.
- **Naming:** files are kebab-case (`signed-change.tsx` exports `SignedChange`, hooks
  `use-xxx.ts` export `useXxx`), one main component per file matching the export; route
  segments/folders lowercase; `'use client'` leaves suffixed by intent
  (`*.client.tsx` optional but consistent). `--font-num` + `tabular-nums` on **every** price/%/score.

### 2.1 TypeScript language rules (avoid → prefer)

Grounded in the official TS [Do's and Don'ts](https://www.typescriptlang.org/docs/handbook/declaration-files/do-s-and-don-ts.html)
+ the `typescript-eslint` **recommended-type-checked** set (Biome maps the equivalents). All are
**errors**, not warnings.

**Types & values — avoid → prefer:**
- **`any` → `unknown`** (then narrow). `any` disables checking and silently infects callers; `unknown`
  forces a guard. The only `any` is a `// biome-ignore … — <reason>` at a 3rd-party edge (§2), never
  across a seam. Banned by extension: `no-unsafe-{assignment,call,member-access,return,argument}`.
- **Boxed wrappers `Number`/`String`/`Boolean`/`Symbol`/`Object` → primitives** `number`/`string`/
  `boolean`/`symbol`; use `object` (or a precise shape) not `Object`, and avoid the empty `{}` type
  (it means "any non-null value"). Prefer `Record<K,V>` / an explicit interface.
- **`enum` → union of string literals** (`type Band = '偏多'|'中性'|'偏空'`) or `as const` object.
  TS `enum` emits runtime code, has surprising numeric semantics, and isn't a plain type; unions are
  erasable and exhaustively checkable. (If an enum is ever unavoidable, `const enum` is banned —
  breaks under `isolatedModules`/bundlers.)
- **`namespace` / `module X {}` / triple-slash `///` → ES modules** (`import`/`export`). No
  `require()` in app code.
- **Non-null `!` → optional chaining `?.` + nullish coalescing `??`** (`a.b?.c ?? fallback`). `!`
  asserts a lie the compiler can't check. Likewise **`||` for defaults → `??`** (so `0`/`''`/`false`
  aren't swallowed).
- **`as`-casts → type guards / `satisfies`.** Avoid `as` (esp. `as any`, double-assert `as unknown
  as T`); validate with Zod at boundaries (§2) and use **`satisfies`** to check a literal against a
  type without widening it. `as const` for literal tuples/config is fine and encouraged.

**Functions, async, control flow:**
- **Explicit return types on all exported functions/hooks** — don't rely on inference across a module
  boundary (catches accidental contract drift; mirrors the backend's "type all public surfaces").
- **No floating promises** (`no-floating-promises`): every Promise is `await`ed, `void`-marked, or
  `.catch()`-handled — a dropped rejection is an invisible bug in an SSE/quote path. **No async
  function passed where a `void`/sync callback is expected** (`no-misused-promises`) — e.g. an
  `onClick={async …}` that isn't awaited.
- **No `throw` of non-Errors** (`only-throw-error`); reject with `Error` instances.
- **Discriminated unions + exhaustive `switch`** for the SSE envelope event types, score bands, market
  tabs — a `default: assertNever(x)` arm makes a new variant a **compile error**, not a silent miss.
- **Prefer `interface` for object shapes that may be extended/implemented; `type` for unions,
  tuples, mapped/conditional types.** Don't mix arbitrarily.

**Imports & modules:**
- **`import type { … }`** for type-only imports (`verbatimModuleSyntax` is on) — keeps types out of the
  runtime bundle and keeps the RSC client/server boundary clean.
- **No default exports for components/utilities** (named exports only) — better refactors, grep-ability,
  and auto-import; Next.js `page.tsx`/`layout.tsx`/`route.ts` default exports are the sanctioned
  exception the framework requires.
- **No `// @ts-ignore`** — use `// @ts-expect-error — <reason>` (fails when the error is fixed, so it
  self-cleans); `ban-ts-comment` is on.

**Readability & maintainability — avoid → prefer:**
- **Nested ternary → a named helper function or an if/else-if chain.** A ternary whose branch is
  itself a ternary reads as a puzzle, not a value expression — pull the inner branch into a small
  named function, or use `let x; if (...) ... else if (...) ...` instead.
- **Long branching function/effect → decompose into named steps.** A function that inlines many
  independent pieces of work (e.g. a chart-setup effect drawing several unrelated series/overlays)
  should extract each independent concern into its own named function called in sequence, rather
  than one long body with a branch per concern. If you can't summarize what a function does in one
  short sentence, it's doing too much.
- **Write-only state → delete it.** Don't build up a collection/ref across a function only to clear
  it or otherwise never read its contents afterward; if nothing downstream inspects a value, delete
  it and whatever populates it.
- **Repeated inline union/shape → a named `type`/`interface`.** The same literal union or object
  shape appearing in more than one signature should be declared once and reused, not retyped at
  each call site.
- **Manual index math → the built-in that says the same thing.** Prefer `array.at(-1)` over
  `array[array.length - 1]`, `String#replaceAll()` over a global-regex `.replace()`, and other
  standard-library equivalents over hand-rolled versions — they're shorter, harder to get off-by-one,
  and read as intent rather than arithmetic.
- **Duplicated markup/logic across components → one shared component.** A byte-identical (or
  near-identical) block of markup or logic living in two places should become one component in
  `components/ui/` that both callers use, with only the surrounding data plumbing staying local to
  each caller — see `components/ui/month-grid-calendar.tsx` for the pattern.
- **Apply this section while writing new code, not only when a linter/CI gate flags it after the
  fact** — treat it as a standing bar for anything you write, the same as the strict-mode/type rules
  above it, not a reactive checklist you run once red.

**Consequences of the strict flags (§1):** `noUncheckedIndexedAccess` means `arr[i]` / `record[k]` is
`T | undefined` — **handle the `undefined`**, don't `!` it away. `exactOptionalPropertyTypes` (on):
`x?: T` means "may be absent", not "may be `undefined`". For **our own** props/params that a caller
forwards (`onEdit={canEdit ? … : undefined}`), declare `x?: T | undefined`. For a **third-party**
prop (recharts, Radix, motion) omit the key instead — `{...(cond ? { cursor } : {})}` — or give a
real fallback (`colors[k] ?? "var(--chart-1)"`).

## 3. Architecture invariants → code rules

- **RSC boundary rule.** Server Components render the static, data-heavy layout (page shells, EOD
  panels, tables, analytics reads). Anything using **motion / scroll / pointer / SSE / a browser
  API** is an **isolated `'use client'` leaf** — push the `'use client'` boundary as deep as
  possible; never mark a whole page/layout client to unlock one interactive child
.
- **Data-layer ownership — one value, one owner**:
  - **TanStack Query** for every API read, in client components: pages are thin server shells
    (`page.tsx`/`layout.tsx` only resolve params and render a client panel), and the data is
    fetched in the browser because nearly every read depends on the signed-in user's tier (the
    sponsor-masking envelope) and the session cookie. `staleTime` comes from the shared
    `FRESHNESS` map in `lib/freshness.ts`; keys for reads more than one component makes come from
    `lib/api/query-keys.ts`, with a shared hook (`hooks/use-stock-data.ts`) instead of copies.
    End-of-day data is refreshed the moment ingest writes it by the `data.updated` push
    (`hooks/use-data-updates.ts`). Server-side fetching (RSC) is only for genuinely public,
    user-independent data, and none of the current pages need it.
  - **Zustand thin store ONLY** for the live SSE layer, active thread, and theme. Live `quote` ticks
    write here (or to motion values), **never** into the TanStack cache.
- **Query keys mirror the backend key** `(capability, market, symbol[, date])` — e.g.
  `['score','TW','2330']`. A symbol crossing screener ↔ watchlist ↔ detail ↔ chat `rich_card`
  resolves to **one** coherent entry.
- **SSE client consumes the module-07 typed envelope.** Deltas append **by `node_id`**; resume via
  **`Last-Event-ID`** replayed by `event_seq` — replayed events materialize instantly (entrance
  animation suppressed). Never re-type, never duplicate a lane.
- **Continuous values use motion values / refs — never `useState` per tick.** Live ticks are
  **rAF-batched**; a watchlist of N rows ticking every ~5s must not re-render the tree N times
.
- **Grid over flex-math** for the app frame + multi-panel workbench; **`min-h-[100dvh]`**, never
  `h-screen` (mobile viewport). One design system — no second component lib.

## 4. Governance-as-code (non-negotiable)

- **紅漲綠跌 is a token, never color-alone.** `--up`(漲 red)/`--down`(跌 green) apply **only** to
  price/%/score/candles, and **always** pair with **▲/▼ + sign + an `aria-label`** carrying direction
  + value ("上漲 1.45%"). Get it wrong and the app is unusable for TW users
. **`--accent` teal is chrome-only —
  never a price.** Categorical chart series use `--chart-1..6` (never signed).
- **`<Caveat>` is a required, non-removable component** on **every** analytical/quote surface (score,
  SWOT, screener list, top-N, chat answer, `ChartWithDataSheet`, marquee, AssetHeader): non-trading
  caveat on analytical, currentness caveat on quote/market. No `prop` may hide it. Pair with
  `DataFreshnessBadge` on cached-and-possibly-stale surfaces.
- **馬賽克 teaser for sponsor data** — frame/headline/label columns **sharp**, value layer under
  `backdrop-blur` + diagonal texture + a centered lock chip + **one calm CTA**. Never a
  wall/interstitial; the answer proceeds around it. Consistent across chat `tool.locked`, detail SWOT,
  top-N/screener columns.
  A `tool.locked` teaser envelope `{locked, required_tier, preview}` is handled as **HTTP 200**, not
  an error.
- **No trading language, ever.** Copy never uses 明牌/飆股/目標價/買賣/進場/停損. Analyst
  targets/ratings render **attributed** ("N analysts, as of <date>"), never restated as ours.
- **Non-trading motion**: no magnitude-scaled
  motion (a bigger move is not a bigger animation), no launch/celebratory motion on price/score, and
  the **多空 gauge needle never re-animates on a quote tick** — it eases only on load / on a *new
  score fetch*. The score gauge reads the `score` query cache, not the live layer.
- **Personality / pet layers style tone only** — never introduce trading language, never drop a
  caveat, never colour or annotate data. Cosmetic pet motion stays in its Sisi/gacha region.

## 5. Performance & state management

- **Cache-bust = background revalidate, never hard reload.** Keep the last good value, update **in
  place**, flash the tick (`--up-weak`/`--down-weak`, `dur.tick`). Never blank a panel, never reset
  scroll/sort/selection; skeleton is **cold-load only**.
- **Client single-flight** — dedupe in-flight requests per key (TanStack does this natively): N
  components asking for `2330@TW` = one request. The frontend mirror of the backend stampede guard.
- **rAF-batched SSE ticks via motion values** — presentation reads `live ?? cachedSnapshot`; the
  TanStack snapshot is left untouched so scroll/sort/selection/chart `activeIndex` survive live
  updates. **Never `useState` per tick, never re-render the tree per tick.**
- **No double-caching / TTL-stacking.** One value is owned by exactly one cache tier (RSC *or*
  TanStack). A **single shared `FRESHNESS[class]` map** is the source of truth for RSC `revalidate`,
  TanStack `staleTime`/`gcTime`, and the `Cache-Control` header — layers **mirror** one budget, never
  each pick their own. `Cache-Control` uses
  `stale-while-revalidate`, not a third opaque TTL.
- **Discard non-monotonic responses** — drop any reply whose `as_of`/`version` is not newer than what
  is shown; use `ETag` + `If-None-Match` so a no-change revalidate is a payload-free `304`.
- **Optimistic + reconcile** for user actions (watchlist star/reorder): apply instantly, reconcile
  with `version` **CAS → 409** (row snaps back + toast, no flicker); lexorank fractional key for
  reorder; star toggle idempotent.
- **Lean RSC payloads**; `tabular-nums` everywhere for flicker-free ticks and column alignment.

## 6. Full-state discipline & UX

Never ship happy-path only. Three shared components on every data surface
:

- **`LoadingState`** — shaped shimmer skeleton **always** (block shapes matching final layout), never
  a bare spinner. Two classes: **analysis-generation** (chat turns, AI 摘要/SWOT, score compute,
  screener run) adds a narration line ("Miru 正在看盤面…"); a **plain data refetch** (grids/charts)
  shows the skeleton **only** — no narration. A **background** revalidate shows neither.
- **`EmptyState`** — two classes, do not conflate: **cold-start** (user has nothing → empty
  watchlist/chats/first-run) routes the primary action to **Chat** ("問我任何台股問題") / server-seeded
  preset; **query/coverage** (no data for this range) is a **calm inline** message that keeps the
  frame + `<Caveat>` and has **no chat CTA** (screener empty offers "放寬 gates", not chat).
- **`ErrorState`** — inline, calm, **retryable** chip; per-panel `<ErrorBoundary>` so one failing view
  never blanks the page. Source-health degrade → honest `<Caveat>` ("資料來源延遲…以下為最後可得"),
  never a silent fabrication.
- `:active` tactile feedback on pressables; button/label contrast meets AA in **both** themes;
  radius/shape/color locks applied everywhere
  (`card 16px · input 10px · pill 999px · button 12px`).

## 7. A11y & i18n (TW conventions)

- **WCAG AA** contrast in **both** themes. **Full keyboard nav** for the workbench: screener table
  (sort on `Enter`, arrow row-nav), chart controls (`←/→` chart, `↑/↓` sheet), thread list, treemap
  tiles (spatial arrow-nav), SupplyChain nodes (edge traversal); **focus-trap** modals (`Esc` closes,
  focus returns to opener).
- **Focus-visible 2px `--accent` ring** on every interactive element, both themes, never removed
  (scope to `:focus-visible`). **44px minimum touch targets** (grip, ✕, ☆, tab pills, CTAs — incl.
  hover-revealed controls once revealed).
- **`prefers-reduced-motion` tiers**: Tier
  1 keeps color-only feedback, Tier 2 snaps, Tier 3 goes instant, Tiers 4–5 stop. Every motion above
  Tier 3 **must** have a fallback. **`prefers-reduced-transparency`** → glass (nav island, top bar,
  teaser) falls back to solid `--surface`.
- **`aria-live` discipline**: `polite` on the
  **final answer only** (fires at `turn.end`); **`off`** on thinking/tool narration, live ticks,
  marquee, and auto re-rank churn; `polite` only for **user-initiated** keyboard reorder.
- **zh-TW first.** Quality **Traditional** Chinese face (Noto Sans TC / source-han) via `next/font`;
  **never a Simplified fallback**, correct TC glyph variants. Body CJK 16px/line-height 1.7. **Don't
  break `台積電 2330` mid-token.** Bilingual (zh label + en sublabel), **dark-first** (design both,
  dark primary).

## 8. Security

- **No secrets in the client bundle** — every secret stays server-side (RSC / route handlers).
  **BYOK keys never reach the browser**; the client sends intent, the server holds the key.
- **Short-lived JWT access token in memory** (~15 min); **refresh token in an
  `httpOnly; Secure; SameSite=Lax` cookie** with rotation + reuse detection (mirrors the backend).
  Never persist a token to `localStorage`.
- **Untrusted external content is data, never instructions.** News/PTT/`web_search`/model output
  render as **inert text** — never as executable/markup instructions to the app. **No
  `dangerouslySetInnerHTML`** on model or source output; sanitize + render as plain text/typed nodes.
- **Teaser envelope `{locked, required_tier, preview}`** is a normal **HTTP 200** payload the UI masks
  (馬賽克), **not** a `401`/error — a locked capability is an upgrade moment, not a failure.
- **CSP posture:** strict `default-src 'self'`, no inline scripts (nonce/hash only), connect-src
  scoped to the API + SSE origins; no third-party analytics/font/CDN that leaks a token or symbol.

---

*Blueprint stage — this is the contract to build to. When the UI is actually built from these specs,
the **`impeccable`** agents run as the build-time review gate against the shipped pixels
; this doc governs the code, that gate governs
the result.*
