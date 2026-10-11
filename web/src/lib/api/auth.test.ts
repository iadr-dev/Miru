import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DELETE_ACCOUNT_CONFIRMATION, deleteAccount, fetchWithRefresh } from "@/lib/api/auth";

describe("fetchWithRefresh", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the first response unchanged when it isn't a 401", async () => {
    const ok = new Response(null, { status: 200 });
    vi.mocked(fetch).mockResolvedValueOnce(ok);

    const response = await fetchWithRefresh("/x", { credentials: "include" });

    expect(response).toBe(ok);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("retries once via /auth/refresh after a 401, then returns the retried response", async () => {
    const retried = new Response(null, { status: 200 });
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // original request
      .mockResolvedValueOnce(new Response(null, { status: 200 })) // POST /auth/refresh
      .mockResolvedValueOnce(retried); // retried request

    const response = await fetchWithRefresh("/x", { credentials: "include" });

    expect(response).toBe(retried);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("gives back the original 401 when the refresh itself fails", async () => {
    const original = new Response(null, { status: 401 });
    vi.mocked(fetch)
      .mockResolvedValueOnce(original) // original request
      .mockResolvedValueOnce(new Response(null, { status: 401 })); // POST /auth/refresh fails

    const response = await fetchWithRefresh("/x", { credentials: "include" });

    expect(response).toBe(original);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("refreshes and retries when a 200 is flagged X-Auth-Expired (expired token served anonymously)", async () => {
    const retried = new Response(null, { status: 200 });
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        new Response(null, { status: 200, headers: { "X-Auth-Expired": "1" } }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 })) // POST /auth/refresh
      .mockResolvedValueOnce(retried);

    const response = await fetchWithRefresh("/x", { credentials: "include" });

    expect(response).toBe(retried);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("keeps the anonymous answer when the flagged refresh fails (a logged-out visitor)", async () => {
    const anonymous = new Response(null, { status: 200, headers: { "X-Auth-Expired": "1" } });
    vi.mocked(fetch)
      .mockResolvedValueOnce(anonymous)
      .mockResolvedValueOnce(new Response(null, { status: 401 })); // POST /auth/refresh fails

    const response = await fetchWithRefresh("/x", { credentials: "include" });

    expect(response).toBe(anonymous);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe("deleteAccount", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the confirmation word to DELETE /me and resolves ok", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{"deleted":true}', { status: 200 }));

    const result = await deleteAccount();

    expect(result).toEqual({ ok: true });
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/me$/);
    expect(init.method).toBe("DELETE");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(init.body as string)).toEqual({ confirm: DELETE_ACCOUNT_CONFIRMATION });
  });

  it("surfaces the backend error code so the dialog can explain a failed billing cancel", async () => {
    const body = { code: "billing_cancel_failed", message: "retry", retryable: true };
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(body), { status: 502 }));

    const result = await deleteAccount();

    expect(result).toEqual({
      ok: false,
      code: "billing_cancel_failed",
      message: "retry",
      retryable: true,
    });
  });

  it("reports a network failure as retryable instead of throwing", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("offline"));

    const result = await deleteAccount();

    expect(result).toMatchObject({ ok: false, code: "network_error", retryable: true });
  });
});

describe("rate-limited reads", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const limited = (seconds: string): Response =>
    new Response(null, { status: 429, headers: { "retry-after": seconds } });

  it("waits out Retry-After and retries a GET", async () => {
    const ok = new Response("{}", { status: 200 });
    vi.mocked(fetch).mockResolvedValueOnce(limited("2")).mockResolvedValueOnce(ok);

    const pending = fetchWithRefresh("/x", { credentials: "include" });
    await vi.advanceTimersByTimeAsync(2_500);

    expect(await pending).toBe(ok);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("does not retry a write, which may already have taken effect", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(limited("1"));

    const response = await fetchWithRefresh("/x", { method: "POST" });

    expect(response.status).toBe(429);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("hands a long wait back to the caller instead of freezing the page", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(limited("45"));

    const response = await fetchWithRefresh("/x", { credentials: "include" });

    expect(response.status).toBe(429);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("gives up after two retries", async () => {
    vi.mocked(fetch).mockImplementation(() => Promise.resolve(limited("1")));

    const pending = fetchWithRefresh("/x", { credentials: "include" });
    await vi.advanceTimersByTimeAsync(5_000);

    expect((await pending).status).toBe(429);
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
