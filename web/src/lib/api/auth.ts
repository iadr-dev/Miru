/**
 * OAuth client — the real begin→redirect→callback contract from `backend/src/api/routers/auth.py`.
 *
 * Flow (do not invent a different shape):
 *  1. POST /auth/oauth/{provider} with credentials → backend sets httpOnly CSRF/PKCE/nonce cookies
 *     and returns { authorization_url, retry_authorization_url? }. We then navigate the *browser*
 *     (full redirect) to authorization_url.
 *  2. Provider redirects the browser back to our callback page (redirect_uri points here, see
 *     .env.local.example). That page calls GET /auth/oauth/{provider}/callback?code=&state= with
 *     credentials — the backend verifies state against the flow cookies, sets the session cookies,
 *     and returns { user, entitlements }.
 *
 * All requests are `credentials: "include"` so the httpOnly flow + session cookies round-trip.
 */
import { apiUrl } from "@/lib/api/config";
import { apiErrorSchema, type BeginAuthResponse, beginAuthResponseSchema } from "@/lib/api/schemas";
import { type MeResponse, meResponseSchema } from "@/types/account";

export const OAUTH_PROVIDERS = ["google", "line", "telegram"] as const;
export type OAuthProvider = (typeof OAUTH_PROVIDERS)[number];

/** The backend error codes this flow can surface (auth.py + dependencies.py). */
export type AuthErrorCode =
  | "provider_unconfigured" // 503 — creds not set in this env (e.g. LINE)
  | "oauth_state_mismatch" // 400 — CSRF/state check failed, restart
  | "oauth_failed" // 401 — token exchange / id-token verify failed, retryable
  | "provider_already_linked" // 409 — link path only
  | "unknown_provider" // 404
  | "network_error" // fetch/transport failure on our side
  | "bad_response"; // payload failed schema validation

export class AuthError extends Error {
  readonly code: AuthErrorCode;
  readonly retryable: boolean;
  constructor(code: AuthErrorCode, message: string, retryable: boolean) {
    super(message);
    this.name = "AuthError";
    this.code = code;
    this.retryable = retryable;
  }
}

function mapErrorCode(raw: string): AuthErrorCode {
  switch (raw) {
    case "provider_unconfigured":
    case "oauth_state_mismatch":
    case "oauth_failed":
    case "provider_already_linked":
    case "unknown_provider":
      return raw;
    default:
      return "oauth_failed";
  }
}

async function readError(response: Response): Promise<AuthError> {
  try {
    const parsed = apiErrorSchema.safeParse(await response.json());
    if (parsed.success) {
      return new AuthError(
        mapErrorCode(parsed.data.code),
        parsed.data.message,
        parsed.data.retryable,
      );
    }
  } catch {
    // fall through to a generic error below
  }
  return new AuthError("oauth_failed", "Sign-in could not be completed.", response.status >= 500);
}

/** Step 1: begin the flow. Returns the provider authorize URL(s) to navigate to. */
export async function beginOAuth(provider: OAuthProvider): Promise<BeginAuthResponse> {
  let response: Response;
  try {
    response = await fetch(apiUrl(`/auth/oauth/${provider}`), {
      method: "POST",
      credentials: "include",
      headers: { accept: "application/json" },
    });
  } catch {
    throw new AuthError("network_error", "Could not reach the sign-in service.", true);
  }
  if (!response.ok) {
    throw await readError(response);
  }
  const parsed = beginAuthResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new AuthError("bad_response", "Unexpected sign-in response.", false);
  }
  return parsed.data;
}

/** Link a provider to the signed-in account: same shape as `beginOAuth`, but the backend's callback
 * attaches the verified identity to the current user and returns to settings → connections. */
export async function beginOAuthLink(provider: OAuthProvider): Promise<BeginAuthResponse> {
  let response: Response;
  try {
    response = await fetch(apiUrl(`/auth/oauth/${provider}/link`), {
      method: "POST",
      credentials: "include",
      headers: { accept: "application/json" },
    });
  } catch {
    throw new AuthError("network_error", "Could not reach the sign-in service.", true);
  }
  if (!response.ok) {
    throw await readError(response);
  }
  const parsed = beginAuthResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new AuthError("bad_response", "Unexpected sign-in response.", false);
  }
  return parsed.data;
}

/** Step 2: complete the flow. Forwards the provider's `code`/`state` query params to the
 * backend's own callback route with credentials, so it can verify `state` against the httpOnly
 * flow cookies and set the session cookies on our origin. Returns the now-signed-in user +
 * entitlements on success. */
export async function completeOAuth(
  provider: OAuthProvider,
  code: string,
  state: string,
): Promise<MeResponse> {
  let response: Response;
  try {
    const params = new URLSearchParams({ code, state });
    response = await fetch(apiUrl(`/auth/oauth/${provider}/callback?${params.toString()}`), {
      credentials: "include",
      headers: { accept: "application/json" },
    });
  } catch {
    throw new AuthError("network_error", "Could not reach the sign-in service.", true);
  }
  if (!response.ok) {
    throw await readError(response);
  }
  const parsed = meResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new AuthError("bad_response", "Unexpected sign-in response.", false);
  }
  return parsed.data;
}

/** Revoke the session (POST /auth/logout) and clear cookies. Best-effort; resolves regardless. */
export async function logout(): Promise<void> {
  try {
    await fetch(apiUrl("/auth/logout"), { method: "POST", credentials: "include" });
  } catch {
    // network failure on logout is non-fatal; the client redirect still happens
  }
}

/** The word the user must type to confirm deleting their account (the backend checks it too). */
export const DELETE_ACCOUNT_CONFIRMATION = "DELETE";

export type DeleteAccountResult =
  | { ok: true }
  | { ok: false; code: string; message: string; retryable: boolean };

/** Permanently delete the signed-in account and all its data (DELETE /me). The backend cancels
 * any Paddle subscription first and erases nothing if that fails, so a failure is safe to retry.
 * Resolves with the outcome instead of throwing, so the dialog can show the reason inline. */
export async function deleteAccount(): Promise<DeleteAccountResult> {
  let response: Response;
  try {
    response = await fetch(apiUrl("/me"), {
      method: "DELETE",
      credentials: "include",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify({ confirm: DELETE_ACCOUNT_CONFIRMATION }),
    });
  } catch {
    return { ok: false, code: "network_error", message: "", retryable: true };
  }
  if (response.ok) return { ok: true };
  try {
    const parsed = apiErrorSchema.safeParse(await response.json());
    if (parsed.success) {
      return {
        ok: false,
        code: parsed.data.code,
        message: parsed.data.message,
        retryable: parsed.data.retryable,
      };
    }
  } catch {
    // fall through to the generic failure
  }
  return { ok: false, code: "unknown", message: "", retryable: response.status >= 500 };
}

let inFlightRefresh: Promise<boolean> | null = null;
let lastRefreshAt = 0;

async function requestRefresh(): Promise<boolean> {
  try {
    const response = await fetch(apiUrl("/auth/refresh"), {
      method: "POST",
      credentials: "include",
      headers: { accept: "application/json" },
    });
    if (response.ok) lastRefreshAt = Date.now();
    return response.ok;
  } catch {
    return false;
  }
}

/** Rotate the refresh token and mint a fresh access token (POST /auth/refresh). The access-token
 * cookie is short-lived (~15 min); the refresh cookie is the 30-day credential that's supposed to
 * survive a closed tab/browser restart, but nothing does so automatically — a caller must hit this
 * endpoint when a credentialed request comes back 401. Returns `false` (never throws) on any
 * failure — no refresh cookie present, it's expired, or reuse was detected — so the caller's only
 * job is "was the session extended, yes or no".
 *
 * The backend's refresh token is single-use with reuse detection: presenting an already-rotated
 * token revokes the whole session family (a theft signal). Several `fetchMe()` calls can 401 at
 * once (e.g. two components mounting together), so every concurrent caller must await the exact
 * same in-flight request rather than each independently spending the one valid refresh token —
 * otherwise the second caller's request looks like reuse and logs out a session that was actually
 * fine. */
export function refreshSession(): Promise<boolean> {
  if (!inFlightRefresh) {
    inFlightRefresh = requestRefresh().finally(() => {
      inFlightRefresh = null;
    });
  }
  return inFlightRefresh;
}

/** Issue a `fetch`, retrying exactly once via `refreshSession()` if the first attempt comes back
 * 401 — the same access-token-expired-but-refresh-cookie-still-valid case `fetchMe()` already
 * handles (account.ts). Every other credentialed client (threads, settings, voice, agent-stream)
 * hits this same ~15-minute access-token expiry, so they share this one retry path rather than
 * each re-implementing it — without it, any request outliving the access token (e.g. clicking an
 * old thread after the tab sat idle) permanently 401s until a full page reload re-runs `fetchMe()`.
 * `init` isn't narrowed to `credentials: "include"` at the type level (callers that build the
 * request options via a spread, e.g. `settings.ts`, can't preserve that literal) — every real
 * caller passes it, but this helper doesn't enforce it structurally.
 * Returns the second response as-is (still possibly 401, if the refresh itself failed) — the
 * caller's normal status handling covers that case unchanged. */
export async function fetchWithRefresh(url: string, init: RequestInit): Promise<Response> {
  const response = await fetchHonouringRateLimit(url, init);
  if (!needsRefresh(response)) {
    return response;
  }
  const refreshed = await refreshSession();
  if (!refreshed) {
    return response;
  }
  return fetchHonouringRateLimit(url, init);
}

/** A rate-limited (429) read is retried after the server's `Retry-After`, with a little jitter so
 * a page's parallel requests do not all return at the same instant. Only safe methods are retried
 * (never a POST/DELETE that might have taken effect), only for a short wait (a long one is handed
 * back to the caller to show), and at most `RATE_LIMIT_RETRIES` times. */
const RATE_LIMIT_RETRIES = 2;
const RATE_LIMIT_MAX_WAIT_MS = 10_000;

export function retryAfterMs(response: Response): number | null {
  const raw = response.headers.get("retry-after");
  const seconds = raw === null ? Number.NaN : Number(raw);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000 : null;
}

type RateLimitListener = (retryAfterSeconds: number | null) => void;
const rateLimitListeners = new Set<RateLimitListener>();

/** Be told when a request finally comes back rate-limited (after any retries), so the UI can say
 * so once instead of every caller showing its own generic failure. Returns the unsubscribe. */
export function onRateLimited(listener: RateLimitListener): () => void {
  rateLimitListeners.add(listener);
  return () => {
    rateLimitListeners.delete(listener);
  };
}

function announceRateLimited(response: Response): void {
  const wait = retryAfterMs(response);
  const seconds = wait === null ? null : Math.max(1, Math.ceil(wait / 1000));
  for (const listener of rateLimitListeners) listener(seconds);
}

async function fetchHonouringRateLimit(url: string, init: RequestInit): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const safe = method === "GET" || method === "HEAD";
  let response = await fetch(url, init);
  for (
    let attempt = 0;
    safe && response.status === 429 && attempt < RATE_LIMIT_RETRIES;
    attempt++
  ) {
    const wait = retryAfterMs(response);
    if (wait === null || wait > RATE_LIMIT_MAX_WAIT_MS || init.signal?.aborted) break;
    await new Promise((resolve) => setTimeout(resolve, wait + Math.random() * 250));
    response = await fetch(url, init);
  }
  if (response.status === 429) announceRateLimited(response);
  return response;
}

/** The backend answers public data routes anonymously when the access token has expired (so a
 * logged-out visitor with a stale cookie still works) but flags it with `X-Auth-Expired: 1`
 * (`backend/src/api/auth_expired.py`). Without honouring that flag a signed-in Pro user would be
 * served free-tier teasers as a plain 200 and never refresh. If the refresh fails the anonymous
 * answer is still the right one, so it is returned as-is. */
const AUTH_EXPIRED_HEADER = "x-auth-expired";

function needsRefresh(response: Response): boolean {
  return response.status === 401 || response.headers.get(AUTH_EXPIRED_HEADER) === "1";
}

/** Renew the access token before it lapses (~15 min) instead of only after a request fails.
 * Live streams (`EventSource`) can't react to a 401 and reconnect with whatever cookie exists, so
 * without this a reconnect after expiry runs as anonymous (masked sponsor quotes). Refreshes on an
 * interval while signed in, and when a hidden tab comes back after the interval has passed (timers
 * are throttled in background tabs). Shares `refreshSession`'s single in-flight request, so it
 * never races a 401-triggered refresh into the backend's reuse detection. Returns a stop function. */
const KEEP_ALIVE_MS = 12 * 60 * 1000;

export function startSessionKeepAlive(): () => void {
  lastRefreshAt = Date.now();
  const refreshIfDue = (): void => {
    if (Date.now() - lastRefreshAt >= KEEP_ALIVE_MS) void refreshSession();
  };
  const timer = window.setInterval(refreshIfDue, KEEP_ALIVE_MS);
  const onVisible = (): void => {
    if (document.visibilityState === "visible") refreshIfDue();
  };
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisible);
  };
}
