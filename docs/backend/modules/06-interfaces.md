# Module: Interfaces — FastAPI + MCP (`backend/src/api/`, `backend/src/mcp/`)

Two interfaces share the same runtime, tools, and guardrails. The FastAPI surface is the
full product API (chat, history, accounts, scheduler…); the MCP server is a **read-only
projection** of our own tool registry for external agents (Claude / ChatGPT / Claude Code).
The complete endpoint list is in [../api-reference.md](../api-reference.md).

## FastAPI (`backend/src/api/`)

- App factory in `api/app.py`; **no startup network calls**. Routers under `api/routers/`
  (file → mounted concerns): `agent.py` (`/agent/query`), `threads.py` + `threads_actions.py`
  (`/threads`, fork/steer/regenerate/edit/feedback/export), `context.py` (canonical/conversation
  reads, `/search`, `/quote/stream`, `/updates/stream`), `market.py` (`/health`, `/sources`,
  `/quote`), `market_rest/` (`/market/*`, `/derivatives/*`, `/macro*`), `fund_data.py`
  (`/funds/*`), `themes.py` (`/themes/*`, `/symbols/{symbol}/themes`), `analytics.py`
  (`/analytics/{symbol}/score|summary|trend-score`, `/analytics/rankings`, the
  `/analytics/screener/*` routes incl. saved screens), `auth.py` + `accounts_extra.py`
  (OAuth, `/me`, personality presets, usage quota, BYOK keys), `billing.py` (Paddle), `memory.py`
  (long-term memory + the relational watchlist rail), `scheduler/` (`/schedules`, `/alerts`,
  `/notifications`, `/me/alert-preferences`, `/me/devices`), `voice.py` (transcribe/speak),
  `pets.py`, `probes.py` (`/livez`/`/readyz`/`/startupz`, mounted unprefixed). **The authoritative
  endpoint list is [api-reference.md](../api-reference.md)** — this module owns cross-cutting
  interface concerns (exposure model, SSE, MCP projection), not the per-router enumeration.
- Core chat entrypoint `POST /agent/query` streams typed SSE events
  ([07-conversation-streaming.md](07-conversation-streaming.md)); no approval handshake —
  `sponsor` data simply returns a masked teaser for unentitled users.
- Auth, tiers, and per-user scoping per [12-multi-user-tiers.md](12-multi-user-tiers.md).

## MCP server (`backend/src/mcp/`) — our own tools

**Framework:** FastMCP. **Transport:** **remote streamable-HTTP** (hosted, multi-user; the
recommended model — external agents add a URL, no install). Not local stdio.
**Schemas:** Pydantic v2 (same models the tool registry uses, so inputs/outputs are
structured — no ad-hoc dicts). We define our **own** MCP tools mapped 1:1 from the registry —
we do **not** adopt any third-party tool set (e.g. `twstock`-based servers): our `sources/`
layer is richer and governed, and we exclude trading-signal tools ("four buy/sell points") by
policy.

**Production entry point: `python -m mcp` (`backend/src/mcp/__main__.py`).** A standalone
process, never wired into the main API process — the same architectural shape as
`python -m scheduler` and `python -m ingest`, one more role in `docker-entrypoint.sh`'s
`SERVICE_ROLE` dispatch. `mcp.composition.build_mcp_http_app` assembles the real tool registry
via the same `orchestrator.composition` composition-root functions the API app uses
(`build_session_factory`/`build_tool_registry`), builds the `FastMCP` server
(`mcp.app.build_mcp_server`), and hands `mcp.app.build_http_app`'s resulting ASGI app to
`uvicorn.run` — `build_http_app` returns a runnable ASGI app for a caller to serve; it has no
`.run()` of its own. Host/port/allowed-origins/allowed-hosts are config (`MCP_HOST`/`MCP_PORT`/
`MCP_ALLOWED_ORIGINS`/`MCP_ALLOWED_HOSTS`, see `.env.example`). Principal resolution
(`mcp.accounts_resolver.AccountsPrincipalResolver`) maps the application's own signed access JWT -
sent as `Authorization: Bearer` or `x-api-key` - to the real user's tier (so sponsor data and masked
teasers match the app); no credential stays anonymous/`Tier.FREE`. `mcp.gateway.McpGateway` is the
HTTP edge (401 + `WWW-Authenticate` for a bad Bearer token, per-principal rate limit, body and
concurrency caps, `/mcp/` normalization, explained Host/Origin rejections), `mcp.discovery` serves
RFC 9728 protected-resource metadata when an authorization server is configured, and the server
sends `instructions` (scope, tier semantics, the non-trading rule). How to connect Claude.ai,
the Messages API and Claude Code: [mcp-claude-connector.md](../mcp-claude-connector.md).

### Exposure model — immediate tools + search/execute (100–107 tools ≫ context budget)
With 100–107 registry tools (5 immediate, the rest deferred — see [03-tools.md](03-tools.md) for
the current authoritative count and the exact conditions behind the range), exposing every schema
over MCP floods the client's context. So MCP mirrors our internal deferred-loading
([03-tools.md](03-tools.md)):
- **Immediate:** the **canonical core set defined in [03-tools.md](03-tools.md) §deferred-loading**
  — the real, wired-up core is `get_canonical_context`, `build_conversation_package`,
  `get_source_health`, `get_company_profile`, `get_price_quote` (5 tools) — as dedicated MCP
  tools. Same core the internal agent loop sends every turn, projected identically over MCP.
- **Deferred long tail** (per-signal chip/ETF/derivatives/MOPS/analyst/screener/score tools)
  exposed via a **`search_tools` / `execute_tool`** pair — `search_tools(intent)` returns
  matching tool ids + schemas; `execute_tool(id, args)` runs one. Keeps the MCP client's context
  lean. A dedicated test (`tests/mcp/test_full_registry_coverage.py`) asserts every real tool in
  the built registry is reachable this way — either as a dedicated immediate tool or via
  `execute_tool` — closing the "only spot-checked, never asserted at 100%" gap.

All registry tools are read-only, so all are reachable over MCP (directly or via
`execute_tool`). `sponsor` (paid FinMind) tools return a **masked teaser** unless the MCP
principal is entitled — same rule as the API.

### Annotations, output & errors (Anthropic Directory conformance)
- **Every MCP tool emits annotations** — since all are read-only: `readOnlyHint: true`,
  `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: true` (they read external
  data), plus a human `title`. (Read-only-only means hosts may auto-approve — a real UX win.)
- **Structured output:** return Pydantic `output_model` as `structuredContent` **and** a JSON
  text fallback (not all hosts read `structuredContent`).
- **Large results:** obey the tool output caps ([03-tools.md](03-tools.md)) — truncate with a
  "showing N of M" note or return a `resource_link`; never dump megabytes.
- **Errors** return MCP `isError` results with a recovery hint (e.g. "unknown symbol — use
  `get_source_matrix`"), never raised exceptions that kill the transport.
- **Descriptions are declarative only** — they describe what the tool does/returns/doesn't do;
  they must **not** instruct the model ("always call X first") — that is rejected as prompt
  injection at Directory review, and our `Tool.description` is shared with the internal loop.

### Exposed tools

| MCP tool | data_class | Backed by |
|----------|-----------|-----------|
| `get_canonical_context(symbols, fields?)` | public | `context/canonical.py` |
| `build_conversation_package(symbols, window?)` | public | `context/conversation.py` |
| `get_source_health()` | public | `context/source_health.py` |
| `get_company_profile(id, market)` | public | `tools/market/` |
| `get_price_quote(id, market)` | public | `tools/market/` |
| `search_tools(intent)` / `execute_tool(id, args)` | public | deferred long-tail access (every non-immediate tool) |
| (via `execute_tool`) `get_chip_flows`, `get_government_bank_flows`, `get_realtime_snapshot`, … | sponsor | FinMind Sponsor (teaser if unentitled) |

Note the real core (above) is narrower than an earlier design draft that named a
`get_market_context`/`get_source_matrix` pair in the immediate set — no real tool named
`get_market_context` exists (see [03-tools.md](03-tools.md)), and `get_source_matrix` is real but
deferred, not immediate. This table follows the real, wired registry, not the original design.

### Exposed resources
- `canonical://{symbol}` — read the canonical package as an MCP resource.
- `health://sources` — source-health snapshot.

### Definition pattern (registry → FastMCP)

The real code lives in `mcp/app.py::build_mcp_server` (registration) and
`mcp/composition.py::build_mcp_http_app` (the config-driven composition root `mcp/__main__.py`
invokes) — this is the shape, not a literal excerpt:

```python
# mcp/app.py — immediate core registered directly; long tail via search/execute
mcp = FastMCP(name)

for tool in registry.immediate_tools():             # small core only (5 real tools)
    fm_tool = _RegistryBackedTool(
        name=tool.name, description=tool.description,
        parameters=tool.input_model.model_json_schema(),
        annotations=_READ_ONLY_ANNOTATIONS,          # readOnlyHint/destructiveHint/... always set
        domain_tool=tool,
    )
    mcp.add_tool(fm_tool)  # .run() always calls tools.base.dispatch_tool — sponsor → masked teaser

# deferred long tail reachable via search_tools(intent) → execute_tool(id, args)
```

## MCP principal & entitlement

`mcp.principal.resolve_principal(headers, resolver=...)` builds the per-call `ToolContext` from
the request's HTTP headers, and the mechanism is explicit (not assumed):
- **Default = anonymous → `Tier.FREE`.** With no `x-api-key` header (or an unrecognized key),
  every call gets a plain `ToolContext()` (anonymous, `Tier.FREE`); every `sponsor` tool returns
  the masked teaser. This is the safe default and needs no auth. Both `python -m mcp`'s
  composition root and `mcp.app.build_mcp_server`'s own default wire
  `principal_resolver=None`, which resolves to `mcp.principal.AnonymousOnlyResolver` — every
  caller today gets this default; no deployment currently wires anything else.
- **`PrincipalResolver` — a seam, not a built feature.** `mcp.principal.PrincipalResolver` is a
  `Protocol` (`async def resolve(api_key) -> tuple[user_id, Tier] | None`) a future
  accounts-backed lookup can implement and inject at the one `principal_resolver=` call site in
  `mcp/composition.py`. **No such resolver exists yet** — there is no API-key issuance flow in
  `accounts/` today (no `user_secrets`/API-key table, no `/me/api-keys` endpoint) — so an
  authenticated, tier-unmasking MCP call is not currently possible; every caller is anonymous.
- No other principal path exists; MCP never elevates beyond the resolved caller's tier, and
  there is no Tier-2/approval concept (safety tiers were removed).

## Governance obligations

- Read-only: neither interface mutates the canonical package or persists credentials.
- Both route every action through the tool registry + guardrails; no interface reaches
  `context/` or `sources/` directly.
- `sponsor` data is entitlement-gated (masked teaser) identically on API and MCP; trading-
  signal tools are excluded by construction.
