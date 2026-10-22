# web/ — Coding Standards

The Next.js (App Router) frontend. The code under `web/src/` and the rules below are the source of
truth; design tokens live in `src/app/tokens.css`. **Governance rules (§4) are non-negotiable and
load-bearing — never relax them to make a screen ship.** Shared guidance for every agent and
engineer is in the project-root [`../AGENTS.md`](../AGENTS.md); the backend contract is
[`../docs/backend/api-reference.md`](../docs/backend/api-reference.md) (the code wins if they differ).

Section numbers (§1–§8, §2.1) are cited from source comments; keep them stable.

## 1. Toolchain

| Concern | Choice |
|---|---|
| Runtime / package manager | **Node 22** (`.nvmrc`, `engines`), **pnpm** only (`packageManager` is pinned; commit `pnpm-lock.yaml`; no npm/yarn) |
| Framework | **Next.js 16** App Router + **React 19**, TypeScript **strict** |
| Lint + format | **Biome** (`biome.json`): 2-space indent, 100 columns, double quotes, trailing commas, semicolons; organizes imports; `noExplicitAny` and `useImportType` are errors; Tailwind class order is a warning |
| Styling | **Tailwind v4** (CSS-first `@theme`, `@tailwindcss/postcss`), own-the-code UI primitives in `components/ui/` (button, card, badge, skeleton, toast, …) skinned to the tokens |
| Data | **TanStack Query v5** for server state, **Zustand v5** for small client state, **Zod** at every boundary |
| i18n | **next-intl**; `messages/zh-TW.json` and `messages/en.json` must keep the same keys. Restart `pnpm dev` after editing a messages file |
| Motion | **`motion/react`** only. One animation library; GSAP is not used |
| Charts | **lightweight-charts** for candles, **recharts** for everything else |
| 3D | `three` + `@react-three/fiber` + `drei`, used only by the 板塊輪動 scene, loaded with `next/dynamic` |
| Icons | **Phosphor** (`@phosphor-icons/react`), `weight="regular"`. No hand-rolled SVG icons, no emoji in product chrome |
| Fonts | `next/font` (`src/app/fonts.ts`). Never a `<link>` to a font CDN |
| Tests | **Vitest** (node by default; a file opts into jsdom with `// @vitest-environment jsdom`) + Testing Library; render helper `src/test/render.tsx` (`renderWithIntl`) |
| Dead code | **knip** (`knip.json`) fails on unused files, exports and dependencies |
| Browser checks | The Chrome DevTools MCP against a running dev server (keyboard, reduced motion, responsive, console, network). There is no Playwright / e2e script |

```bash
pnpm install            # from the lockfile (CI: --frozen-lockfile)
pnpm dev                # next dev
pnpm lint               # biome check . (lint + format verify); pnpm lint:fix writes fixes
pnpm typecheck          # tsc --noEmit (also runs the API contract checks, §2)
pnpm test               # vitest run
pnpm knip               # unused files / exports / dependencies
pnpm build              # next build (stop `pnpm dev` first, then restart it)
pnpm gen:api            # regenerate src/types/generated/openapi.ts from openapi.json
```

CI (`frontend-ci`) runs, in order: lint → generated API types are current (`gen:api` leaves no diff)
→ typecheck → knip → vitest → production build. All must pass.

`tsconfig.json` turns on `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`,
`noImplicitOverride` and `verbatimModuleSyntax`; the alias `@/*` maps to `src/*`.

## 2. Type & style

- **No `any`.** Use `unknown` and narrow. The only escape is an isolated third-party-edge adapter
  with `// biome-ignore … — <reason>`; `any` never crosses a module seam.
- **Zod at every boundary** — API responses, the SSE envelope, URL params. A malformed payload
  fails loudly where it enters; it never renders half-parsed.
- **Shared contracts live in `src/types/` as Zod schemas, and the TS type is `z.infer`'d** — never
  hand-written next to the schema. One file per domain (`types/market/*`, `types/stock/*`,
  `types/account.ts`, …), each naming the backend routes it mirrors. Import from the module that
  owns the name (no barrel files) and never redefine a schema another module exports. Types used by
  one feature only stay co-located (`app/.../<feature>/types.ts`).
- **Schemas are checked against the backend at compile time.** `openapi.json` (kept current by the
  backend's `test_openapi_contract.py`) → `pnpm gen:api` → `types/generated/openapi.ts`;
  `types/generated/contract-checks.ts` has one line per API client asserting that the response the
  backend documents is accepted by the schema that parses it. A renamed, removed, retyped or
  newly-nullable field is a `tsc` error naming the field; an added field is fine. **Adding an API
  client means adding its line there** (`contract-coverage.test.ts` fails until you do). A backend
  route that returns an untyped dict has nothing to compare and is listed at the bottom of that file.
- **Colours come from CSS-variable tokens, never a hex literal** (`--bg`, `--surface`, `--text`,
  `--accent`, `--up`, `--down`, `--chart-1..6`, …, defined once in `src/app/tokens.css`). The one
  deliberate exception is the 3D sector scene, which needs concrete colour values for three.js and
  keeps its palette inside `components/market/sector-rotation-scene/`.
- **Naming:** files are kebab-case (`signed-change.tsx` exports `SignedChange`; `use-xxx.ts`
  exports `useXxx`); one main component per file; **named exports only** (Next's `page.tsx`,
  `layout.tsx`, `route.ts` defaults are the exception). Route segments are lowercase. Every
  price/percent/score uses `font-num` + `tabular-nums`.
- **Components:** arrow functions for callbacks, `function` declarations for components and exported
  utilities; destructure props in the signature; imports are grouped by Biome — don't hand-sort.
- **Dependency direction:** `components/`, `lib/`, `hooks/`, `types/` and `i18n/` never import from
  `src/app/` (Biome's `noRestrictedImports` enforces it). Shared pieces live in those folders; route
  folders only compose them.

### 2.1 TypeScript rules (avoid → prefer)

All are errors, not warnings.

- `any` → `unknown`, then narrow. `as` casts → type guards or `satisfies` (`as const` is fine).
  Non-null `!` → `?.` and `??`. `||` for defaults → `??` (so `0` and `''` survive).
- Boxed `Number`/`String`/`Object` → primitives; `enum` → a union of string literals or an
  `as const` object; `namespace` and `///` → ES modules.
- **Explicit return types on every exported function, hook and component.**
- No floating promises: `await`, `void`, or `.catch()` every one. No async function where a sync
  callback is expected (`onClick={async …}`). Throw `Error` instances only.
- Discriminated unions with an exhaustive `switch` (`default: assertNever(x)`) for SSE event kinds,
  score bands and tabs, so a new variant is a compile error.
- `import type { … }` for type-only imports. `@ts-expect-error — <reason>` instead of `@ts-ignore`.
- Readability: no nested ternaries (use a named helper or if/else); split a long effect into named
  steps; delete write-only state; name a union or shape that appears in more than one signature;
  prefer `array.at(-1)`, `replaceAll`, and other built-ins over hand-rolled index math; a
  duplicated block of markup or logic becomes one component in `components/ui/` (see
  `month-grid-calendar.tsx`). Apply this while writing, not after a linter complains.
- **Strict-flag consequences:** `arr[i]` and `record[k]` are `T | undefined` — handle it, don't `!`
  it away. With `exactOptionalPropertyTypes`, `x?: T` means "may be absent", not "may be
  `undefined`": declare `x?: T | undefined` on our own props that callers forward, and omit the key
  (`{...(cond ? { cursor } : {})}`) for third-party props (recharts, Radix, motion).

## 3. Architecture invariants

- **RSC boundary.** `page.tsx` and `layout.tsx` are thin server shells: they resolve params and
  render a client panel. Anything using motion, pointers, streams or browser APIs is an isolated
  `'use client'` leaf, pushed as deep as possible. Never mark a whole page client to unlock one
  interactive child.
- **Server state = TanStack Query, fetched in the browser.** Nearly every read depends on the
  signed-in user's tier and session cookie, so pages do not fetch on the server (server fetching is
  only for public, user-independent data, and none of the current pages need it). Rules:
  - `staleTime` comes from the shared `FRESHNESS` map (`lib/freshness.ts`: `live`, `standard`,
    `slow`, `reference`, `static`) — pick the class that matches the data, don't invent a number.
  - A read that more than one component makes gets a key from `lib/api/query-keys.ts` and a shared
    hook (`hooks/use-stock-data.ts`), not a copy. Keys run resource → market → symbol → arguments.
  - End-of-day data is refreshed soon after ingest writes it by the `data.updated` push
    (`hooks/use-data-updates.ts`, mounted once for the whole app by
    `components/providers/data-updates-bridge.tsx`). `lib/data-update-refresh.ts` decides what
    and when: the reads on screen whose key names the signalled market (or none), excluding
    account state and live feeds; signals are merged over 2 s and the refresh is delayed a random
    0-20 s so thousands of tabs do not refetch at once; it waits while the tab is hidden and
    refreshes everything after a reconnect. Keys need no `[capability, market]` shape.
  - All requests go through the one seam `lib/api/http.ts` (credentials, refresh-on-401, the typed
    `{code, message, retryable}` error); each feature keeps its own named error class.
- **Zustand is for small client-only state** — the active chat turn (`chat/turn-store.ts`), the
  market tab, the search palette, the new-thread signal, toasts. Never put a server read in it,
  and never write live quote ticks into the TanStack cache.
- **Streaming.** `POST /agent/query` returns `text/event-stream`, so the chat client
  (`lib/api/agent-stream.ts`) reads the body as a stream and parses frames by hand; every frame is
  validated against the SSE envelope schema (`types/sse.ts`). Live quotes and `data.updated` use
  their own small clients (`lib/api/quote-stream.ts`, `lib/api/data-updates.ts`). Automatic resume
  after a dropped stream is not implemented.
- **Continuous values never go through `useState` per tick.** Live quote frames are coalesced onto
  `requestAnimationFrame` (`hooks/use-live-quote.ts`), so a burst produces at most one render, and
  a watchlist of N rows does not re-render the tree N times.
- **Layout:** CSS grid for the app frame and multi-panel views; `min-h-[100dvh]`, never `h-screen`.
  Responsive checks use the card's own width where the surface is a card (container queries), not
  only the viewport — the sidebar and watchlist take a lot of a laptop screen.

## 4. Governance-as-code (non-negotiable)

- **紅漲綠跌 is a token, never colour alone.** `--up` (漲, red) and `--down` (跌, green) apply
  **only** to price / % / score / candle moves, and always pair with **▲/▼ + sign + an `aria-label`**
  carrying the direction and value ("上漲 1.45%"). Use `components/ui/signed-change.tsx`
  (`SignedChange`, helpers in `lib/signed.ts`) instead of hand-rolling the glyph and colour; `0`
  renders neutral. `--accent` (teal) is chrome only — never a price. Categorical series use
  `--chart-1..6`, never the signed tokens.
- **No caveat chip.** The web app renders no per-surface "analysis is not investment advice" badge
  (the old `<Caveat>` component is gone). Wording is still never trading advice (below), the page
  footer under the chat input keeps its plain disclaimer text, and the backend output guardrail still
  blocks buy/sell/target/sizing language in every answer. Pair cached, possibly-stale surfaces with a
  data-freshness badge.
- **馬賽克 teaser for sponsor data:** frame / headline / label columns stay sharp, the value layer
  sits under `backdrop-blur` + diagonal texture + a centred lock chip + **one calm CTA**. Never a
  wall or interstitial; the answer proceeds around it. A teaser envelope
  `{locked, required_tier, preview}` is a normal **HTTP 200** payload, not an error.
- **No trading language, ever.** Copy never uses 明牌 / 飆股 / 目標價 / 買賣 / 進場 / 停損. Analyst
  targets and ratings render attributed ("N analysts, as of <date>"), never restated as ours.
- **Non-trading motion.** No magnitude-scaled motion (a bigger move is not a bigger animation), no
  celebratory motion on price or score, and the 多空 gauge needle never re-animates on a quote tick
  — it eases only on load or on a new score fetch (it reads the `score` query cache, not the live
  layer).
- **Personality and pet layers style tone only** — never trading language, never a dropped caveat,
  never colouring or annotating data. Pet motion stays inside its own region.

## 5. Performance & state

- **A cache refresh is a background revalidate, never a hard reload.** Keep the last good value,
  update in place, flash the tick (`--up-weak` / `--down-weak`). Never blank a panel or reset
  scroll, sort or selection. A skeleton is for the cold load only.
- **Single-flight:** TanStack dedupes in-flight requests per key, so N components asking for
  `2330` make one request.
- **One owner per value.** Don't stack caches or TTLs; the `FRESHNESS` map is the single freshness
  budget. Live ticks are presentation (`live ?? cachedSnapshot`); the TanStack snapshot underneath
  stays untouched so scroll, sort and chart state survive.
- **Optimistic updates with reconcile** for user actions (watchlist star / reorder, settings): apply
  instantly, and if the server answers 409 (stale `version`) snap back with a toast.
- **Load heavy, rarely-first-visible code lazily** (`next/dynamic`, as the 3D scene does). Keep RSC
  payloads lean.

## 6. Full-state discipline & UX

Never ship the happy path only. Every data surface covers four states:

- **Loading** — a shaped shimmer skeleton (`ui/skeleton.tsx`, `market/card-skeleton.tsx`) that
  matches the final layout, never a bare spinner. Analysis-generation work (chat turns, AI summary,
  score compute, screener run) adds a narration line; a plain refetch shows the skeleton only; a
  background revalidate shows neither.
- **Error** — inline, calm and retryable. `QueryState` (`ui/query-state.tsx`) renders loading /
  error-with-retry / empty for a query; `PanelErrorBoundary` (`ui/panel-error-boundary.tsx`) and
  `app/[locale]/(app)/error.tsx` keep one failing panel from blanking the page. A degraded source
  shows an honest caveat ("data delayed — showing the last available"), never a silent fabrication.
- **Empty** (`ui/empty-state.tsx`) — two kinds, don't conflate them. *Cold start* (the user has
  nothing yet) routes to the primary action (Chat, a seeded preset). *No data for this range* is a
  calm inline message that keeps the frame and `<Caveat>` and has no chat CTA (an empty screener
  offers "relax the gates", not chat).
- **Shape and feel:** `:active` feedback on pressables; AA contrast in both themes; radius locks
  card 16px · input 10px · pill 999px · button 12px.

## 7. A11y & i18n

- **WCAG AA** contrast in both themes. Full keyboard navigation: table sort on `Enter`, arrow row
  navigation, chart controls, treemap tiles, supply-chain nodes; modals trap focus, `Esc` closes,
  focus returns to the opener.
- **Tab strips are one behaviour.** Render `role="tablist"` > `role="tab"` with `aria-selected`;
  `TabStripBehavior` (`components/shell/tab-strip-behavior.tsx`, logic in `lib/tab-strip.ts`) adds
  the roving tab stop, Left/Right (flipped in RTL) / Home / End movement that wraps and skips
  disabled tabs, and edge-fade scrolling. Activation stays manual (`Enter`/`Space`) so arrowing
  never fires a fetch. Never hand-roll arrow keys or `tabIndex` on a tab; mark an inactive
  placeholder strip `aria-hidden`.
- **Focus-visible:** a 2px `--accent` ring on every interactive element in both themes, never
  removed (scope it to `:focus-visible`). **44px minimum touch targets**, including controls that
  appear on hover.
- **`prefers-reduced-motion` tiers:** Tier 1 keeps colour-only feedback, Tier 2 snaps, Tier 3 goes
  instant, Tiers 4–5 stop. Every motion above Tier 3 has a fallback. **`prefers-reduced-transparency`**
  turns glass surfaces (nav, top bar, teaser, overlays) into solid `--surface`.
- **`aria-live` discipline:** `polite` on the final answer only (at `turn.end`); `off` on
  thinking / tool narration, live ticks, the marquee and auto re-rank churn; `polite` only for
  user-initiated keyboard reorder.
- **zh-TW first.** Traditional Chinese with a quality TC face (Noto Sans TC via `next/font`),
  never a Simplified fallback. CJK body text 16px / line-height 1.7. Don't break `台積電 2330`
  mid-token. Bilingual (zh label + en sublabel where it helps), dark-first (design both themes).
- **Responsive:** every surface works at 390px wide with no horizontal page scroll. Wide tables sit
  in their own `overflow-x-auto` wrapper and keep numbers on one line (`whitespace-nowrap`);
  charts label every category.

## 8. Security

- **No secrets in the client bundle.** Provider and BYOK keys never reach the browser; the client
  sends intent and the server holds the key.
- **Session = httpOnly cookies set by the backend, never script-readable.** Requests use
  `credentials: "include"` through `fetchWithRefresh` (`lib/api/auth.ts`), which refreshes on a 401;
  a keep-alive (`startSessionKeepAlive`) refreshes before expiry while the tab is open. Never put a
  token in `localStorage` or `sessionStorage`.
- **Untrusted external content is data, never instructions.** News, PTT, web-search results
  and model output render as inert text or typed nodes: markdown becomes React elements
  (`chat/answer-markdown.tsx`), and any hand-built DOM uses text nodes, never `innerHTML`. No
  `dangerouslySetInnerHTML` on anything from a model or a source; the only use is the static
  no-flash theme script in the root layout. Links from untrusted content pass `lib/safe-url.ts`.
- **A locked capability is an upgrade moment, not an error** (§4): the teaser is HTTP 200.
- **No third-party analytics, font or CDN that could leak a token or a symbol.** A Content
  Security Policy is not configured yet; adding one (strict `default-src 'self'`, nonce/hash for
  inline scripts, `connect-src` limited to the API origin) is open work.
