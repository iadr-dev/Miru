import { describe, expect, it } from "vitest";
import { providerStyle } from "@/lib/provider-style";

describe("providerStyle", () => {
  it("gives each known provider a distinct colour token and a display name", () => {
    const ids = ["anthropic", "openai", "gemini", "openrouter", "local"];
    const colors = ids.map((id) => providerStyle(id).color);
    expect(new Set(colors).size).toBe(ids.length);
    expect(providerStyle("openai").label).toBe("OpenAI");
  });

  it("labels the local provider as Docker Model with the muted token", () => {
    expect(providerStyle("local")).toEqual({ label: "Docker Model", color: "var(--text-muted)" });
  });

  it("draws a provider id it no longer knows under its own id", () => {
    expect(providerStyle("retired-provider").label).toBe("retired-provider");
    expect(providerStyle("retired-provider").color.startsWith("var(--chart-")).toBe(true);
  });

  it("never returns a literal colour", () => {
    for (const id of ["anthropic", "local", "some-new-provider"]) {
      expect(providerStyle(id).color.startsWith("var(--")).toBe(true);
    }
  });

  it("is stable for an unknown provider and keeps its id as the label", () => {
    expect(providerStyle("acme")).toEqual(providerStyle("acme"));
    expect(providerStyle("acme").label).toBe("acme");
  });
});
