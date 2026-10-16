# Connecting Claude to the Miru MCP server

Miru's MCP server (`python -m mcp`, compose service `mcp`, port 8100, endpoint `/mcp`) speaks
**streamable HTTP** and works with all three ways Claude connects to a remote MCP server:

| Path | Who connects | URL must be | Auth options |
|------|--------------|-------------|--------------|
| **A. claude.ai / Claude Desktop custom connector** | Anthropic's cloud | public HTTPS | none (authless) today; OAuth sign-in is a follow-up (see [Known limits](#known-limits)) |
| **B. Messages API MCP connector** (`mcp_servers`) | Anthropic's API | public HTTPS | none, or `authorization_token` (sent as `Authorization: Bearer`) |
| **C. Claude Code** (`claude mcp add --transport http`) | your machine | any reachable URL, `http://localhost:8100/mcp` is fine | none, or `--header "Authorization: Bearer ..."` |

A and B call from Anthropic's servers, so the endpoint must be reachable from the public
internet (or allow-list Anthropic's published IP ranges in your firewall). C runs locally and does
not need a tunnel for a local stack.

What the server offers: five core tools listed directly (canonical context, quote, company profile,
source health, conversation package), plus `search_tools` / `execute_tool` for the long tail of
registry tools, two resources (`canonical://{symbol}`, `health://sources`), and server
`instructions` that tell the model what Miru covers, how masked teasers work, and that it gives
**data and analysis only, never buy/sell advice**. Tools are read-only (annotated), have titles,
and are listed in a stable (alphabetical) order. A tool added to the registry later appears
automatically; nothing in `backend/src/mcp` names individual tools.

## 1. Expose the service over HTTPS

The MCP port speaks plain HTTP and must be published through something that terminates TLS.

### Cloudflare Tunnel (no inbound ports)

```bash
# quick, throwaway hostname (testing)
cloudflared tunnel --url http://localhost:8100

# named tunnel on your own domain (stable URL)
cloudflared tunnel login
cloudflared tunnel create miru-mcp
cloudflared tunnel route dns miru-mcp mcp.example.com
cloudflared tunnel run --url http://localhost:8100 miru-mcp
```

Do not put Cloudflare Access (or any login page) in front of the path for path A/B: Anthropic's
connector cannot complete an interactive login that is not OAuth-spec compliant.

### Reverse proxy (Caddy example)

```
mcp.example.com {
    reverse_proxy localhost:8100 {
        flush_interval -1        # do not buffer streamed (SSE) responses
    }
}
```

nginx: `proxy_pass http://127.0.0.1:8100; proxy_buffering off; proxy_http_version 1.1;
proxy_read_timeout 300s;` and forward `Host` / `X-Forwarded-For`.

### Settings (backend `.env`, all non-secret)

```
MCP_PUBLIC_BASE_URL=https://mcp.example.com      # no path, no trailing slash
TRUSTED_PROXY_COUNT=1                            # tunnel/proxy hops, so rate limits key on the real client
```

`MCP_PUBLIC_BASE_URL` adds `mcp.example.com` to the Host allow-list and `https://mcp.example.com`
to the Origin allow-list (the DNS-rebinding protection the MCP spec requires). Extra hostnames go
in `MCP_ALLOWED_HOSTS` (comma separated; `localhost` / `127.0.0.1` always stay allowed, so the
container health check keeps working). A request whose `Host` is not allowed gets HTTP 421 with a
JSON body naming `MCP_PUBLIC_BASE_URL` / `MCP_ALLOWED_HOSTS`; a rejected `Origin` gets 403 naming
`MCP_ALLOWED_ORIGINS`. Server-side callers (A, B) send no `Origin`.

Check it from outside:

```bash
curl -s https://mcp.example.com/healthz
# {"status":"ok","service":"miru","version":"0.1.0","uptime_seconds":42,
#  "tools":{"total":97,"listed":7},"auth":{"anonymous":true,"bearer":true,"oauth_metadata":false}}
```

`/healthz` carries no secrets or hostnames. Both `https://mcp.example.com/mcp` and
`https://mcp.example.com/mcp/` work (no redirect).

## 2. Connect Claude

### A. claude.ai / Claude Desktop custom connector

1. Settings, Connectors, **Add custom connector** (on Team/Enterprise an owner adds it under
   Organization settings, Connectors first).
2. Name: `Miru`. URL: `https://mcp.example.com/mcp`. Leave the OAuth client fields empty.
3. Add, then enable the connector in a chat (the "+" / tools menu). Claude lists the tools and asks
   permission per tool; they are all marked read-only.

Anonymous access is served as the **Free tier** (public data; sponsor-tier tools return a masked
teaser that Claude explains). Per the MCP authorization spec a client probes the OAuth discovery paths; with no
authorization server configured they answer 404 and an authless server is used as-is (not
verified against the live claude.ai service, which this environment cannot reach).

### B. Messages API MCP connector

Beta header `anthropic-beta: mcp-client-2025-11-20`. The connector supports tool calls only
(Miru's resources are not used by it) and requires an `https://` URL.

```json
{
  "model": "claude-sonnet-4-5",
  "max_tokens": 1024,
  "messages": [{"role": "user", "content": "Summarize 2330's latest fundamentals and data freshness."}],
  "mcp_servers": [
    {
      "type": "url",
      "url": "https://mcp.example.com/mcp",
      "name": "miru",
      "authorization_token": "<optional: a Miru access token>"
    }
  ],
  "tools": [{"type": "mcp_toolset", "mcp_server_name": "miru"}]
}
```

```bash
curl https://api.anthropic.com/v1/messages \
  -H "content-type: application/json" -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" -H "anthropic-beta: mcp-client-2025-11-20" \
  -d @request.json
```

Omit `authorization_token` for anonymous (Free). Use the model id current for your account. To
restrict the tools Claude may call, use the toolset's `default_config` / `configs` allowlist
(see Anthropic's MCP connector page).

### C. Claude Code

```bash
# anonymous (Free tier)
claude mcp add --transport http miru https://mcp.example.com/mcp

# local stack, no tunnel
claude mcp add --transport http miru http://localhost:8100/mcp

# signed in: a Miru access token as a Bearer header (see Auth below)
claude mcp add --transport http miru https://mcp.example.com/mcp \
  --header "Authorization: Bearer $MIRU_ACCESS_TOKEN"

# or, equivalently, the x-api-key header
claude mcp add --transport http miru https://mcp.example.com/mcp \
  --header "x-api-key: $MIRU_ACCESS_TOKEN"
```

Verify with `claude mcp list` and `/mcp` inside Claude Code. Claude Code caps a tool result at
about 25,000 tokens by default (`MAX_MCP_OUTPUT_TOKENS`); Miru marks its tools with
`anthropic/maxResultSizeChars` so larger structured results are allowed where the client honors it.

## 3. Authentication

| Credential | Result |
|------------|--------|
| none | anonymous, `Tier.FREE`, masked teasers for sponsor data (the default; the authless connector) |
| `Authorization: Bearer <Miru access JWT>` | that user's tier (Free / Premium / Pro), sponsor data unmasked for paid tiers |
| `x-api-key: <Miru access JWT>` | same as Bearer (for clients that can only set a custom header) |
| `x-api-key: <anything else>` | unrecognized, treated as anonymous (never an error) |
| `Authorization: Bearer <invalid / expired / forged>` | **HTTP 401** |

The token is the application's own short-lived access JWT (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`,
default 15 min), the same one the web app uses; the tier is read from the database on every call,
so a plan change applies on the next request, and a payment lapse past grace drops the tier exactly
as in the app. Because the lifetime is short, a pasted token is useful for testing and for scripted
use that can refresh it; long-lived sign-in needs the OAuth follow-up below.

A 401 carries the spec's challenge:

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="miru-mcp", error="invalid_token",
  error_description="The access token is invalid or has expired; obtain a fresh one.",
  resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
```

`resource_metadata` appears only when an authorization server is configured:

```
MCP_PUBLIC_BASE_URL=https://mcp.example.com
MCP_AUTHORIZATION_SERVER_URL=https://auth.example.com
```

With both set the server serves RFC 9728 metadata at
`/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/mcp`
(`resource` = `https://mcp.example.com/mcp`, `authorization_servers` = your URL,
`bearer_methods_supported` = `["header"]`); otherwise both paths return 404. Miru ships **no OAuth
authorization server**: set this only when an RFC 8414 server (with dynamic client registration or
client-id metadata) that issues Miru-valid tokens exists.

## 4. Safety limits (all configurable, see `.env.example`)

- Rate limit on `/mcp`: `MCP_RATE_LIMIT_ANONYMOUS_PER_MINUTE` (per client address) and
  `MCP_RATE_LIMIT_SIGNED_IN_PER_MINUTE` (per user id). Uses the same limiter backend as the API
  (`RATE_LIMITER_BACKEND=redis` shares counts across replicas). Exceeded: HTTP 429 + `Retry-After`.
- `MCP_MAX_BODY_BYTES` (default 1 MiB): HTTP 413.
- `MCP_MAX_CONCURRENT_REQUESTS` (default 64): HTTP 503 + `Retry-After: 1`.
- `MCP_STATELESS_HTTP=true` (default): no `Mcp-Session-Id`; every request stands alone, so a load
  balancer, restart or one-request-per-connection client (the Messages API connector) just works.
  Miru sends no server-initiated messages, so nothing is lost. Set `false` for session mode.
- Tool and resource reads are bounded single reads; `isError` results carry a recovery hint.

## 5. Client requirements checked

Verified with the official `mcp` SDK client and `fastmcp.Client` against the real server over real
HTTP (`backend/tests/mcp/test_connector_*.py`): protocol versions 2024-11-05, 2025-03-26,
2025-06-18 and 2025-11-25 initialize (an unknown version is answered with the server's latest); an
unsupported `MCP-Protocol-Version` header is a 400; every listed tool name matches
`^[a-zA-Z0-9_-]{1,64}$`; every schema is valid JSON Schema 2020-12 with no dangling `$ref`; the
`tools/list` payload stays under 40 KB; `outputSchema` is present (sponsor tools admit the masked
teaser); unknown tool or bad arguments produce `isError` with a hint; a Bearer token round-trips to
the right tier; `/mcp` and `/mcp/` both answer directly.

## Known limits

- **Full OAuth 2.1 sign-in for claude.ai is not available yet.** claude.ai's built-in sign-in
  needs an authorization server (authorization endpoint, token endpoint with PKCE, RFC 8414
  metadata, dynamic client registration or client-id metadata documents). Miru has none; the
  resource-server half (401 challenge, RFC 9728 metadata, token validation) is in place. Until
  then claude.ai connects authless (Free tier) and paid-tier access over MCP is via Bearer /
  `x-api-key` in Claude Code and the Messages API. Follow-up: build or front an authorization
  server and issue tokens with an `aud` of the MCP URL (the spec requires audience validation; the
  app's access JWT currently carries no audience claim).
- No API-key store exists; `x-api-key` accepts the same short-lived access JWT.
- The Messages API connector uses tools only. Resources are visible to claude.ai and Claude Code
  but not to the API connector; every datum is therefore also reachable as a tool.
- Legacy SSE-only transport is not served (`/sse`); current Claude clients use streamable HTTP and
  fall back automatically.
- Large-tail tools are reached in two steps (`search_tools`, then `execute_tool`) by design, to
  keep the model's context small; `search_tools` returns each match's argument schema.
- Behind Cloudflare, free/proxied plans cap request duration (about 100 s); tool calls are well
  below that. Anthropic's source IP ranges are published by Anthropic if you must allow-list them.
