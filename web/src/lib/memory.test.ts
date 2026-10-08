import { describe, expect, it } from "vitest";
import { contentFromText, keyFromText, memoryText, sourceThreadId } from "@/lib/memory";

describe("memoryText", () => {
  it("reads the text field", () => {
    expect(memoryText({ content: { text: "高股息 ETF" } })).toBe("高股息 ETF");
  });

  it("falls back to the readable values of an older free-form memory", () => {
    expect(memoryText({ content: { level: "concise", n: 3, nested: { a: 1 } } })).toBe(
      "level: concise\nn: 3",
    );
  });

  it("is empty for an empty memory", () => {
    expect(memoryText({ content: {} })).toBe("");
  });
});

describe("contentFromText / keyFromText", () => {
  it("trims the text it stores", () => {
    expect(contentFromText("  長期  ")).toEqual({ text: "長期" });
  });

  it("derives a short single-line name", () => {
    expect(keyFromText("我主要關注\n高股息 ETF")).toBe("我主要關注 高股息 ETF");
    const long = "字".repeat(60);
    expect(keyFromText(long)).toBe(`${"字".repeat(40)}…`);
  });
});

describe("sourceThreadId", () => {
  it("extracts a chat reference and ignores anything else", () => {
    expect(sourceThreadId({ source_ref: "thread:abc" })).toBe("abc");
    expect(sourceThreadId({ source_ref: "turn:abc" })).toBeNull();
    expect(sourceThreadId({ source_ref: "thread:" })).toBeNull();
    expect(sourceThreadId({ source_ref: null })).toBeNull();
  });
});
