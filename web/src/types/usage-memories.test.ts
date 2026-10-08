import { describe, expect, it } from "vitest";
import { memoryUsedDataSchema, turnUsageDataSchema } from "@/types/usage";

describe("memories used", () => {
  it("parses the memory.used frame", () => {
    const parsed = memoryUsedDataSchema.parse({
      memories: [{ id: "m1", key: "focus", type: "preference", text: "高股息 ETF" }],
    });
    expect(parsed.memories[0]?.text).toBe("高股息 ETF");
  });

  it("defaults to none, so a turn that used no memory (or an older stored turn) still parses", () => {
    expect(memoryUsedDataSchema.parse({}).memories).toEqual([]);
    expect(turnUsageDataSchema.parse({ input_tokens: 1 }).memories_used).toEqual([]);
  });

  it("carries the memories on a stored turn's usage", () => {
    const usage = turnUsageDataSchema.parse({
      memories_used: [{ id: "m1", key: "focus", type: "preference" }],
    });
    expect(usage.memories_used).toHaveLength(1);
    expect(usage.memories_used[0]?.text).toBe("");
  });
});
