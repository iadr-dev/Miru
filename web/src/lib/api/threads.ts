/** Thread CRUD + history client — the cold-read seam TanStack Query keys mirror.
 *
 * Mirrors `backend/src/api/routers/threads.py` (`GET /threads` → `{threads}`, `GET /threads/{id}`
 * → `Thread`, `POST /threads` → `Thread`) and `threads_actions.py`
 * (`POST /threads/{id}/turns/{tid}/cancel`). All credentialed (session cookie). Parse-don't-trust:
 * every response is Zod-validated at the boundary. */
import { z } from "zod";
import { fetchWithRefresh } from "@/lib/api/auth";
import { apiUrl } from "@/lib/api/config";
import { type Thread, threadListResponseSchema, threadSchema } from "@/types/conversation";

async function jsonOrThrow(response: Response, context: string): Promise<unknown> {
  if (response.status === 401) {
    throw new Error("unauthenticated");
  }
  if (!response.ok) {
    throw new Error(`${context} failed (${response.status})`);
  }
  return response.json();
}

/** `GET /threads` — the sidebar thread list, sorted by recency client-side. */
export async function listThreads(signal?: AbortSignal): Promise<Thread[]> {
  const response = await fetchWithRefresh(apiUrl("/threads"), {
    method: "GET",
    credentials: "include",
    headers: { accept: "application/json" },
    ...(signal ? { signal } : {}),
  });
  const parsed = threadListResponseSchema.parse(await jsonOrThrow(response, "List threads"));
  return [...parsed.threads].sort(
    (a, b) => Date.parse(b.last_message_at) - Date.parse(a.last_message_at),
  );
}

/** `GET /threads/{id}` — a thread with its active-path history (for rendering persisted turns). */
export async function getThread(threadId: string, signal?: AbortSignal): Promise<Thread> {
  const response = await fetchWithRefresh(apiUrl(`/threads/${encodeURIComponent(threadId)}`), {
    method: "GET",
    credentials: "include",
    headers: { accept: "application/json" },
    ...(signal ? { signal } : {}),
  });
  return threadSchema.parse(await jsonOrThrow(response, "Get thread"));
}

/** `POST /threads` — create an empty thread (the 新對話 button). */
export async function createThread(title = ""): Promise<Thread> {
  const response = await fetchWithRefresh(apiUrl("/threads"), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ title }),
  });
  return threadSchema.parse(await jsonOrThrow(response, "Create thread"));
}

/** `PATCH /threads/{id}` — rename / star / archive / set the per-thread model override. `If-Match`
 * carries the thread's current `version` so a stale edit from another device surfaces as a 409
 * instead of clobbering. */
export async function patchThread(
  threadId: string,
  patch: {
    title?: string;
    starred?: boolean;
    archived?: boolean;
    /** A model id, or null to clear the pick (the caller's default applies again). */
    model_override?: string | null;
    personality_override?: string;
  },
  expectedVersion: number,
): Promise<Thread> {
  const response = await fetchWithRefresh(apiUrl(`/threads/${encodeURIComponent(threadId)}`), {
    method: "PATCH",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "If-Match": String(expectedVersion),
    },
    body: JSON.stringify(patch),
  });
  return threadSchema.parse(await jsonOrThrow(response, "Update thread"));
}

/** `DELETE /threads/{id}` — permanent, no undo. */
export async function deleteThread(threadId: string): Promise<void> {
  const response = await fetchWithRefresh(apiUrl(`/threads/${encodeURIComponent(threadId)}`), {
    method: "DELETE",
    credentials: "include",
    headers: { accept: "application/json" },
  });
  if (!response.ok && response.status !== 404) {
    throw new Error(`Delete thread failed (${response.status})`);
  }
}

/** Multi-select delete (sidebar "Select" mode) — the backend has no batch-delete endpoint, so
 * this fires the existing per-thread `DELETE` in parallel and reports which ids failed, rather
 * than adding a new backend route for what's a rare bulk action. Not atomic: a partial failure
 * leaves some threads deleted and others not — the caller (`RecentsList`) surfaces the failed
 * count and still refetches, so the list reflects exactly what happened, never a stale/optimistic
 * view. */
export async function deleteThreads(threadIds: string[]): Promise<{ failedIds: string[] }> {
  const outcomes = await Promise.allSettled(threadIds.map((id) => deleteThread(id)));
  const failedIds = threadIds.filter((_, i) => outcomes[i]?.status === "rejected");
  return { failedIds };
}

/** `POST /threads/{id}/turns/{tid}/cancel` — stop a running turn (the composer's stop morph). */
export async function cancelTurn(threadId: string, turnId: string): Promise<void> {
  const response = await fetchWithRefresh(
    apiUrl(`/threads/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}/cancel`),
    { method: "POST", credentials: "include", headers: { accept: "application/json" } },
  );
  // A turn that already finished is a 404 here — a no-op for cancel purposes, not an error to surface.
  if (!response.ok && response.status !== 404) {
    throw new Error(`Cancel turn failed (${response.status})`);
  }
}

/** `up`/`down`, mirroring `threads_actions.py::TurnFeedbackRequest.rating`'s pattern-constrained
 * field — the ONLY two values the backend accepts (`extra="forbid"`, no explicit un-vote value). */
export type FeedbackRating = "up" | "down";

export const feedbackRecordSchema = z.object({
  turn_id: z.string(),
  user_id: z.string(),
  rating: z.enum(["up", "down"]),
  comment: z.string().nullable().optional(),
});
export type FeedbackRecord = z.infer<typeof feedbackRecordSchema>;

/** `POST /threads/{id}/turns/{tid}/feedback` — records a 👍/👎 (upserted per `(turn_id, user_id)`,
 * so re-submitting updates the one row rather than accumulating duplicates — mirrors
 * `threads_actions.py::turn_feedback`'s docstring). */
export async function submitTurnFeedback(
  threadId: string,
  turnId: string,
  rating: FeedbackRating,
): Promise<FeedbackRecord> {
  const response = await fetchWithRefresh(
    apiUrl(`/threads/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}/feedback`),
    {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ rating }),
    },
  );
  return feedbackRecordSchema.parse(await jsonOrThrow(response, "Submit feedback"));
}
