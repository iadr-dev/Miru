# Module specifications

**Start here.** [`../architecture.md`](../architecture.md) is the system blueprint (layering, the
dependency rule, repo layout, the anatomy of a turn); [`../api-reference.md`](../api-reference.md) lists
every endpoint; coding rules and the toolchain are in
[`backend/CLAUDE.md`](../../../backend/CLAUDE.md). Then read the module for the layer you are touching.

Each document specifies one layer of the framework: its responsibilities, public
interface, dependencies, and governance obligations. Every module inherits the
governance rules (governance-as-code in [`backend/CLAUDE.md`](../../../backend/CLAUDE.md) §4, security in §8, and
[`AGENTS.md`](../../../AGENTS.md)). Endpoints are in [../api-reference.md](../api-reference.md); the
database tables are the SQLAlchemy models in `backend/src/db/models/`.

| Doc | Layer |
|-----|-------|
| [01-sources.md](01-sources.md) | Data-source adapters (the only network I/O) |
| [02-packages.md](02-packages.md) | Context pipeline (canonical / observation / conversation / source-health / builder) |
| [03-tools.md](03-tools.md) | Tool registry — the agent's action surface |
| [04-llm-adapter.md](04-llm-adapter.md) | Provider-agnostic LLM adapter + role routing |
| [05-agent-runtime.md](05-agent-runtime.md) | Loop, planner, context window, guardrails, journal |
| [06-interfaces.md](06-interfaces.md) | FastAPI API + MCP server (FastMCP, own tools); connecting Claude: [../mcp-claude-connector.md](../mcp-claude-connector.md) |
| [07-conversation-streaming.md](07-conversation-streaming.md) | Threads, typed SSE events, turn lifecycle, sponsor-data masking |
| [08-skills.md](08-skills.md) | Skill system — packaged analysis playbooks |
| [09-memory.md](09-memory.md) | Working / session / long-term memory |
| [10-events-hooks.md](10-events-hooks.md) | Event bus, hooks, usage accounting, rate limiting |
| [11-orchestration.md](11-orchestration.md) | Intent classifier + single/subagent/team frameworks, model routing |
| [12-multi-user-tiers.md](12-multi-user-tiers.md) | Auth, users, tiers (free/premium/pro), entitlements, quotas, BYOK |
| [13-chat-interaction-features.md](13-chat-interaction-features.md) | Feedback, retry/regenerate, edit-resend, stop, voice, history management |
| [14-scheduler-alerts.md](14-scheduler-alerts.md) | User-authorized scheduled jobs, alerts, delivery (governed carve-out) |
| [15-payments-paddle.md](15-payments-paddle.md) | Paddle subscriptions, BYOK-priced variants, checkout + webhooks → entitlement |
| [16-infrastructure.md](16-infrastructure.md) | Deployment topology, cross-process events (Redis), market-data cache tier (DuckDB/Redis), scheduler HA, observability, secrets, migrations |
| [17-devops-operations.md](17-devops-operations.md) | CI/CD, containerization, environments, probes, graceful shutdown, DR, cost ops, PII residency, runbooks, feature flags |
| [18-calendar-events.md](18-calendar-events.md) | Forward events calendar: rule engine + feeds + gap scrapers, event state model (除權息/配息/股東會/法說會/財報/結算/rebalance/macro) |
| [19-data-access-router.md](19-data-access-router.md) | Multi-market data layer: capabilities, Market/Symbol, per-(market,capability) priority-fallback source router (config-driven) |
| [20-analytics-scoring.md](20-analytics-scoring.md) | Derived analytics: indicators, 5-factor 多空 score, tag screener, theme layers |
| [21-pet-gamification.md](21-pet-gamification.md) | Cosmetic pet/gamification: gacha/pity, XP/leveling, reactive chat companion (decoupled from tiers/analysis) |
| [22-data-ingest.md](22-data-ingest.md) | Data ingest/ETL: cadence table, whole-market-by-date writers, idempotent atomic-swap Parquet, backfill/gap-fill (platform-ingest carve-out) |
| [23-agent-personality.md](23-agent-personality.md) | Agent voice: user-selectable curated personality presets (穩健/白話/簡潔/數據派), style-only, subordinate to guardrails |
| [24-deep-research.md](24-deep-research.md) | Pro-only budgeted, cited multi-step web research inside a chat turn (structural citations, untrusted pages, SSRF-safe fetch, parallel sub-questions) |
| [25-governance-enforcement.md](25-governance-enforcement.md) | Where each governance rule is enforced in code and which tests pin it: non-trading output scan, whole-market-scan rejection, sponsor masking, caveats, untrusted content, secrets |
| [26-attachments.md](26-attachments.md) | Chat attachments: request limits, image vs document handling, untrusted extracted text, persisted `attachment` blocks, use in deep research |
