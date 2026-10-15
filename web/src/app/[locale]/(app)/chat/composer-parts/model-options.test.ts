import { describe, expect, it } from "vitest";
import { MODEL_CATEGORIES, modelOptionFor } from "./model-options";

const ids = MODEL_CATEGORIES.flatMap((category) => category.models.map((m) => m.id));

describe("the Claude models in the picker", () => {
  it("lists the 5.x line and not the retired Sonnet 4.6 and Haiku 4.5", () => {
    expect(ids).toContain("anthropic/claude-opus-5-5");
    expect(ids).toContain("anthropic/claude-sonnet-5-5");
    expect(ids).toContain("anthropic/claude-haiku-5-5");
    expect(ids).not.toContain("anthropic/claude-sonnet-4-6");
    expect(ids).not.toContain("anthropic/claude-haiku-4-5-20251001");
  });
});

describe("modelOptionFor", () => {
  it("returns null when the thread picked no model", () => {
    expect(modelOptionFor(null)).toBeNull();
  });

  it("names a model that has left the list as a person would, not by its raw id", () => {
    expect(modelOptionFor("anthropic/claude-sonnet-4-6")).toEqual({
      id: "anthropic/claude-sonnet-4-6",
      label: "Claude Sonnet 4.6",
    });
    expect(modelOptionFor("openrouter/some/unlisted-model")?.label).toBe("some/unlisted-model");
  });
});
