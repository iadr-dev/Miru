import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  getModelCatalog,
  getModelDefault,
  ModelsApiError,
  putModelDefault,
} from "@/lib/api/models";

const CATALOG = {
  selectable: true,
  models: [
    {
      provider: "anthropic",
      id: "anthropic/claude-sonnet-5-5",
      label_zh: "Claude Sonnet 5.5",
      label_en: "Claude Sonnet 5.5",
      is_default: true,
    },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("model catalog client", () => {
  it("parses GET /models", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(CATALOG));
    const catalog = await getModelCatalog();
    expect(catalog.selectable).toBe(true);
    expect(catalog.models[0]?.is_default).toBe(true);
  });

  it("rejects a malformed catalog instead of rendering it half-parsed", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      json({ selectable: true, models: [{ id: "x", provider: "nope" }] }),
    );
    await expect(getModelCatalog()).rejects.toThrow();
  });

  it("parses GET /me/model-default, including no saved default", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ default_model: null }));
    expect((await getModelDefault()).default_model).toBeNull();
  });

  it("PUT sends the model, and null to clear it", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({ default_model: "openai/gpt-5.4" }))
      .mockResolvedValueOnce(json({ default_model: null }));
    await putModelDefault("openai/gpt-5.4");
    await putModelDefault(null);
    const bodies = vi.mocked(fetch).mock.calls.map(([, init]) => init?.body);
    expect(bodies).toEqual([
      JSON.stringify({ default_model: "openai/gpt-5.4" }),
      JSON.stringify({ default_model: null }),
    ]);
    expect(vi.mocked(fetch).mock.calls[0]?.[1]?.method).toBe("PUT");
  });

  it("surfaces the backend error code (403 byok_required)", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      json({ code: "byok_required", message: "Add your own key first.", retryable: false }, 403),
    );
    const error = await putModelDefault("openai/gpt-5.4").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ModelsApiError);
    expect((error as ModelsApiError).code).toBe("byok_required");
  });
});
