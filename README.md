# Miru

**Your AI assistant for Taiwan & US stocks and ETFs: it explains the market and shows its sources.**

[![Backend CI](https://github.com/iadr-dev/Miru/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/iadr-dev/Miru/actions/workflows/backend-ci.yml)
[![Frontend CI](https://github.com/iadr-dev/Miru/actions/workflows/frontend-ci.yml/badge.svg)](https://github.com/iadr-dev/Miru/actions/workflows/frontend-ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-f2873a.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)
![Node](https://img.shields.io/badge/node-22+-339933?logo=node.js&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-SSE-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-App%20Router-000000?logo=next.js&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-read--only-6E56CF)

**English** | [繁體中文](README_TW.md)

</div>

<!-- Promo video -->
https://github.com/user-attachments/assets/a04a3593-ed1a-4fb3-b1a0-c2163ea41b9d

<div align="center">

<img src="web/public/logo.png" alt="Miru" width="120" />


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
| 🌏 **Taiwan + US markets** | Prices, institutional flows, margin and short interest, fundamentals, ETF holdings and NAV, derivatives, corporate actions, news. The router covers 146 data capabilities across TW, US and global markets |
| 📊 **Stock & ETF pages** | Charts, technical indicators, a descriptive multi-factor 多空 (bull/bear) score, ETF comparison and overlap, valuation bands |
| 🔎 **Screener & themes** | A tag screener, theme and supply-chain layers, market breadth and rankings |
| 🧩 **41 built-in skills** | Packaged analysis playbooks: stock reports (TW/US), ETF reports, morning brief, chip-flow review, earnings quality, macro dashboard and more |
| 🔬 **Deep research** (Pro) | Budgeted, multi-step web research inside a chat turn, with structural citations |
| 🔔 **Alerts & scheduled briefs** | Rules and briefings you authorise yourself, delivered to LINE, Telegram, email or web push |
| 🎭 **Miru's voice** | Four tone presets (Steady · Coach · Concise · Data). One engine with four ways of speaking, never four different advisors |
| 🧠 **Memory** | Session and long-term memory that you can review and edit in Settings |
| 🔌 **Bring your own model** | A provider-agnostic LLM adapter: Anthropic, OpenAI, Gemini, Groq, Cerebras, OpenRouter, or a local model through Ollama. BYOK is supported |
| 🛰️ **MCP server** | The same tools, exposed read-only over MCP (streamable HTTP), so other agents can use them |
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
<summary><b>Connect an MCP client</b></summary>

The stack serves a read-only MCP endpoint at `http://localhost:8100/mcp` (streamable HTTP), with a
liveness probe at `/healthz`. It runs as its own process, separate from the API.
</details>

## ⚙️ Configuration

All settings live in `backend/.env`. [`backend/.env.example`](backend/.env.example) documents every
variable. The web app needs only `NEXT_PUBLIC_API_BASE` in `web/.env.local`.

| Group | Variables | Notes |
|---|---|---|
| LLM | `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `GEMINI_API_KEY` · `GROQ_API_KEY` · `CEREBRAS_API_KEY` · `OPENROUTER_API_KEY` · `OLLAMA_BASE_URL` | Set the ones you use; `LLM_PROVIDER` / `MODEL_ROLE_*` route models by role |
| Market data | `FINMIND_API_KEY` | Whole-market ingest needs FinMind **Backer** or above |
| Optional data | `FINNHUB_KEY` · `ALPHA_VANTAGE_API_KEY` · `FRED_KEY` · `FMP_API_KEY` · `BRAVE_SEARCH_API_KEY` | US, macro, analyst estimates, web-search fallback |
| Datastores | `DATABASE_URL` · `REDIS_URL` · `DUCKDB_PATH` · `PARQUET_ROOT` | SQLite + local paths work for dev |
| Sign-in | `GOOGLE_*` · `LINE_*` · `TELEGRAM_LOGIN_*` · `JWT_*` | Google, LINE Login, Log in with Telegram |
| Delivery | `LINE_MESSAGING_*` · `TELEGRAM_BOT_*` · `RESEND_*` · `APNS_*` · `FCM_*` | Alert and brief channels |
| Payments | `PADDLE_*` | Subscriptions; the webhook is the source of truth for entitlements |

> [!WARNING]
> `backend/.env` holds live secrets. It is gitignored. Never commit it, paste it or log it.

## 🗂️ Repository layout

```
Miru/
├─ backend/   FastAPI + MCP backend (Python 3.13, uv): src/<domain>/, tests/, docker-compose.yml
├─ web/       Next.js App Router frontend (pnpm): chat, market, screener, stock pages, settings
├─ docs/      the authoritative spec: architecture, API reference, 26 module specs, brand
├─ promo/     the Remotion promo film (zh-TW + en)
└─ scripts/   dev-backend.sh · dev-frontend.sh
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

[MIT](LICENSE) © 2026 Ray Shen
