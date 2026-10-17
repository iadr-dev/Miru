# Connecting an MCP client to Miru ("sign in with your Miru account")

Miru's MCP server (`python -m mcp`, compose service `mcp`, port 8100, endpoint `/mcp`) speaks
**streamable HTTP** and is an **OAuth 2.1 resource server**. Every caller signs in with their Miru
account; the account's plan decides what they get:

| Plan | What the same tool call returns |
|------|---------------------------------|
| Free | public data; sponsor-tier data comes back as a masked teaser the model explains |
| Premium / Pro | the full data |

The plan is read from your account on **every request** (it is never stored in the token), so an
upgrade, a downgrade or a lapsed payment applies from the next call.

What the server offers: five core tools listed directly (canonical context, quote, company profile,
source health, conversation package), plus `search_tools` / `execute_tool` for the long tail of
registry tools, two resources (`canonical://{symbol}`, `health://sources`), and server
`instructions` that tell the model what Miru covers, how masked teasers work, and that it gives
**data and analysis only, never buy/sell advice**. Tools are read-only (annotated), have titles,
and are listed in a stable (alphabetical) order.

## Release notes: what changes when you deploy this

- **`MCP_REQUIRE_AUTH` is `true` by default.** Every `/mcp` request needs an OAuth access token
  issued by this app; a request without one gets HTTP 401. A deployment that used the MCP server
  anonymously (the authless connector) must now sign in, or set `MCP_REQUIRE_AUTH=false`
  (development only).
- **The web app's own session JWT is refused** (it has no audience, so it was never issued for this
  server) unless `MCP_ACCEPT_APP_ACCESS_TOKEN=true`, a development flag. `Authorization: Bearer
  <session JWT>` and `x-api-key: <session JWT>` therefore stop working by default.
- `MCP_AUTHORIZATION_SERVER_URL` is gone; set `OAUTH_ISSUER` and `MCP_PUBLIC_BASE_URL` on both the
  `api` and the `mcp` service.
- New tables (`oauth_*`) come from `create_all` (additive mode adds them to an existing dev DB; the
  authorization-request and code tables gained columns during review, so reset a dev DB that
  already holds an earlier build of this branch).

## How sign-in works (what every client does)

Every major client starts sign-in only after the MCP server answers **HTTP 401**, then follows the
MCP authorization spec (revision 2026-07-28):

1. `POST /mcp` with no token -> `401` + `WWW-Authenticate: Bearer realm="miru-mcp",
   resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp",
   scope="mcp:read"`.
2. Client reads the protected-resource metadata (RFC 9728): `resource` = the MCP URL,
   `authorization_servers` = `[OAUTH_ISSUER]`.
3. Client reads the authorization-server metadata at `OAUTH_ISSUER/.well-known/oauth-authorization-server`
   (RFC 8414): `S256` PKCE only, public clients (no secret), `client_id_metadata_document_supported`.
4. Client identifies itself: a pre-registered id, a **Client ID Metadata Document** URL (Claude Code
   uses `https://claude.ai/oauth/claude-code-client-metadata`), or **dynamic registration**
   (`POST /oauth/register`, RFC 7591).
5. Client opens the browser at `GET /oauth/authorize` (PKCE challenge, `resource` = the MCP URL).
   The API validates the request and sends the browser to the web app's consent screen
   `<FRONTEND_BASE_URL>/<locale>/oauth/consent?request=<id>`.
6. **The consent screen is bound to your browser and account.** `/oauth/authorize` sets an httpOnly,
   `SameSite=Lax`, host-only cookie `oauth_req_<request id>` on the API origin (10 minutes); the
   web page must call `GET`/`POST /api/v1/oauth/consent/<id>` **with credentials** so the browser
   sends it (the same way it already sends the session cookie). Only the browser that started the
   flow and the first account that opens the screen can view or answer it (403
   `oauth_browser_mismatch` / `oauth_user_mismatch`). The screen shows the exact redirect URL the
   decision will use and an "unverified app" flag unless the client id is in
   `OAUTH_VERIFIED_CLIENT_IDS`.
   **You see Miru's page** (`/<locale>/oauth/consent` in the web app). If you are signed out it asks
   you to sign in with Google / LINE / Telegram as usual and returns you to the same screen. The screen names the app and where it will send you
   back, and asks **Allow / Deny**.
7. Allow -> the browser goes to the client's redirect URI with a one-time code (60 s) plus `state`
   and `iss`. The client calls `POST /oauth/token` (code + PKCE verifier + `resource`) and gets a
   10-minute access token and a 30-day refresh token. Refresh tokens **rotate** on every use; using
   an old one again revokes the whole connection.
8. The client retries `/mcp` with `Authorization: Bearer <access token>`. The server checks the
   signature, expiry, issuer, **audience (= the MCP URL)** and that the connection is not revoked,
   then reads your plan.

The token is only good for this server: it is bound to the MCP URL, and Miru never forwards it to
anything else.

## What you see in each client

> These steps come from each client's own documentation. They were **not** run against the live
> clients (this environment cannot reach them). What was run, end to end, is the protocol itself
> with the official `mcp` SDK client (see [What was verified](#what-was-verified)).

- **Claude Code**: `claude mcp add --transport http miru https://mcp.example.com/mcp`, then in a
  session run `/mcp`, pick **miru**, choose **Authenticate** (or run `claude mcp login miru`). The
  browser opens on Miru's consent screen; after **Allow** Claude Code shows the server as connected.
  Claude Code uses a loopback redirect (any port) and the metadata-document client id.
- **claude.ai / Claude Desktop**: Customize, Connectors, **Add custom connector**. Name `Miru`,
  URL `https://mcp.example.com/mcp`, leave the OAuth client fields empty (Miru registers the client
  itself), **Add**, then **Connect**. A browser window opens on Miru's consent screen. The callback
  is `https://claude.ai/api/mcp/auth_callback`. On Team/Enterprise an owner adds the connector
  first, then members connect individually.
- **Gemini CLI**: `gemini mcp add --transport http miru https://mcp.example.com/mcp`, then in a
  session `/mcp auth miru`. Gemini opens the browser and listens on
  `http://localhost:<random>/oauth/callback`.
- **Codex CLI**: `codex mcp add miru --url https://mcp.example.com/mcp`, then `codex mcp login
  miru` (configurable loopback callback).
- **Cursor**: add the server (`~/.cursor/mcp.json` -> `{"mcpServers": {"miru": {"url":
  "https://mcp.example.com/mcp"}}}`) and click **Connect**; callbacks are
  `https://www.cursor.com/agents/mcp/oauth/callback` and `http://localhost:8787/callback`.
- **VS Code / Windsurf**: add the HTTP server by URL; the client runs the same flow
  (VS Code uses loopback and `vscode://` redirects, which `OAUTH_EXTRA_REDIRECT_SCHEMES` allows).
- **Messages API MCP connector** (`mcp_servers`): the API does not run a browser flow. Complete the
  sign-in once with any of the clients above (or the SDK) and pass the resulting access token as
  `authorization_token`; it expires in 10 minutes, so refresh it with the refresh token. Header
  `anthropic-beta: mcp-client-2025-11-20`, tools only, `https://` URL required.

A client that skips the flow and sends a pasted token works too, as long as it is an MCP access
token; the web app's own session token is rejected (see [the dev flag](#development-without-sign-in)).

## 1. Expose the service over HTTPS

The MCP port speaks plain HTTP and must be published through something that terminates TLS.
Claude's cloud (claude.ai, Messages API) calls from the public internet, so the URL must be public
HTTPS (or allow-list Anthropic's published ranges, currently `160.79.104.0/21`). Claude Code and
the other CLIs run locally and can use `http://localhost:8100/mcp`.

### Cloudflare Tunnel (no inbound ports)

```bash
cloudflared tunnel --url http://localhost:8100            # quick, throwaway hostname
cloudflared tunnel login && cloudflared tunnel create miru-mcp
cloudflared tunnel route dns miru-mcp mcp.example.com
cloudflared tunnel run --url http://localhost:8100 miru-mcp  # stable URL on your domain
```

Do not put Cloudflare Access (or any login page) in front of `/mcp` or the `/.well-known/*` paths:
the clients cannot complete a login that is not the OAuth flow above. The API host (`OAUTH_ISSUER`)
must be reachable by the client too, and by the user's browser.

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

## 2. Settings (backend `.env`; none of these is a secret)

```
# The MCP server's own public URL (no path, no trailing slash). It becomes the token audience.
MCP_PUBLIC_BASE_URL=https://mcp.example.com
# The API service's public URL (scheme + host only): the token issuer and the authorization server.
OAUTH_ISSUER=https://app.example.com
# Where /oauth/authorize sends the browser: the web app, which hosts the consent screen.
FRONTEND_BASE_URL=https://app.example.com
TRUSTED_PROXY_COUNT=1          # tunnel/proxy hops, so rate limits key on the real client
```

`MCP_PUBLIC_BASE_URL` and `OAUTH_ISSUER` are read by **both** the `api` and the `mcp` service (they
share the compose environment): the API needs the MCP URL to bind tokens to it, the MCP server needs
the issuer to verify them. Both must be set, or MCP sign-in is unavailable (the metadata paths
answer 404 and `/oauth/*` answer 503 `server_error`). `JWT_SECRET_KEY` signs the tokens (a key
derived from it, separate from the web session's).

`MCP_PUBLIC_BASE_URL` also adds `mcp.example.com` to the Host allow-list and `https://mcp.example.com`
to the Origin allow-list (the DNS-rebinding protection the MCP spec requires). Extra hostnames go in
`MCP_ALLOWED_HOSTS`. A request whose `Host` is not allowed gets HTTP 421 naming the setting to
change; a rejected `Origin` gets 403 naming `MCP_ALLOWED_ORIGINS`.

Abuse bounds added in review: IPv6 callers are counted per /64; `OAUTH_REGISTER_GLOBAL_PER_HOUR`
caps registration across all callers; `OAUTH_MAX_CIMD_CLIENTS` caps metadata-document clients
separately; `OAUTH_CIMD_FETCH_PER_MINUTE` meters how many NEW metadata documents one address can make
the server fetch; `OAUTH_UNUSED_CLIENT_DAYS` (3) drops clients no user ever approved. Metadata
documents are fetched on port 443 only, without proxies or content-encoding, and failures are logged
server-side but answered with one generic message.

Optional tuning (defaults in `.env.example`): `OAUTH_ACCESS_TOKEN_MINUTES` (10),
`OAUTH_REFRESH_TOKEN_DAYS` (30, sliding), `OAUTH_EXTRA_REDIRECT_SCHEMES` (desktop clients that use a
custom scheme, default `vscode,vscode-insiders,cursor,windsurf`; https and `http://localhost` /
`127.0.0.1` / `[::1]` are always allowed), the metadata-document limits `OAUTH_CIMD_*`, and the
abuse bounds `OAUTH_REGISTER_PER_HOUR`, `OAUTH_TOKEN_PER_MINUTE`, `OAUTH_MAX_REGISTERED_CLIENTS`.

Check from outside:

```bash
curl -s https://mcp.example.com/healthz
# {"status":"ok","service":"miru","version":"0.1.0","uptime_seconds":42,
#  "tools":{"total":97,"listed":7},
#  "auth":{"required":true,"anonymous":false,"bearer":true,"oauth_metadata":true}}
curl -i -X POST https://mcp.example.com/mcp -H 'content-type: application/json' -d '{}'
# HTTP/1.1 401 ... WWW-Authenticate: Bearer realm="miru-mcp", resource_metadata="...", scope="mcp:read"
curl -s https://mcp.example.com/.well-known/oauth-protected-resource/mcp
curl -s https://app.example.com/.well-known/oauth-authorization-server
```

`/healthz` carries no secrets or hostnames. Both `.../mcp` and `.../mcp/` work (no redirect).

## 3. Revoking access

- **From the client**: "disconnect" / "log out" in the client discards its tokens. A client that
  calls `POST /oauth/revoke` (RFC 7009, form `token=<refresh or access token>&client_id=...`) ends
  the connection on the server: its refresh token stops working at once and its access token is
  refused by the MCP server on the next request (the grant is checked on each call).
- **Replay**: using a rotated-out refresh token, or an authorization code twice, revokes the whole
  connection.
- **Expiry**: an unused connection ends after 30 days without a refresh; access tokens last 10
  minutes.
- **Deleting the account** removes all of its connections.
- There is **no "connected apps" screen in the web app yet**; until one exists, a connection can
  only be ended by the client or the endpoint above.

## 4. Development without sign-in

For a local stack where you only want to try the tools:

```
MCP_REQUIRE_AUTH=false              # no credential -> anonymous Free tier, as before
MCP_ACCEPT_APP_ACCESS_TOKEN=true    # also accept the web app's own session JWT as a Bearer / x-api-key
```

Both are development flags. `MCP_ACCEPT_APP_ACCESS_TOKEN` exists because the web session token has
no audience (it was not issued for this server), so by default it is **refused** with 401. With
`MCP_REQUIRE_AUTH=false`, a presented-but-invalid Bearer token is still a 401.

## 5. Safety limits (all configurable, see `.env.example`)

- Rate limit on `/mcp`: `MCP_RATE_LIMIT_ANONYMOUS_PER_MINUTE` (per client address) and
  `MCP_RATE_LIMIT_SIGNED_IN_PER_MINUTE` (per user id), same limiter backend as the API.
  Exceeded: HTTP 429 + `Retry-After`.
- `MCP_MAX_BODY_BYTES` (default 1 MiB): HTTP 413. `MCP_MAX_CONCURRENT_REQUESTS` (default 64):
  HTTP 503 + `Retry-After: 1`.
- `MCP_STATELESS_HTTP=true` (default): no `Mcp-Session-Id`; every request stands alone.
- Authorization server: registration, token and revoke endpoints are rate limited per client
  address; metadata-document fetches refuse private, loopback, link-local and cloud-metadata
  addresses, never follow redirects, time out at 5 s and cap the body at 64 KB; authorization codes
  and refresh tokens are stored only as SHA-256 hashes; nothing secret is logged.

## What was verified

Run in this repository (`backend/tests`), over real HTTP, with no mocks of the server side:

- `tests/mcp/test_oauth_end_to_end.py` - the **official `mcp` SDK's `OAuthClientProvider`** drives
  the whole flow against the real API app and the real MCP app (two live servers sharing one
  database): 401 + discovery, dynamic registration, `/oauth/authorize`, the consent calls the web
  app makes (with the session cookie), the loopback redirect with the code, `/oauth/token`, then
  `tools/list` and a sponsor-data call as a Free and as a Premium user (teaser vs data), a plan
  change taking effect on the next request, the SDK refreshing an expired access token with
  rotation, a denied consent, and RFC 7009 revocation stopping the token. The only stand-in is the
  browser / web consent screen (it makes the same two HTTP calls the screen makes).
- `tests/api/test_oauth_routes.py`, `tests/accounts/test_mcp_oauth_*.py`,
  `tests/sources/test_oauth_client_metadata.py` - PKCE, redirect-URI matching (loopback port rule,
  open-redirect rejection), single-use / 60 s / bound codes, refresh rotation and replay
  revocation, registration validation and caps, metadata documents and the SSRF fence, consent CSRF
  nonce, token audience / issuer / type checks, revocation.
- `tests/mcp/test_connector_*.py` - protocol versions, tool-name and schema rules, the 401
  challenge shape, host/origin protection, limits, `/healthz`.

**Not** verified against the live services (documentation only): claude.ai, Claude Desktop, Claude
Code, Gemini CLI, Codex, Cursor, VS Code, Windsurf and the Messages API connector; real TLS to a
metadata-document host (the fetcher pins the connection to the checked address and sends the host
name as TLS server name, which is httpcore's documented `sni_hostname` behaviour); the web consent
screen itself (it is a web-app deliverable).

## Known limits

- The consent screen is a web-app page (`/<locale>/oauth/consent`); the API side is complete but
  until the page exists, `/oauth/authorize` lands on a missing route.
- No "connected apps" list in the app yet (see Revoking access).
- Atomic code/refresh claims (`UPDATE ... WHERE used_at IS NULL`) carry the single-use guarantee
  on Postgres; only SQLite is exercised in the suite (a Postgres concurrency test needs the
  `concurrency` profile and a live database), so that path is reasoned, not run.
- A client that retries a refresh after losing the response, with the old token, trips replay
  detection and the user must sign in again (rotation without a grace window, by design).
- Unknown scopes a client asks for (`offline_access`, `openid`) are ignored; the only scope is
  `mcp:read`, and the token response says what was granted.
- The Messages API connector uses tools only. Resources are visible to claude.ai and Claude Code
  but not to the API connector; every datum is therefore also reachable as a tool.
- Legacy SSE-only transport is not served (`/sse`); current clients use streamable HTTP.
- Large-tail tools are reached in two steps (`search_tools`, then `execute_tool`) by design.
- Behind Cloudflare, free/proxied plans cap request duration (about 100 s); tool calls are well
  below that.
