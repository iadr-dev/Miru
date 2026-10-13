/**
 * The `POST /agent/query` SSE client.
 *
 * `/agent/query` is a POST that returns `text/event-stream`, so native `EventSource` (GET-only)
 * can't drive it — we read the body as a stream and parse frames by hand, matching
 * `backend/src/api/sse.py::format_sse_event` exactly:
 *
 *     id: <seq>\n
 *     event: <kind>\n
 *     data: <json-envelope>\n
 *     \n                         ← blank line terminates one frame
 *
 * The JSON on the `data:` line is the correlation envelope `{seq, ts, turn_id, agent_id,
 * parent_agent_id, node_id, data}` (the `kind` is the `event:` line, NOT inside the JSON). We
 * re-pair the two and hand the consumer a `{kind, envelope}` per frame. The request body is
 * `{message, thread_id?, web_search?}` — the only fields the real endpoint accepts.
 *
 * Mirrors `backend/src/api/routers/agent.py::AgentQueryRequest` + `api/sse.py`.
 */
import { fetchWithRefresh } from "@/lib/api/auth";
import { apiUrl } from "@/lib/api/config";
import { type SseEnvelope, sseEnvelopeSchema } from "@/types/sse";

/** A non-2xx reply from a turn endpoint. `code` is the backend's stable `ApiError.code`
 * (e.g. `feature_not_entitled`, `chat_limit_reached`) so the UI can
 * explain the refusal in the user's language instead of echoing an English message. */
export class AgentStreamError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null,
  ) {
    super(message);
    this.name = "AgentStreamError";
  }
}

async function errorCodeOf(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "code" in body) {
      const code = (body as { code: unknown }).code;
      return typeof code === "string" ? code : null;
    }
  } catch {
    // not JSON - no code
  }
  return null;
}

export type AgentStreamFrame = {
  kind: string;
  envelope: SseEnvelope;
};

/** One chat attachment as sent to the backend — mirrors `backend/src/api/attachments.py`'s
 * `AttachmentInput` (the whole file inline as base64, not multipart). */
export type AttachmentInput = {
  filename: string;
  media_type: string;
  data_base64: string;
};

export type AgentQueryBody = {
  message: string;
  thread_id?: string;
  web_search?: boolean;
  /** Pro-only cited multi-step web research instead of a normal reply. */
  deep_research?: boolean;
  /** Composer model pick; persisted as the thread override so the first turn honors it. */
  model?: string;
  /** Composer 語氣 pick (a preset id, or "default" = follow the account); persisted as the thread override. */
  personality?: string;
  attachments?: AttachmentInput[];
};

/** Open the turn stream and yield each parsed frame until the connection closes. The caller passes
 * an `AbortSignal` to cancel (the composer's stop control aborts it after firing the cancel POST). */
export async function* streamAgentQuery(
  body: AgentQueryBody,
  signal: AbortSignal,
): AsyncGenerator<AgentStreamFrame> {
  yield* streamSse("/agent/query", body, signal, "Agent query");
}

/** `POST /threads/{id}/turns/{tid}/regenerate` — re-run the source user turn that produced
 * `turnId`'s assistant answer, streaming a NEW sibling assistant turn through the exact same SSE
 * frame vocabulary as `/agent/query` (mirrors `backend/src/api/routers/threads_actions.py::
 * RegenerateRequest`/`regenerate_turn`). `turnId` here is the ASSISTANT turn being regenerated. */
export function streamRegenerateTurn(
  threadId: string,
  turnId: string,
  body: { web_search?: boolean; locale?: string },
  signal: AbortSignal,
): AsyncGenerator<AgentStreamFrame> {
  return streamSse(
    `/threads/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}/regenerate`,
    body,
    signal,
    "Regenerate turn",
  );
}

/** `POST /threads/{id}/turns/{tid}/edit` — edit a user turn's text and resend, streaming the new
 * assistant answer through the same SSE frame vocabulary (mirrors `threads_actions.py::
 * EditTurnRequest`/`edit_turn`). `turnId` here is the USER turn being edited. */
export function streamEditTurn(
  threadId: string,
  turnId: string,
  body: {
    message: string;
    web_search?: boolean;
    locale?: string;
    attachments?: AttachmentInput[];
  },
  signal: AbortSignal,
): AsyncGenerator<AgentStreamFrame> {
  return streamSse(
    `/threads/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}/edit`,
    body,
    signal,
    "Edit turn",
  );
}

/** Shared POST-then-read-SSE-body loop behind `streamAgentQuery`/`streamRegenerateTurn`/
 * `streamEditTurn` — all three hit a `text/event-stream` POST endpoint emitting the same
 * correlation-envelope frame shape, so the fetch/parse/abort plumbing lives once here. */
async function* streamSse(
  path: string,
  body: unknown,
  signal: AbortSignal,
  errorContext: string,
): AsyncGenerator<AgentStreamFrame> {
  const response = await fetchWithRefresh(apiUrl(path), {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || response.body === null) {
    throw new AgentStreamError(
      `${errorContext} failed (${response.status})`,
      response.status,
      await errorCodeOf(response),
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      // Frames are separated by a blank line. Normalize CRLF just in case a proxy rewrites it.
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const rawFrame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const frame = parseFrame(rawFrame);
        if (frame !== null) {
          yield frame;
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    // Releasing the lock lets an abort tear the underlying connection down promptly.
    reader.releaseLock();
  }
}

/** Parse one raw SSE frame (its lines, sans the trailing blank line) into a typed frame. Returns
 * `null` for a frame we can't use (no `event:` line, or a `data:` payload that fails validation) —
 * a malformed frame is skipped, never silently coerced into a wrong shape. */
function parseFrame(rawFrame: string): AgentStreamFrame | null {
  let kind: string | null = null;
  const dataLines: string[] = [];

  for (const line of rawFrame.split("\n")) {
    const normalized = line.endsWith("\r") ? line.slice(0, -1) : line;
    if (normalized.startsWith(":")) {
      continue; // SSE comment / keep-alive
    }
    if (normalized.startsWith("event:")) {
      kind = normalized.slice("event:".length).trim();
    } else if (normalized.startsWith("data:")) {
      dataLines.push(normalized.slice("data:".length).replace(/^ /, ""));
    }
    // `id:` is echoed as the envelope `seq`; we read it from the JSON, so the line itself is ignored.
  }

  if (kind === null || dataLines.length === 0) {
    return null;
  }

  try {
    const parsed = sseEnvelopeSchema.safeParse(JSON.parse(dataLines.join("\n")));
    if (!parsed.success) {
      return null;
    }
    return { kind, envelope: parsed.data };
  } catch {
    return null;
  }
}
