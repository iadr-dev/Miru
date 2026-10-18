<div align="center">

<img src="web/public/logo.png" alt="Miru" width="120" />

# Miru

**Your AI assistant for Taiwan & US stocks and ETFs: it explains the market and shows its sources.**

[![Backend CI](https://github.com/iadr-dev/Miru/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/iadr-dev/Miru/actions/workflows/backend-ci.yml)
[![Frontend CI](https://github.com/iadr-dev/Miru/actions/workflows/frontend-ci.yml/badge.svg)](https://github.com/iadr-dev/Miru/actions/workflows/frontend-ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-f2873a.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)
![Node](https://img.shields.io/badge/node-22+-339933?logo=node.js&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-SSE-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-App%20Router-000000?logo=next.js&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-read--only-6E56CF)

**English** | [繁體中文](README_TW.md)

</div>

<!-- Promo video -->
https://github.com/user-attachments/assets/31bfb709-9e6b-4a2c-bf3b-1054e0a79f5a

> [!NOTE]
> Miru provides **context for research, not investment advice**. It never says buy, sell or hold,
> never sets price targets, and never sizes a position. Every answer carries its sources and how
> current the data is.

## ✨ Why Miru

The market never stops talking: headlines, price moves, fund flows, earnings, rumours. Miru takes
one question in plain language, gathers the data that bears on it, and answers **summary first,
then the evidence**, with every figure traced to its source.

The name comes from 見る (*miru*, "to see"). The product is about seeing the market clearly, not
about acting for you.

## 🧭 Features

| | |
|---|---|
| 💬 **Ask in plain language** | A chat agent that plans, calls data tools, and streams a sourced answer over SSE. You can stop, retry, edit and resend, and attach images or documents |
| 🌏 **Taiwan + US markets** | Prices, institutional flows, margin and short interest, fundamentals, ETF holdings and NAV, derivatives, corporate actions, news. The router covers 150 data capabilities across TW, US and global markets |
| 📊 **Stock & ETF pages** | Charts, technical indicators, a descriptive multi-factor 多空 (bull/bear) score, ETF comparison and overlap, valuation bands |
| 🔎 **Screener & themes** | A tag screener, market breadth and rankings. Concept themes (CoWoS, HBM, low-orbit satellites and more) with a theme page and a market tab, plus industry chains |
| 🕸️ **Supply chain & relationships** | A supply-chain graph where every supplier, customer and competitor link carries a confidence grade, an as-of date and clickable sources; a company's position in its chain (upstream, midstream, downstream) with the reasons, or "not yet classified" when the evidence is thin; and an entity graph of the technologies, materials and companies it is mentioned with |
| 👁️ **AI watch** | Five rule-based watch strategies (volatility squeeze, oversold bounce, volume-price divergence, chip accumulation, strong consolidation) screened at 08:00 and re-checked every 10 minutes during the session; the conditions you subscribe to alert you through your channels. Descriptive context, never a signal to trade |
| 🏛️ **US macro** | A yield curve with recession bands, plus inflation, jobs, credit, liquidity, growth, housing and money-supply indicators, and a release feed with the latest value against the previous one (needs a free `FRED_API_KEY`) |
| 🧩 **41 built-in skills** | Packaged analysis playbooks: stock reports (TW/US), ETF reports, morning brief, chip-flow review, earnings quality, macro dashboard and more |
| 🔬 **Deep research** (Pro) | Budgeted, multi-step web research inside a chat turn, with structural citations |
| 🔔 **Alerts & scheduled briefs** | Rules and briefings you authorise yourself, delivered to LINE, Telegram, email or web push |
| 🎭 **Miru's voice** | Four tone presets (Steady · Coach · Concise · Data). One engine with four ways of speaking, never four different advisors |
| 🧠 **Memory** | Session and long-term memory that you can review and edit in Settings |
| 🔌 **Bring your own model** | A provider-agnostic LLM adapter: Anthropic, OpenAI, Gemini, OpenRouter, or a local model through Ollama. BYOK is supported |
| 🛰️ **MCP server** | The same tools, exposed read-only over MCP (streamable HTTP), so other agents can use them. It works as a Claude connector: a claude.ai custom connector, the Messages API MCP connector, or Claude Code |
| 🌐 **Bilingual** | zh-TW first, English throughout (`web/messages/`) |

## 🏗️ How it works

```
Next.js web app  ──REST + SSE──▶  FastAPI  ◀──MCP──  other agents
                                     │
                    orchestrator · conversation · memory · skills
                                     │
                       agent runtime (loop · planner · guardrails)
                          │                         │
                    LLM adapter               tool registry (read-only)
                    (7 providers)                   │
                                        context pipeline (caveats, provenance)
                                                    │
                                 data-access router  (market, capability) → sources
                                                    │
                  FinMind · TWSE · TPEx · TAIFEX · MOPS · TDCC · yfinance · Finnhub · FRED · news
```

- **One-way dependencies.** Nothing lower imports anything higher, and only `sources/` touches the network.
- **Market data and app state are separate stores.** Market data lives in DuckDB/Parquet plus
  Redis: one ingest writer, many readers. App state lives in Postgres (or SQLite in dev).
- **Governance is code.** The non-trading output scan, sponsor-data masking, caveats and the
  untrusted-content rules all have governance tests (`pytest -m governance`). See
  [`docs/backend/modules/25-governance-enforcement.md`](docs/backend/modules/25-governance-enforcement.md).

## 🚀 Quick start

**You need:** Docker, Node 22+ with pnpm, and Python 3.13 with [uv](https://docs.astral.sh/uv/)
(uv is only needed outside Docker).

```bash
git clone https://github.com/iadr-dev/Miru.git
cd Miru

# 1. Secrets: copy the template and set at least one LLM key and FINMIND_API_KEY
cp backend/.env.example backend/.env

# 2. Backend stack: api :8000 · mcp :8100 · scheduler · ingest · Postgres · Redis
./scripts/dev-backend.sh --backfill   # --backfill seeds market data on a fresh volume (TW, then US)

# 3. Web app on http://localhost:3000
./scripts/dev-frontend.sh
```

<details>
<summary><b>Lighter API-only loop (SQLite, no Docker)</b></summary>

```bash
cd backend
uv sync
uv run uvicorn api.app:app --reload
```

This runs with an in-process event bus. There is no scheduler, ingest or MCP process.
</details>

<details>
<summary><b>Connect an MCP client</b> (Claude, Gemini CLI, Codex, Cursor, VS Code)</summary>

The stack serves a read-only MCP endpoint at `http://localhost:8100/mcp` (streamable HTTP), with a
liveness probe at `/healthz`. It runs as its own process, separate from the API. Clients sign in with
your Miru account: the client's "Sign in" / `/mcp` button opens a Miru consent page in your browser
(sign in if asked, then Allow or Deny), and your plan decides what the tools return. Set
`MCP_REQUIRE_AUTH=false` only for local development without sign-in. See
[docs/backend/mcp-claude-connector.md](docs/backend/mcp-claude-connector.md) for the details.

| Client | Add the server (`http://localhost:8100/mcp`) |
|---|---|
| Claude Code | `claude mcp add --transport http miru http://localhost:8100/mcp` |
| Gemini CLI | `gemini mcp add --transport http miru http://localhost:8100/mcp` |
| Codex CLI | `codex mcp add miru --url http://localhost:8100/mcp` |
| Cursor | `~/.cursor/mcp.json`: `{"mcpServers": {"miru": {"url": "http://localhost:8100/mcp"}}}` |
| VS Code | `.vscode/mcp.json`: `{"servers": {"miru": {"type": "http", "url": "http://localhost:8100/mcp"}}}` |
| claude.ai / Claude Desktop | Customize, Connectors, Add custom connector, choose **No sign in**. Needs a **public HTTPS** URL, not localhost |
| Claude API | `mcp_servers` with the public HTTPS URL (and `authorization_token` for a tier) |

The setup commands above come from each client's own documentation and are not yet run against the
live clients; ChatGPT connectors are not verified. Details, the HTTPS tunnel steps and the
Bearer-token option are in [`docs/backend/mcp-claude-connector.md`](docs/backend/mcp-claude-connector.md).
</details>

## ⚙️ Configuration

All settings live in `backend/.env`. [`backend/.env.example`](backend/.env.example) documents every
variable. The web app needs only `NEXT_PUBLIC_API_BASE` in `web/.env.local`.

| Group | Variables | Notes |
|---|---|---|
| LLM | `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `GEMINI_API_KEY` · `OPENROUTER_API_KEY` · `OLLAMA_BASE_URL` | Set the ones you use; `LLM_PROVIDER` / `MODEL_ROLE_*` route models by role |
| Market data | `FINMIND_API_KEY` | Whole-market ingest needs FinMind **Backer** or above. Sponsor-only datasets (for example institutional detail and ETF holdings) stop updating on a lower plan, and the app then shows the last day it has |
| Optional data | `FINNHUB_KEY` · `BRAVE_SEARCH_API_KEY` · `FRED_API_KEY` | US analyst estimates, web-search fallback, and the US macro dashboard (free key; without it the nightly job skips and the panels stay empty) |
| Reference data | `REFERENCE_DATA_DIR` | The supply-chain, concept-theme and entity data you maintain locally (default `./reference-data`, gitignored, mounted read-only in Docker). It is not part of this repository: without it those panels show honest empty states. Load it with `python -m analytics.supply_chain load`, then `positions`; `eval` scores positions against your own labelled set |
| Datastores | `DATABASE_URL` · `REDIS_URL` · `DUCKDB_PATH` · `PARQUET_ROOT` | SQLite + local paths work for dev |
| Sign-in | `GOOGLE_*` · `LINE_*` · `TELEGRAM_LOGIN_*` · `JWT_*` | Google, LINE Login, Log in with Telegram |
| Delivery | `LINE_MESSAGING_*` · `TELEGRAM_BOT_*` · `RESEND_*` · `VAPID_*` | Alert and brief channels (LINE, Telegram, email, web push) |
| Payments | `PADDLE_*` | Subscriptions; the webhook is the source of truth for entitlements |

> [!WARNING]
> `backend/.env` holds live secrets. It is gitignored. Never commit it, paste it or log it.

## 🗂️ Repository layout

```
Miru/
├─ backend/   FastAPI + MCP backend (Python 3.13, uv): src/<domain>/, tests/, docker-compose.yml
├─ web/       Next.js App Router frontend (pnpm): chat, market, screener, stock pages, settings
├─ docs/      the authoritative spec: architecture, API reference, 27 module specs, brand
├─ scripts/   dev-backend.sh · dev-frontend.sh
└─ reference-data/   your local supply-chain and theme data (gitignored, not shipped)
```

## 🧪 Development

```bash
# backend
cd backend
uv run ruff check --fix . && uv run ruff format .
uv run mypy src
uv run pytest -m "not network"

# web
cd web
pnpm lint && pnpm typecheck && pnpm test
```

The spec in [`docs/`](docs/) is authoritative: read the module spec before changing a layer.
Start with [`AGENTS.md`](AGENTS.md), [`docs/backend/architecture.md`](docs/backend/architecture.md)
and [`docs/backend/modules/README.md`](docs/backend/modules/README.md). The coding standards are in
[`backend/CLAUDE.md`](backend/CLAUDE.md) and [`web/OVERVIEW.md`](web/OVERVIEW.md).

## ⚠️ Disclaimer

Miru and this repository provide **analytical context for research and education only, not
investment advice**. Nothing here is a recommendation to buy, sell or hold any security. Data can
be delayed or incomplete; every answer states its sources and how current they are. Make your own
decisions, and consult a licensed professional where appropriate.

## 📄 License

[Apache License 2.0](LICENSE) © 2026 Ray Shen
