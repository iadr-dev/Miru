/** `GET /me` — the signed-in user + entitlements. Credentialed (httpOnly session cookie set by the
 * OAuth flow round-trips). Used by the app-shell auth guard and the top bar. Mirrors
 * `backend/src/api/routers/auth.py::me`. */
import { refreshSession } from "@/lib/api/auth";
import { apiUrl } from "@/lib/api/config";
import { type MeResponse, meResponseSchema } from "@/types/account";

export class UnauthenticatedError extends Error {
  constructor() {
    super("Not authenticated");
    this.name = "UnauthenticatedError";
  }
}

async function requestMe(signal?: AbortSignal): Promise<Response> {
  return fetch(apiUrl("/me"), {
    method: "GET",
    credentials: "include",
    headers: { accept: "application/json" },
    ...(signal ? { signal } : {}),
  });
}

/** Fetch the current session. The access-token cookie is short-lived (~15 min) by design; a 401
 * here does not necessarily mean the user signed out — the 30-day refresh cookie may still be
 * valid. So a 401 triggers exactly one `POST /auth/refresh` + retry before giving up: this is what
 * lets a closed-and-reopened tab land back on the authenticated app instead of bouncing to sign-in.
 * Throws `UnauthenticatedError` only once the refresh attempt itself fails (no/expired/reused
 * refresh cookie) — the guard's real redirect signal. A plain `Error` covers any other
 * transport/parse failure (surfaced as a retryable shell error). */
export async function fetchMe(signal?: AbortSignal): Promise<MeResponse> {
  let response = await requestMe(signal);
  if (response.status === 401) {
    const refreshed = await refreshSession();
    if (!refreshed) {
      throw new UnauthenticatedError();
    }
    response = await requestMe(signal);
  }
  if (response.status === 401) {
    throw new UnauthenticatedError();
  }
  if (!response.ok) {
    throw new Error(`Failed to load account (${response.status})`);
  }
  const parsed = meResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new Error("Unexpected /me response shape");
  }
  return parsed.data;
}
