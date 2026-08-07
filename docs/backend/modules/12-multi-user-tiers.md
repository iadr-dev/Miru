# Module: Multi-user, tiers & entitlements (`backend/src/accounts/`)

Turns the framework from local-first single-operator into a **multi-user hosted product**:
authentication, user tiers (free/premium/pro), entitlements, per-tier rate limits & quotas,
and BYOK. This is a deliberate scope expansion — see [../adr/0008](../adr/0008-multi-user-hosted.md).

## 1. Authentication & identity

**OAuth 2.0 authorization-code flow** (Google + LINE Login), then our own **JWT session**
(access + refresh, httpOnly cookie; `JWT_*` config). Anonymous browsing allowed for read-only
public market data; AI chat requires sign-in.

Two-leg flow per provider, **with PKCE (S256)**:
1. `POST /auth/oauth/{provider}` → server generates a random **`state`** (CSRF) **and a PKCE
   `code_verifier`**, stores both in the session, and builds the authorize URL with
   `state` + `code_challenge=base64url(sha256(verifier))` + `code_challenge_method=S256` (LINE
   also adds a `nonce`). Redirects the user.
2. `GET /auth/oauth/{provider}/callback?code&state` (= the `*_REDIRECT_URI` in config) → verify
   `state`, exchange `code` at the token endpoint **server-side (client secret + `code_verifier`)**,
   fetch identity, upsert `users` + `oauth_accounts`, issue our JWT session.

- **PKCE (S256)** is applied to both providers (LINE-recommended, defense-in-depth vs. code
  interception; complements — doesn't replace — the client secret for our confidential server).
- **LINE auto-login failure:** the LINE in-app auto-login can fail (private browsing, OS
  Universal/App-Link limits). Detected at the callback by **`state` mismatch / unusable `code`**
  → the app retries the authorize URL with **`disable_auto_login=true`** (button-triggered
  navigation, not a JS redirect) and shows a normal login fallback. Google has no equivalent.

| Provider | authorize / token endpoint | client_id / secret (config) | scopes | identity source |
|----------|----------------------------|-----------------------------|--------|-----------------|
| **Google** | `accounts.google.com/o/oauth2/v2/auth` · `oauth2.googleapis.com/token` | `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` | `openid email profile` | ID token + userinfo |
| **LINE Login** | `access.line.me/oauth2/v2.1/authorize` · `api.line.me/oauth2/v2.1/token` | `LINE_CHANNEL_ID`/`LINE_CHANNEL_SECRET` | `profile openid email` | **ID token (JWT)**; verify `nonce` |

- **`state` is required** (CSRF) on both; LINE also uses a `nonce` bound to the ID token.
- **LINE email is conditional:** requires the `email` scope **and** email-permission approval in
  the LINE Developers console; even then a user may decline. So **`users.email` is nullable** —
  identity is keyed on **`oauth_accounts(provider, provider_account_id)`**, never on email alone.
- **LINE Login ≠ LINE Messaging API** — separate channels/credentials. `LINE_CHANNEL_*` = Login
  (this section); `LINE_MESSAGING_*` = the Messaging API used by the notification **DeliveryChannel**
  ([14-scheduler-alerts.md](14-scheduler-alerts.md) §4), not for sign-in.
- OAuth client secrets + LINE/Paddle tokens are KMS-encrypted platform secrets ([16](16-infrastructure.md) §8),
  never logged/journaled.

- `users` table: `id, email (nullable), display_name, picture_url, birthday, phone_country_code,
  phone_number, nationality, tier, oauth_accounts[], created_at`. Profile fields
  (birthday / phone with country code / nationality) are user-editable via `PATCH /me`.

## 1a. Multi-provider identity & account linking

A user may sign in with **Google and/or LINE** and expect **one account**. `oauth_accounts` is a
separate table (many providers per `user_id`), so this is native. Resolution at callback:

1. **Known provider identity** — `oauth_accounts(provider, provider_account_id)` matches → log
   into that user. (Primary key; always works, even when LINE has no email.)
2. **New provider identity, logged-out** — if the provider returns a **verified email** that
   matches an existing `users.email`, **auto-link** (add an `oauth_accounts` row to that user);
   else **create a new user**. (Only *verified* emails auto-link — never link on an unverified
   or absent email; LINE's may be absent.)
3. **New provider identity, already logged-in (Settings "link account")** — attach the new
   provider to the **current** user regardless of email. This is how a Google user adds LINE.

Rules: one provider identity maps to exactly one user (unique constraint); linking a provider
already bound to another account is refused (surface "already linked elsewhere"); a user can
**unlink** a provider only while **≥1 login method remains** (never orphan an account).

**Settings** shows linked providers with link/unlink; `display_name`/`picture_url` seed from the
first provider and are user-overridable via `PATCH /me`.

## 1b. Session security — token storage & persistence

**Split-token model (XSS- and CSRF-resistant):**
- **Refresh token** → `httpOnly; Secure; SameSite=Lax` cookie, **path-scoped to `/auth/refresh`**,
  ~30d (`JWT_REFRESH_TOKEN_EXPIRE_DAYS`). JS can never read it (XSS-safe); server-side revocable
  via the `sessions` table (`token_hash`).
- **Access token** → **short-lived (~15 min)**, held **in memory** (never localStorage, never a
  JS-readable cookie), sent as `Authorization: Bearer` on API/SSE calls. In-memory + custom
  header ⇒ inherently CSRF-safe.
- **Not localStorage** for either — localStorage is readable by any injected script, and this
  product renders LLM output + scraped web/news (real XSS surface).
- **HTTPS only:** `Secure` cookies + HSTS at the edge ([16](16-infrastructure.md) §8, [17](17-devops-operations.md) §7);
  add a CSRF double-submit token (or custom-header requirement) on `/auth/refresh` as belt-and-braces.

**Silent re-login (user closes site without logout, returns later):**
1. Tab closed → in-memory access token gone; the httpOnly **refresh cookie persists**.
2. Reopen → SPA has no access token → `POST /auth/refresh` → browser auto-sends the refresh
   cookie → server checks `sessions` (not revoked/expired) → issues a fresh access token →
   **user is silently signed back in** (no OAuth round-trip). Expired/revoked → 401 → login.

**Refresh-token hardening:**
- **Rotation** — each `/auth/refresh` issues a new refresh token and invalidates the prior
  (`sessions.token_hash` rotates).
- **Reuse detection** — presenting an already-rotated-out refresh token = theft signal →
  **revoke the whole session family** (defense against stolen refresh tokens).
- **Server-side revocable** — `sessions` rows back logout, "sign out all devices," and
  reuse-revocation (delete rows). Access-token expiry keeps ~15 min so revocation takes effect fast.

- All prior modules gain a **`user_id` scope**: threads, memory, usage, feedback, schedules
  are per-user. `ConversationStore` / `MemoryStore` / journal queries filter by `user_id`.

## 2. Tiers

| Tier | AI chats/day | Req/min | FinMind class | Gated AI features |
|------|--------------|---------|---------------|-------------------|
| **Free** | 3 | 60 | Free (no Sponsor pool) | **EOD prices** (`price_eod`); indicators + 多空 score (`get_stock_score`/`get_score_leaderboard`, public factors); tag screener (`run_tag_screener`/`get_tag_catalog`); theme/tier lookup (`get_theme_members`/`get_symbol_themes`/`get_theme_tiers`); **cosmetic pets** (free register pull — no tier gate); **agent-voice personality presets** ([modules/23](../modules/23-agent-personality.md) — no tier gate) |
| **Premium** | 100 | 300 | Sponsor (shared 6000/hr pool) | + **live-ish quote feed** (`price_intraday`, SSE, **5s** while market-open), `sponsor` data (分點/官股/股權分級/產業鏈/主動ETF持股), full 多空 score (incl. 分點 chip sub-signal), news_sentiment. **`get_ai_summary`/`get_swot`/AI top-N ranking/theme discovery are design intent, not real tools yet** — see [module 20](20-analytics-scoring.md)'s implementation-status note. There is no per-turn `web_search` toggle in the real API today (`AgentQueryRequest` is `{message, thread_id}` only) |
| **Pro** | unlimited | 600 | Sponsor (shared 6000/hr pool) | + tick/minute-K, **deep_research** (multi-step web), scheduler |

**Price tiering:** Free = EOD only; Premium/Pro = `price_intraday` — a delayed snapshot pushed
over SSE by the shared **demand-driven server poller** (5s, market-open only, [modules/16](../modules/16-infrastructure.md)
§2a; governance §9b), caveated as delayed not true streaming. Outside trading hours it serves the
last snapshot / EOD.

Single source of truth: `accounts/entitlements.py` — one module both API and agent consult.
Tier→limits and feature→min-tier tables are config, not scattered constants.

## 3. Entitlements & feature gating

**Three distinct gating axes** (they look similar but are different concerns — not redundant;
together they are "capability resolution"):
1. **Data licensing** — `data_class = public | sponsor` is a property of the **serving adapter**
   (which source actually returned the datum, [19](19-data-access-router.md) §4 provenance), *not*
   the abstract capability. The router stamps each normalized result with the `data_class` of the
   adapter that served it; entitlement resolves it per-user → masked teaser. (So the *same* live-ish
   MIS snapshot is `public` when served by `twse_mis` and `sponsor` when served by `finmind` — see §4
   + governance §9b.) ← this module + §4.
2. **Tier-feature** — `feature_allowed(user, feature)` (e.g. `deep_research`=Pro,
   `live_quote_stream`=Premium (server-pushed 5s streaming — a *feature* gate, not a data-class gate),
   `tts_read_aloud`=Premium+; note **STT/voice-input is Free**, quota-bounded not tier-gated —
   [13](13-chat-interaction-features.md) §voice): a feature-min-tier lookup.
3. **Per-turn consent** — a request-scoped toggle the *user* flips for that turn (e.g. the
   web-search toggle, [03-tools.md](03-tools.md)): opt-in, not tier.

```python
class Entitlements:                              # OUR gate — feature-tier + data-licensing axes
    def chat_limit_for(user) -> int | None       # None = unlimited (or BYOK)
    def rate_limit_for(user) -> RateLimit
    def feature_allowed(user, feature) -> bool    # (2) FEATURE_MIN_TIER lookup
    def finmind_class_for(user) -> "free" | "sponsor"   # (1) drives data_class unmask
    # or fold all three axes into one resolver: resolve_capability(user, request) -> CapabilityDecision
```

- **Market data / watchlist / news stay free** for everyone. Gating applies **only** to AI
  features and Sponsor-only datasets.
- **Unentitled access returns a teaser envelope**, never a hard 401:
  `{locked: true, required_tier, preview}` — e.g. top-N rows + a locked strip for a Sponsor
  table, or a blurred AI summary. Keeps the product discoverable.

## 4. Rate limits & quotas (ties into hooks + M5Q)

Enforced at the `pre_turn` / `pre_llm` hooks ([10-events-hooks.md](10-events-hooks.md)):

- **Request rate** (per-minute) and **daily chat count** per tier.
- **FinMind quota** is a single static Redis-backed pool (6000 req/hr) shared across every
  Premium/Pro user — atomically reserved before each Sponsor-tier fetch and refunded on
  failure/under-use (`usage/finmind_pool.py::RedisFinMindPoolReserver`). Free tier never draws
  on this pool at all; there is no Free-tier FinMind quota concept, and no live upstream
  quota-polling call anywhere. Near the cap, requests degrade gracefully (serve cached /
  `unavailable-tier`) rather than failing — surfaced via M5Q.
- **LLM cost budget** per tier/user; `usage` accounting from [10](10-events-hooks.md) enforces it.

## 5. BYOK (bring your own key)

- A user can store their own provider key(s) (Anthropic/OpenAI/…) — held in the user's
  encrypted secret store, **not** in memory/prompts, and used only to satisfy that user's
  requests via the LLM adapter.
- BYOK **lifts the daily chat limit** (`chat_limit_for → None`) since the user pays inference.
- Governance unchanged: keys are never logged, journaled, or echoed; deletable on demand.

## 6. Endpoints

```
GET  /me                              profile + tier + entitlements
PATCH /me                             update profile (display_name, birthday, phone, nationality)
POST /auth/oauth/{provider}           begin OAuth (google|line) — sets state, redirects to provider
GET  /auth/oauth/{provider}/callback  OAuth redirect target (?code&state) — verify state, exchange
                                      code, upsert user+oauth_account, issue JWT session
POST /auth/refresh                    rotate JWT access token via refresh token
POST /auth/logout                     revoke session
GET  /me/connections                  linked providers (google/line) for Settings
POST /auth/oauth/{provider}/link      link a provider to the CURRENT user (Settings; logged-in)
DELETE /me/connections/{provider}     unlink a provider (refused if it's the last login method)
GET  /usage/quota                     per-user usage vs limits (chat/day, req/min, FinMind, cost)
POST /me/keys                         store BYOK key (encrypted)
DELETE /me/keys/{provider}
```

## Governance obligations
- No credentials (OAuth secrets, BYOK, FinMind token) stored in plaintext, prompts, journal,
  or memory. Encrypted at rest; supplied to the adapter at call time only.
- Per-user data isolation enforced at the store layer (`user_id` scoping) — no cross-user leak.
- Non-trading and caveat rules apply to every tier equally (a paid tier buys throughput +
  access to `sponsor` data, never a trading recommendation).
