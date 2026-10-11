/**
 * The one JSON request seam every feature API client shares: credentials + the refresh-on-401
 * retry (`fetchWithRefresh`), the backend's `{code, message, retryable}` error envelope parsed into
 * a typed error, and 204/empty-body handling. Each feature keeps its own named error class (so
 * `instanceof` checks stay specific) but they all extend `ApiRequestError`.
 */
import { fetchWithRefresh } from "@/lib/api/auth";
import { apiUrl } from "@/lib/api/config";
import { apiErrorSchema } from "@/lib/api/schemas";

export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;
  constructor(name: string, code: string, message: string, status: number, retryable: boolean) {
    super(message);
    this.name = name;
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

export type ApiRequestInit = RequestInit & {
  /** Set false for routes that answer with no body (204). Default true. */
  expectBody?: boolean;
};

type ErrorFactory = (code: string, message: string, status: number, retryable: boolean) => Error;

/** Builds a `requestJson` that throws the feature's own error class on a non-2xx response. */
export function createRequestJson(
  makeError: ErrorFactory,
): (path: string, init?: ApiRequestInit) => Promise<unknown> {
  return async (path, init = {}) => {
    const { expectBody = true, ...rest } = init;
    const response = await fetchWithRefresh(apiUrl(path), {
      credentials: "include",
      headers: { accept: "application/json", ...rest.headers },
      ...rest,
    });
    if (!response.ok) {
      const parsed = apiErrorSchema.safeParse(await response.json().catch(() => null));
      if (parsed.success) {
        throw makeError(
          parsed.data.code,
          parsed.data.message,
          response.status,
          parsed.data.retryable,
        );
      }
      throw makeError(
        "unknown_error",
        `Request failed (${response.status})`,
        response.status,
        response.status >= 500,
      );
    }
    if (!expectBody || response.status === 204) {
      return null;
    }
    return response.json();
  };
}

export function jsonHeaders(): HeadersInit {
  return { "content-type": "application/json" };
}
