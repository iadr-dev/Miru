# AGENTS.md — AI TW Stock/ETF Agent Framework

Shared guidance for **all** AI coding agents (Claude Code, Codex, Cursor, Amp, …). This is the
canonical root file; `CLAUDE.md` points here.

**Per-area coding standards:**

| Area | Standards doc |
|---|---|
| Python backend | [`backend/CLAUDE.md`](backend/CLAUDE.md) |
| Next.js frontend — full standards + toolchain | [`web/OVERVIEW.md`](web/OVERVIEW.md) |
| Next.js frontend — quick style reference | [`web/CODE-STYLE.md`](web/CODE-STYLE.md) |

> `web/AGENTS.md` holds **Next.js's own managed agent-rules block** (re-written by `next dev`).
> Our frontend standards live in `web/OVERVIEW.md` / `web/CODE-STYLE.md`; `web/CLAUDE.md` is a
> pointer that imports `OVERVIEW.md` so Claude Code loads the same single source.

## What this project is

A **Claude Code-style agentic framework** for Taiwan stock/ETF analysis — a provider-agnostic LLM
runtime that treats governed market data as tools, served over a read-only **FastAPI + MCP**
interface. Built on the **context-only, non-trading, bounded-observation** governance of the
reference project `SmartyJohnway/tw-market-live-data-intelligence`.

**Current stage:** the doc set is the authoritative spec — code implements it, not the other way
round. The backend is a real, growing FastAPI codebase; the Next.js frontend build is underway.

## Repository layout

```
ai-tw-stock/
├─ AGENTS.md · CLAUDE.md          # this file (+ its pointer)
├─ docs/
│  ├─ backend/                    # ← the authoritative backend blueprint (READ THIS FIRST)
│  │  ├─ README.md                #   index + confirmed decisions + module map
│  │  ├─ architecture.md          #   layered architecture + DEPENDENCY RULE + repo layout
│  │  ├─ governance.md            #   NON-NEGOTIABLE rules (non-trading, §9a/§9b/§9c carve-outs)
│  │  ├─ api-reference.md · database-schema.md · data-catalog.md
│  │  ├─ modules/ (01–23)         #   one spec per module
│  │  └─ adr/ (0001–0026)         #   why each decision was made
│  └─ mobile/                     # deferred deliverable — don't build unless asked
├─ backend/                       # Python FastAPI backend — see backend/CLAUDE.md
│  ├─ .env                        # ⚠ LIVE SECRETS — gitignored, never read aloud / commit / log
│  └─ src/<domain>/               # agent, orchestrator, llm, tools, sources, dataaccess,
│                                 #   context, ingest, analytics, calendar, conversation, memory,
│                                 #   skills, events, accounts, payments, feedback, usage,
│                                 #   scheduler, voice, personality, pets, schemas, db, api, mcp, config
└─ web/                           # Next.js App Router frontend — see web/OVERVIEW.md
   ├─ CLAUDE.md · AGENTS.md       # pointer to OVERVIEW.md · Next.js-managed block (see note above)
   ├─ OVERVIEW.md · CODE-STYLE.md # our coding standards + quick style reference
   └─ src/                        # app/ (routes), components/, lib/, i18n/, types/
```

## The rules that override everything (governance — `docs/backend/governance.md`)

These are inherited from the reference project and are **non-negotiable**. Never relax them to
make code, a test, or a feature pass:

1. **Context-only, non-trading.** The agent produces caveated analytical context. It **never**
   emits buy/sell/hold, price targets, or position sizing. The 多空 score / screener / SWOT are
   *descriptive factor context*, never a signal.
2. **Only `sources/` performs network I/O.** Every other layer goes through it (via the dataaccess
   router). No socket/HTTP/scraper/SDK call lives outside `sources/`.
3. **No autonomy over the network** — no full-market scans, no startup network calls (prod), no
   unbounded polling. The three sanctioned scheduled activities are the **only** exceptions:
   §9a user-authorized jobs · §9b demand-driven live quotes · §9c platform data-ingest.
4. **Caveats + provenance always.** Every output carries a currentness caveat; analyst
   targets/ratings are attributed third-party estimates, never restated as ours; web claims are
   cited or blocked.
5. **No credentials stored/logged.** `.env` is gitignored; keys are KMS-encrypted, decrypt at call
   time only, never in prompts/journal/memory/logs.
6. **Immutable canonical** — M5F canonical packages are versioned/append-only, never mutated.
7. **Paid-data gate** — `data_class = sponsor` (FinMind Sponsor) is masked (馬賽克 teaser) for
   unentitled users. It is the *only* access gate — there are no safety tiers / approval steps.

## Architecture invariants (`docs/backend/architecture.md`)

- **One-way dependency rule:** `interfaces → product → interaction → runtime → tools → context →
  dataaccess → sources`. Nothing lower imports anything higher.
- **Capability-addressed data:** callers request a *capability* for a *(symbol, market)* — never a
  source by name; the dataaccess router resolves adapters with per-market priority-fallback.
- **Protocol seams, config-driven:** depend on `LLMClient`/`SourceAdapter`/`Tool`/
  `MarketDataStore`/store protocols; the concrete impl and the LLM provider come from config, not
  imports. 7 LLM providers, all swappable.
- **Derived-store split:** market data lives in **DuckDB/Parquet + Redis** (leader writes
  Hive-partitioned Parquet, workers read Parquet read-only); app state in **Postgres (prod) /
  SQLite (dev)**. The two are separate stores.

## Working agreements

- **Read the spec before coding.** Backend work → the relevant `docs/backend/modules/` spec +
  `governance.md`. Frontend work → `web/OVERVIEW.md`, and check `docs/backend/api-reference.md`
  before wiring any endpoint (the spec describes more surface than the backend currently
  implements — the code wins on conflict).
  The docs are the source of truth; if code and docs disagree elsewhere, update the docs
  deliberately (with an ADR, for a backend decision).
- **Keep docs in sync.** Changing a decision → update the module doc **and** add/update an ADR.
  Internal doc links must resolve (there's a link-checker convention; keep anchors valid).
- **Match surrounding style.** Backend → `backend/CLAUDE.md`. Frontend → `web/OVERVIEW.md` +
  `web/CODE-STYLE.md`. Don't introduce new tools or patterns without cause.
- **Governance tests are highest priority.** A feature isn't done until the governance suite
  (`@pytest.mark.governance`) passes for it.
- **Never read secrets aloud or echo `.env` values.** Treat `backend/.env` as exposed/rotate-worthy;
  reference variable *names*, never values.
- **Splitting one working tree into multiple PRs: patch the index, never overwrite the working
  tree.** When several independent features share uncommitted edits in the same file (e.g. two
  features both touch `capabilities.py` or a shared test's assertion counts), staging one
  feature's slice must never destroy the other pending edits sitting in that same file:
  - **Never `cp`/`Write` a hand-built "final" version of a file that has other unrelated
    uncommitted changes on it.** A full-file overwrite can't tell "the part I meant to change"
    from "the part someone else (or an earlier turn) already changed and hasn't committed yet"
    — it silently deletes the latter. This is real destructive side-effect risk, the same class
    of harm as `git checkout`/`git reset` on a file with pending work.
  - **Stage a slice with `git apply --cached <patch>`** (a hand-built or `git diff`-extracted
    hunk) — this mutates the index only and leaves the working tree completely untouched, so the
    remaining PRs' pending edits stay exactly where they were.
  - **After every stage/restore step, verify both sides**: `git diff --cached` (what will be
    committed) and `git diff` (what's still pending) — before moving on, not after the user
    notices something's missing.
  - **If a mistake happens anyway** (e.g. a `cp` overwrote pending edits), reconstruct from the
    diff already captured earlier in the conversation/terminal output rather than guessing —
    diff text is the source of truth for what was lost.

## Quick pointers

| Need | Read |
|------|------|
| Backend big picture + module map | `docs/backend/README.md`, `architecture.md` |
| What's allowed / forbidden | `docs/backend/governance.md` |
| Python coding rules + toolchain | `backend/CLAUDE.md` |
| Backend endpoints | `docs/backend/api-reference.md` |
| Tables + portability | `docs/backend/database-schema.md` |
| Data sources + ingest cadence | `docs/backend/data-catalog.md`, `modules/22-data-ingest.md` |
| A past backend decision's rationale | `docs/backend/adr/` |
| TypeScript/Next.js coding rules + toolchain | `web/OVERVIEW.md` |
| Quick frontend style reference | `web/CODE-STYLE.md` |
