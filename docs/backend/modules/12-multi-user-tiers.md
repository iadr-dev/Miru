# Module: Multi-user, tiers & entitlements (`backend/src/accounts/`)

Turns the framework from local-first single-operator into a **multi-user hosted product**:
authentication, user tiers (free/premium/pro), entitlements, per-tier chat limits & quotas,
and BYOK. Why: the product is a hosted multi-user service, not a single-operator local tool.

## 1. Authentication & identity

**OAuth 2.0 authorization-code flow** (Google + LINE Login; Telegram via OpenID Connect, link-only in Settings), then our own **JWT session**
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

- **PKCE (S256) is LINE-only.** LINE recommends it as defense-in-depth against code interception,
  complementing (not replacing) the client secret for our confidential server. Google's
  authorization-code flow for confidential server-side clients does not require or meaningfully
  add PKCE — `state` is Google's real CSRF defense here — so the Google leg uses `state` only,
  no `code_verifier`/`code_challenge`.
- **LINE auto-login failure:** the LINE in-app auto-login can fail (private browsing, OS
  Universal/App-Link limits). Detected at the callback by **`state` mismatch / unusable `code`**
  → the app retries the authorize URL with **`disable_auto_login=true`** (button-triggered
  navigation, not a JS redirect) and shows a normal login fallback. Google has no equivalent.

| Provider | authorize / token endpoint | client_id / secret (config) | scopes | identity source |
|----------|----------------------------|-----------------------------|--------|-----------------|
| **Google** | `accounts.google.com/o/oauth2/v2/auth` · `oauth2.googleapis.com/token` | `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` | `openid email profile` | ID token + userinfo |
| **Log in with Telegram** (OIDC) | `oauth.telegram.org/auth` · `oauth.telegram.org/token` (HTTP Basic client auth) · JWKS `oauth.telegram.org/.well-known/jwks.json` | `TELEGRAM_LOGIN_CLIENT_ID` (defaults to the bot id) / `TELEGRAM_LOGIN_CLIENT_SECRET` / `TELEGRAM_LOGIN_REDIRECT_URI` (register under the bot's *Login Widget* in @BotFather) | `openid profile` (never `phone`) | **ID token (RS256)**; PKCE S256; no email — `sub` is the Telegram user id. Identity only — alert delivery to Telegram still uses the bot link flow (modules/14) |
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

**Google and LINE are peer, coexisting login methods on the same account — never a primary/
fallback pair.** A user may hold both simultaneously and sign in with either one on any visit;
neither provider is authoritative over the other, and there is no "primary provider" concept
anywhere in the schema. `oauth_accounts` is a separate table (many providers per `user_id`), so
this is native, not a retrofit. Resolution at callback:

1. **Known provider identity** — `oauth_accounts(provider, provider_account_id)` matches → log
   into that user. (Primary key; always works, even when LINE has no email.)
2. **New provider identity, logged-out** — if the provider returns a **verified email** that
   matches an existing `users.email`, **auto-link** (add an `oauth_accounts` row to that user);
   else **create a new user**. (Only *verified* emails auto-link — never link on an unverified
   or absent email; LINE's may be absent.)
3. **New provider identity, already logged-in (Settings "link account")** — attach the new
   provider to the **current** user regardless of email. This is how a Google user adds LINE.

Rules: one provider identity may be linked to **several** users (e.g. one LINE account on a main
and a second Miru account) — each account that completes the link flow shows it as linked;
signing in with such an identity opens the account that linked it first (an account chooser is a
possible later refinement); a user can
**unlink** a provider only while **≥1 login method remains** (never orphan an account).

**Settings** shows linked providers with link/unlink; `display_name`/`picture_url` seed from the
first provider and are user-overridable via `PATCH /me`. Concretely, case 3 above plays out as:
a user first signs up via Google → `GET /me/connections` shows a "Google — linked" badge only →
in Settings they click "Link LINE account" → `POST /auth/oauth/line/link` starts the same
authorization-code(+PKCE) flow as a fresh login, but tagged with the **current, already-
authenticated** `user_id` → on a successful callback, `complete_authorization` resolves case 3
(attach, don't create-or-auto-link-by-email) → `GET /me/connections` now shows both
"Google — linked" and "LINE — linked" badges, and either provider logs into the same account
from then on.

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

| Tier | AI chats/day | FinMind class | Gated AI features |
|------|--------------|---------------|-------------------|
| **Free** | 3 | Free (no Sponsor pool) | **EOD prices** (`price_eod`); indicators + 多空 score (`get_stock_score`/`get_score_leaderboard`, public factors — real, cache-only reads, [module 20](../modules/20-analytics-scoring.md) §2c); tag screener (`run_tag_screener`/`get_tag_catalog`, public criteria — real, [module 20](../modules/20-analytics-scoring.md) §5a); theme lookup (`get_theme_members`/`get_symbol_themes`/`get_theme_tiers`, public); basic `web_search` (toggle); **cosmetic pets** (free register pull — no tier gate); **agent-voice personality presets** ([modules/23](../modules/23-agent-personality.md) — no tier gate) |
| **Premium** | 100 | Sponsor (shared 6000/hr pool) | + **live-ish quote feed** (`price_intraday`, SSE, **5s** while market-open), tick / minute-K (sponsor-class data), `sponsor` data (分點/官股/股權分級/產業鏈/主動ETF持股), **AI summary/SWOT** (`get_ai_summary`/`get_swot` — design intent, not yet a real tool, [module 20](../modules/20-analytics-scoring.md)), full 多空 score (incl. 分點 chip sub-signal), AI top-N ranking, web_search |
| **Pro** | unlimited | Sponsor (shared 6000/hr pool) | + **deep_research** (multi-step cited research; no daily report cap, each run is bounded by its own budget) |

**Alerts & schedules are not a tier feature:** every signed-in user gets them under uniform fair-use caps (`scheduler/caps.py`: 50 alert rules, 10 schedules; exceeding → 409 `cap_reached`; see [14](14-scheduler-alerts.md)). Tiers only gate the *data* a notification carries — sponsor values are masked for non-entitled users.

**Price tiering:** Free = EOD only; Premium/Pro = `price_intraday` — a delayed snapshot pushed
over SSE by the shared **demand-driven server poller** (5s, market-open only, [modules/16](../modules/16-infrastructure.md)
§2a; the live-quote poller's bounded observation is a governed carve-out, see [backend/CLAUDE.md](../../../backend/CLAUDE.md) §4), caveated as delayed not true streaming. Outside trading hours it serves the
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
   MIS snapshot is `public` when served by `twse_mis` and `sponsor` when served by `finmind` — see §4.)
   ← this module + §4.
2. **Tier-feature** — `feature_allowed(user, feature)` (e.g. `deep_research`=Pro,
   `live_quote_stream`=Premium (server-pushed 5s streaming — a *feature* gate, not a data-class gate),
   `tick_minute_k`=Premium (sponsor-class data); note **voice is Free** - speech-to-text and
   read-aloud are not tier-gated and have no daily quota while they run on free providers (local
   Whisper, Edge TTS); every user gets a 20 requests/min abuse bound, and a daily cap of 100 per
   kind (`429 voice_limit_reached`) applies only if a provider that bills per call (TTS `openai`)
   is configured — [13](13-chat-interaction-features.md) Voice): a feature-min-tier lookup.
   **Model picker (platform key):** a Free account may pick only the light models
   (`FREE_PLATFORM_MODELS`; others -> `403 model_requires_upgrade`, shown locked in the picker via
   `entitlements.platform_models`); paid tiers and BYOK callers (their own key) are unrestricted.
3. **Per-turn consent** — a request-scoped toggle the *user* flips for that turn (e.g. the
   web-search toggle, [03-tools.md](03-tools.md)): opt-in, not tier.

Real API (`accounts/entitlements.py` — module functions, not a class; `TIER_LIMITS`/
`FEATURE_MIN_TIER` are the config tables):

```python
def chat_limit_for(user: User, *, byok_active: bool) -> int | None   # None = unlimited (or BYOK)
def rate_limit_per_minute(user: User) -> int
def feature_allowed(user: User, feature: str) -> bool                # (2) FEATURE_MIN_TIER lookup
def finmind_class_for(user: User) -> Tier                             # (1) drives data_class unmask — FREE or PREMIUM
def entitlement_view(user: User, *, byok_active: bool) -> EntitlementView  # folds all axes into the `GET /me` envelope
```

- **Market data / watchlist / news stay free** for everyone. Gating applies **only** to AI
  features and Sponsor-only datasets.
- **Unentitled access returns a teaser envelope**, never a hard 401:
  `{locked: true, required_tier, preview}` — e.g. top-N rows + a locked strip for a Sponsor
  table, or a blurred AI summary. Keeps the product discoverable.

## 4. Rate limits & quotas (ties into hooks + M5Q)

Enforced by `api.turn_driver.enforce_pre_turn_gate` — the `pre_turn` gate every turn-starting
route (`/agent/query`, `regenerate`, `edit`) calls before a turn is created ([10-events-hooks.md](10-events-hooks.md)
(hook taxonomy):

- **Request rate** (per-minute abuse bound, `rate_limit_per_minute`; an internal protection, no longer shown to users) and **daily chat count** per tier. Deep research has no daily cap (Pro-only).
- **FinMind quota is not a live upstream poll.** There is one FinMind token, and every call to
  it, from any tier and from ingest, is counted against its **6000 per hour** cap; ingest may use
  at most its share, so users keep the rest ([16](../modules/16-infrastructure.md) §4). Premium/Pro
  (`finmind_class=sponsor`) fan-outs also reserve lanes pre-flight from the shared Sponsor lane
  pool, granted only while calls are left. Free-tier turns skip the lane pool. When the cap is
  near, requests degrade gracefully (cached / an unavailable source) rather than failing.
- **LLM cost accounting** — `usage.store.record_turn`/`write_turn_usage` persist `cost_micros`
  per turn/user ([10](10-events-hooks.md) §3). **Not yet built:** an actual per-
  tier/user cost *budget* that rejects a turn on overspend — today cost is recorded, not
  enforced. Only the two limits above (`enforce_pre_turn_gate`'s rate + daily-chat reservation)
  are real gates.

## 5. BYOK (bring your own key)

- A user can store their own provider key(s) (Anthropic/OpenAI/…) — held in the user's
  encrypted secret store, **not** in memory/prompts, and used only to satisfy that user's
  requests via the LLM adapter.
- BYOK **lifts the daily chat limit** (`chat_limit_for → None`) since the user pays inference.
- Governance unchanged: keys are never logged, journaled, or echoed; deletable on demand.
- **Who is a BYOK caller:** anyone with at least one saved LLM key (providers: anthropic, openai,
  gemini, openrouter - `POST /me/keys` rejects others with 422). No subscription
  is required.
- **Routing (`api/byok.py`, `llm/registry.py::byok_scope`):** before a turn starts, `resolve_byok`
  takes the thread's model (else the AGENT role default), decrypts the key for *that model's
  provider* and runs the whole turn inside `byok_scope`: every role (classifier, lanes, synthesis,
  title) resolves to that one model on the user's key, with **no fallback to a platform key**. If
  the user has keys but none for the picked model's provider, the turn is refused
  (`409 byok_key_required`) - never served on ours, since a BYOK turn is uncapped.
- The chat picker shows only the providers the caller has keys for (`GET /me/keys` returns names,
  never keys). Removing the keys restores the plan's normal limits and models.
- Deep research stays Pro-only with no daily report cap (each run is bounded by its own budget, and
  the per-minute rate and daily-chat allowance still apply); its model calls run on the user's key.
  `llm_usage.byok` marks these calls.
- Not covered yet: scheduled runs (`scheduler/`) still use platform keys and the tier cap.

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
GET  /usage/quota                     the caller's resolved `EntitlementView` (chat/day limit,
                                      FinMind class, BYOK, features, platform models) — limits only
GET  /usage/today                     chats claimed in today's UTC window
GET  /usage/llm                       per-day / per-model LLM usage summary
POST /me/keys                         store BYOK key (encrypted)
DELETE /me/keys/{provider}
```

## Governance obligations
- No credentials (OAuth secrets, BYOK, FinMind token) stored in plaintext, prompts, journal,
  or memory. Encrypted at rest; supplied to the adapter at call time only.
- Per-user data isolation enforced at the store layer (`user_id` scoping) — no cross-user leak.
- Non-trading and caveat rules apply to every tier equally (a paid tier buys throughput +
  access to `sponsor` data, never a trading recommendation).
