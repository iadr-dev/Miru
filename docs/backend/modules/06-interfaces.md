# Module: Interfaces — FastAPI + MCP (`backend/src/api/`, `backend/src/mcp/`)

Two interfaces share the same runtime, tools, and guardrails. The FastAPI surface is the
full product API (chat, history, accounts, scheduler…); the MCP server is a **read-only
projection** of our own tool registry for external agents (Claude / ChatGPT / Claude Code).
The complete endpoint list is in [../api-reference.md](../api-reference.md).

## FastAPI (`backend/src/api/`)

- App factory in `api/app.py`; **no startup network calls**. Routers under `api/routers/`:
  `agent`, `threads`, `context`, `sources`, `health`, `accounts`, `feedback`, `memory`,
  `usage`, `schedules`, `alerts`, `voice`, `analytics` (+ screener/screens), `watchlists`,
  `search`, `market` (+ `derivatives`, `macro`), `quote` (+ `updates` stream), `pets`, `billing`,
  `personality`, `notifications` (+ `devices`). **The authoritative endpoint list is
  [api-reference.md](../api-reference.md)** — this module owns cross-cutting interface concerns
  (exposure model, SSE, MCP projection), not the per-router enumeration.
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

### Exposure model — immediate tools + search/execute (~50 tools ≫ context budget)
With ~50 registry tools, exposing every schema over MCP floods the client's context (each
schema is per-turn tokens; the ~30-tool ceiling is exceeded). So MCP mirrors our internal
deferred-loading ([03-tools.md](03-tools.md)):
- **Immediate:** the **canonical core set defined in [03-tools.md](03-tools.md) §deferred-loading**
  (`get_canonical_context`, `get_market_context`, `build_conversation_package`, `get_source_health`,
  `get_source_matrix`) as dedicated MCP tools — the MCP projection omits the agent-loop-only
  `get_company_profile`/news-price basics (they load via `execute_tool`). Same core, deliberate projection.
- **Deferred long tail** (per-signal chip/ETF/derivatives/MOPS/news tools) exposed via a
  **`search_tools` / `execute_tool`** pair — `search_tools(intent)` returns matching tool ids +
  schemas; `execute_tool(id, args)` runs one. Keeps the MCP client's context lean.

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
| `get_market_context()` | public | index / breadth canonical |
| `get_source_matrix()` | public | adapter matrix |
| `build_conversation_package(symbols, window?)` | public | `context/conversation.py` |
| `get_source_health()` | public | `context/source_health.py` |
| `search_tools(intent)` / `execute_tool(id, args)` | public | deferred long-tail access |
| (via `execute_tool`) `get_chip_flows`, `get_government_bank_flows`, `get_realtime_snapshot`, … | sponsor | FinMind Sponsor (teaser if unentitled) |

### Exposed resources
- `canonical://{symbol}` — read the canonical package as an MCP resource.
- `health://sources` — source-health snapshot.

### Definition pattern (registry → FastMCP)

```python
# mcp/server.py — immediate core registered directly; long tail via search/execute
mcp = FastMCP("ai-tw-stock", transport="streamable-http")

for tool in registry.immediate_tools():             # small core only
    @mcp.tool(
        name=tool.name, title=tool.title, description=tool.description,   # declarative only
        annotations={"readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": True},
    )
    def _handler(args: tool.input_model, _tool=tool) -> tool.output_model:
        r = _tool.run(args, ToolContext(user_id=mcp_principal()))  # sponsor → masked teaser
        return r  # FastMCP emits structuredContent + JSON text fallback; caps large results

# deferred long tail reachable via search_tools(intent) → execute_tool(id, args)
```

## MCP principal & entitlement

`mcp_principal()` resolves the caller to an entitlement, and the mechanism is explicit (not
assumed):
- **Default = anonymous → `public` tier.** With no credential, only `public` data is served;
  every `sponsor` tool returns the masked teaser. This is the safe default and needs no auth.
- **Optional authenticated MCP:** a caller may present an **API key** (issued per user in
  account settings) → resolves to that `users` row + tier, so `sponsor` tools unmask exactly
  as on REST. Per-user isolation applies identically over MCP (same store wrapper / RLS).
- No other principal path exists; MCP never elevates beyond the resolved user's tier, and
  Tier-2/approval concepts do not exist (removed, [adr/0010](../adr/0010-remove-safety-tiers.md)).

## Governance obligations

- Read-only: neither interface mutates the canonical package or persists credentials.
- Both route every action through the tool registry + guardrails; no interface reaches
  `context/` or `sources/` directly.
- `sponsor` data is entitlement-gated (masked teaser) identically on API and MCP; trading-
  signal tools are excluded by construction.
