# Code Style — quick reference

The terse version of the rules for `web/`. The reasoning, the strict-mode consequences and the
governance rules live in [`OVERVIEW.md`](OVERVIEW.md) (§2, §2.1, §4); when the two seem to
disagree, `OVERVIEW.md` wins. Biome formats and lints (`pnpm lint:fix`) — don't hand-format.

## Files and exports
- **kebab-case files**, one main component per file, named after its export:
  `signed-change.tsx` → `SignedChange`, `use-live-quote.ts` → `useLiveQuote`.
- **Named exports only.** The exceptions are Next's `page.tsx`, `layout.tsx`, `route.ts`,
  `error.tsx`, which must default-export.
- **Colocate** a feature's own types and helpers next to it; shared pieces go in `components/`,
  `lib/` or `hooks/`. Shared code never imports from `src/app/`.
- **Imports** use the `@/` alias for anything outside the current folder. Biome orders them
  (React → Next.js → third-party → local); type-only imports use `import type`.

## Components and hooks
- `function` declarations for components and exported utilities; arrow functions for callbacks
  and handlers.
- **Destructure props in the signature.** Props are `readonly`. Our own optional props that callers
  forward are declared `x?: T | undefined` (`exactOptionalPropertyTypes`).
- **Annotate the return type of every exported function, hook and component** (`ReactNode` for
  components).
- `'use client'` goes on the smallest leaf that needs it; pages and layouts stay server shells.
- No `useState` per tick for fast-changing values — use refs, motion values or `rAF` batching.

## Types
- **No `any`.** Use `unknown` and narrow; a Zod schema at every boundary and `z.infer` for the type.
- Prefer `satisfies` and type guards to `as`. No non-null `!`; use `?.` and `??`.
- `arr[i]` and `record[k]` are `T | undefined` — handle it.
- A union of string literals or an `as const` object instead of `enum`.
- Exhaustive `switch` with `assertNever` on discriminated unions.

## Styling
- **Tailwind classes with design tokens** (`text-[var(--text-muted)]`, `bg-[var(--surface)]`).
  **No hex literals** in components. Biome warns on class order.
- Numbers (price, %, score) use `font-num` + `tabular-nums`.
- Price moves go through `SignedChange` (▲/▼ + sign + `aria-label`); never colour alone.
- Phosphor icons only, `weight="regular"`. No emoji in product chrome.
- Touch targets are at least 44px; interactive elements keep the `:focus-visible` ring.

## Data and state
- Server reads: TanStack Query with `staleTime` from `FRESHNESS`; shared keys from
  `lib/api/query-keys.ts`; every request through `lib/api/http.ts`.
- Small client state: Zustand. Never put a server read or a live tick in it.
- Show loading / error / empty with `QueryState` and `EmptyState`; never ship only the happy path.

## Copy and i18n
- Every user-facing string goes through `next-intl`; add the key to **both**
  `messages/zh-TW.json` and `messages/en.json`.
- No trading language (明牌 / 飆股 / 目標價 / 買賣 / 進場 / 停損), ever.

## Tests
- Vitest next to the code (`*.test.ts` / `*.test.tsx`); jsdom files start with
  `// @vitest-environment jsdom` and render through `renderWithIntl` (`src/test/render.tsx`).
- A new API client needs its line in `types/generated/contract-checks.ts`.
- Before pushing: `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm knip`; `pnpm build` for
  anything that touches routing, messages or config.

## Comments
- Explain *why* when it isn't obvious from the code — a governance rule, a constraint, a
  workaround. Don't restate what the line does.
- `// biome-ignore … — <reason>` and `// @ts-expect-error — <reason>` always carry the reason.
