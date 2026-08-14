# Code Style Standards

Quick-reference style rules for `web/`. This is the terse version; the full rationale, the
strict-mode consequences, and the governance-as-code rules live in
[`OVERVIEW.md`](OVERVIEW.md) §2/§2.1 — when the two seem to disagree, `OVERVIEW.md` wins.

- **Prefer arrow functions** for callbacks/handlers; named `function` declarations for
  components and exported utilities (keeps stack traces + Fast Refresh readable).
- **Annotate return types** on every exported function/hook/component — don't rely on
  cross-module inference (`OVERVIEW.md` §2.1).
- **Always destructure props** at the function signature, not inside the body.
- **Avoid `any`** — use `unknown` and narrow, or a strict generic. The only escape is an
  isolated third-party-edge adapter with a `// biome-ignore … — <reason>` (`OVERVIEW.md` §2).
- **Group imports:** React → Next.js → third-party libraries → local (`@/...`) — Biome's
  `organizeImports` assist enforces the ordering automatically; don't hand-sort.
