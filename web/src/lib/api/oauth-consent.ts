/**
 * MCP OAuth consent client (`GET/POST /oauth/consent/{request_id}`). Goes through the shared
 * request seam, so it sends credentials and retries once after `/auth/refresh` on a 401; a 401
 * that survives that means the user is signed out.
 */
import { ApiRequestError, createRequestJson, jsonHeaders } from "@/lib/api/http";
import {
  type ConsentDecision,
  type ConsentResponse,
  consentDecisionResponseSchema,
  consentResponseSchema,
} from "@/types/oauth-consent";

export class ConsentApiError extends ApiRequestError {
  constructor(code: string, message: string, status: number, retryable: boolean) {
    super("ConsentApiError", code, message, status, retryable);
  }
}

const requestJson = createRequestJson(
  (code, message, status, retryable) => new ConsentApiError(code, message, status, retryable),
);

function consentPath(requestId: string): string {
  return `/oauth/consent/${encodeURIComponent(requestId)}`;
}

export async function getConsent(requestId: string): Promise<ConsentResponse> {
  return consentResponseSchema.parse(await requestJson(consentPath(requestId)));
}

/** Answer the request; resolves with the URL to send the browser to (the client's redirect URI
 * with a code, or with `access_denied`). */
export async function decideConsent(
  requestId: string,
  decision: ConsentDecision,
  nonce: string,
): Promise<string> {
  const raw = await requestJson(consentPath(requestId), {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ decision, nonce }),
  });
  return consentDecisionResponseSchema.parse(raw).redirect_to;
}
