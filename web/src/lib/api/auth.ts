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

export const OAUTH_PROVIDERS = ["google", "line"] as const;
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

let inFlightRefresh: Promise<boolean> | null = null;

async function requestRefresh(): Promise<boolean> {
  try {
    const response = await fetch(apiUrl("/auth/refresh"), {
      method: "POST",
      credentials: "include",
      headers: { accept: "application/json" },
    });
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
