import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchWithRefresh } from "@/lib/api/auth";

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
