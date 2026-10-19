import { describe, expect, it } from "vitest";
import { defaultModel, groupByProvider, labelForModelId, modelLabel } from "@/lib/model-catalog";
import type { ModelOption } from "@/types/models";

const SONNET: ModelOption = {
  provider: "anthropic",
  id: "anthropic/claude-sonnet-5-5",
  label_zh: "Claude Sonnet 5.5（均衡）",
  label_en: "Claude Sonnet 5.5",
  is_default: true,
};
const GPT: ModelOption = {
  provider: "openai",
  id: "openai/gpt-5.4",
  label_zh: "GPT-5.4",
  label_en: "GPT-5.4",
  is_default: false,
};
const OPUS: ModelOption = { ...SONNET, id: "anthropic/claude-opus-5-5", is_default: false };

describe("model catalog helpers", () => {
  it("labels by locale", () => {
    expect(modelLabel(SONNET, "zh-TW")).toBe("Claude Sonnet 5.5（均衡）");
    expect(modelLabel(SONNET, "en")).toBe("Claude Sonnet 5.5");
  });

  it("groups by provider in first-seen order", () => {
    const groups = groupByProvider([SONNET, GPT, OPUS]);
    expect(groups.map((g) => g.provider)).toEqual(["anthropic", "openai"]);
    expect(groups[0]?.models.map((m) => m.id)).toEqual([SONNET.id, OPUS.id]);
  });

  it("finds the main model, or null", () => {
    expect(defaultModel([GPT, SONNET])?.id).toBe(SONNET.id);
    expect(defaultModel([GPT])).toBeNull();
  });

  it("names a retired model as a person would, not by raw id", () => {
    expect(labelForModelId(GPT.id, [GPT], "en")).toBe("GPT-5.4");
    expect(labelForModelId("openrouter/some/unlisted-model", [GPT], "en")).toBe(
      "some/unlisted-model",
    );
  });
});
