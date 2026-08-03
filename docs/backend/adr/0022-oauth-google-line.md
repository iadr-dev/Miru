# ADR 0022 — OAuth sign-in: Google + LINE Login, authorization-code + PKCE

## Status
Accepted. Refines auth in [modules/12](../modules/12-multi-user-tiers.md) §1;
[database-schema.md](../database-schema.md) (`users.email` nullable, `oauth_flows`);
[modules/16](../modules/16-infrastructure.md) §8 (config).

## Context
Sign-in uses **Google** and **LINE Login**. Verified both against the official docs; the design
was generically correct but under-specified vs. the real flows.

## Decision
- **Authorization-code flow + PKCE (S256)** for both providers → then our own **JWT session**
  (access + refresh). Endpoints: Google `accounts.google.com/o/oauth2/v2/auth` ·
  `oauth2.googleapis.com/token`; LINE `access.line.me/oauth2/v2.1/authorize` ·
  `api.line.me/oauth2/v2.1/token`. Scopes: Google `openid email profile`; LINE
  `profile openid email`.
- **Two endpoints:** `POST /auth/oauth/{provider}` (mint `state` + PKCE `code_verifier`, store
  in `oauth_flows`/Redis, redirect) and `GET /auth/oauth/{provider}/callback` (verify `state`,
  exchange `code` with secret + `code_verifier`, upsert `users`+`oauth_accounts`, issue JWT).
- **Identity key = `oauth_accounts(provider, provider_account_id)`**, not email. **`users.email`
  is nullable** — LINE email requires the `email` scope **and** console approval and can still
  be declined.
- **Multi-provider = one account** (§1a): a user can link Google **and** LINE. Resolution —
  known provider-id → that user; else verified-email match → auto-link; else new user; and in
  Settings while logged-in → link to current user (any email). Unlink allowed only while ≥1
  login method remains. Endpoints `/me/connections`, `/auth/oauth/{provider}/link`.
- **LINE ≠ Messaging:** `LINE_CHANNEL_*` (Login) is separate from `LINE_MESSAGING_*` (the
  notification `DeliveryChannel`) — different channels/credentials.
- **LINE auto-login failure** (private browsing / OS link limits): detect at callback via
  `state` mismatch / unusable `code` → retry authorize with **`disable_auto_login=true`**
  (button-triggered), fall back to normal login.
- Client secrets/tokens are KMS-encrypted platform secrets; `state`/`code_verifier` are
  per-request (short-TTL, not env).
- **Session security (§1b):** split-token model — **refresh token in an `httpOnly; Secure;
  SameSite=Lax` cookie** (path `/auth/refresh`, ~30d), **access token in memory** (~15 min,
  `Bearer`), **never localStorage** (XSS surface: LLM/scraped content). Refresh-token
  **rotation + reuse-detection** (reuse → revoke session family) via the `sessions` table;
  server-side revocable (logout / sign-out-all). **Silent re-login** on return: SPA calls
  `/auth/refresh`, the persisted cookie re-issues an access token — no re-login unless the
  refresh token expired/was revoked. HTTPS/HSTS enforced.

## Consequences
- Matches both providers' current specs incl. PKCE + LINE's auto-login edge case.
- Nullable email + oauth-keyed identity avoids the LINE-no-email failure mode.
- Adds `oauth_flows` (transient) + `/auth/oauth/{provider}/callback` + `/auth/refresh`.
- No governance change; secrets handling unchanged (§6/§9, §16.8).
