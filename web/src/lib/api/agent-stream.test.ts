import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentStreamError, newIdempotencyKey, streamAgentQuery } from "@/lib/api/agent-stream";

function sentKeys(): string[] {
  return vi.mocked(fetch).mock.calls.map(([, init]) => {
    const headers = (init as RequestInit).headers as Record<string, string>;
    return headers["idempotency-key"] ?? "";
  });
}

async function drain(message: string): Promise<void> {
  for await (const _ of streamAgentQuery({ message }, new AbortController().signal)) {
    // no frames expected from an empty body
  }
}

describe("streamAgentQuery idempotency key", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends a key with each message, and a different one for the next message", async () => {
    vi.mocked(fetch).mockImplementation(async () => new Response("", { status: 200 }));

    await drain("hello");
    await drain("hello");

    const [first, second] = sentKeys();
    expect(first).toMatch(/^[\w-]{8,}$/);
    expect(second).toMatch(/^[\w-]{8,}$/);
    expect(first).not.toBe(second);
  });

  it("turns the server's duplicate refusal into a typed error carrying the thread", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({ code: "duplicate_request", message: "dup", retryable: false }),
        {
          status: 409,
          headers: { "content-type": "application/json", "x-thread-id": "thread-7" },
        },
      ),
    );

    const error = await drain("hello").then(
      () => null,
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(AgentStreamError);
    expect((error as AgentStreamError).status).toBe(409);
    expect((error as AgentStreamError).code).toBe("duplicate_request");
    expect((error as AgentStreamError).threadId).toBe("thread-7");
  });
});

describe("newIdempotencyKey", () => {
  it("falls back to a random string where crypto.randomUUID is missing", () => {
    vi.stubGlobal("crypto", {});
    const key = newIdempotencyKey();
    vi.unstubAllGlobals();

    expect(key.length).toBeGreaterThanOrEqual(16);
    expect(key).toMatch(/^[\w-]+$/);
  });
});
