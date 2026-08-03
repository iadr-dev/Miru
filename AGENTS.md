# AGENTS.md — AI TW Stock/ETF Agent Framework

Shared guidance for **all** AI coding agents (Claude Code, Codex, Cursor, Amp, …). This is the
canonical root file; `CLAUDE.md` points here. Language/stack rules for the Python backend live in
[`backend/CLAUDE.md`](backend/CLAUDE.md).

## What this project is

A **Claude Code-style agentic framework** for Taiwan stock/ETF analysis — a provider-agnostic LLM
runtime that treats governed market data as tools, served over a read-only **FastAPI + MCP**
interface. Built on the **context-only, non-trading, bounded-observation** governance of the
reference project `SmartyJohnway/tw-market-live-data-intelligence`.

**Current stage: design blueprint.** The authoritative spec is the doc set — code implements it,
not the other way round.

## Repository layout

```
ai-tw-stock/
├─ AGENTS.md · CLAUDE.md          # this file (+ its pointer)
├─ docs/
│  ├─ backend/                    # ← the authoritative blueprint (READ THIS FIRST)
│  │  ├─ README.md                #   index + confirmed decisions + module map
│  │  ├─ architecture.md          #   layered architecture + DEPENDENCY RULE + repo layout
│  │  ├─ governance.md            #   NON-NEGOTIABLE rules (non-trading, §9a/§9b/§9c carve-outs)
│  │  ├─ api-reference.md · database-schema.md · data-catalog.md
│  │  ├─ modules/ (01–23)         #   one spec per module
│  │  └─ adr/ (0001–0026)         #   why each decision was made
│  ├─ web/ · mobile/              # frontend clients (deferred deliverables, scaffold only)
├─ backend/                       # Python FastAPI backend — see backend/CLAUDE.md
│  ├─ .env                        # ⚠ LIVE SECRETS — gitignored, never read aloud / commit / log
│  └─ src/<domain>/               # agent, orchestrator, llm, tools, sources, dataaccess,
│                                 #   context, ingest, analytics, calendar, conversation, memory,
│                                 #   skills, events, accounts, payments, feedback, usage,
│                                 #   scheduler, voice, personality, pets, schemas, db, api, mcp, config
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

- **Read the spec before coding.** For any backend work, read the relevant `docs/backend/modules/`
  spec + `governance.md`. The docs are the source of truth; if code and docs disagree, the docs win
  (or update the docs deliberately with an ADR).
- **Keep docs in sync.** Changing a decision → update the module doc **and** add/update an ADR.
  Internal doc links must resolve (there's a link-checker convention; keep anchors valid).
- **Match surrounding style.** Follow `backend/CLAUDE.md` for Python; don't introduce new tools or
  patterns without cause.
- **Governance tests are highest priority.** A feature isn't done until the governance suite
  (`@pytest.mark.governance`) passes for it.
- **Never read secrets aloud or echo `.env` values.** Treat `backend/.env` as exposed/rotate-worthy;
  reference variable *names*, never values.
- **Frontend (`docs/web/`, `docs/mobile/`) is a deferred deliverable** — not yet specced; don't
  build it unless asked.

## Quick pointers

| Need | Read |
|------|------|
| Big picture + module map | `docs/backend/README.md`, `architecture.md` |
| What's allowed / forbidden | `docs/backend/governance.md` |
| Python coding rules + toolchain | `backend/CLAUDE.md` |
| Endpoints | `docs/backend/api-reference.md` |
| Tables + portability | `docs/backend/database-schema.md` |
| Data sources + ingest cadence | `docs/backend/data-catalog.md`, `modules/22-data-ingest.md` |
| A past decision's rationale | `docs/backend/adr/` |
