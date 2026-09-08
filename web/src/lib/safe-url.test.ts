import { describe, expect, it } from "vitest";
import { safeHttpUrl } from "@/lib/safe-url";

describe("safeHttpUrl", () => {
  it("passes through https URLs", () => {
    expect(safeHttpUrl("https://example.com/a")).toBe("https://example.com/a");
  });

  it("passes through http URLs", () => {
    expect(safeHttpUrl("http://example.com/a")).toBe("http://example.com/a");
  });

  it("rejects javascript: URIs", () => {
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
  });

  it("rejects data: URIs", () => {
    expect(safeHttpUrl("data:text/html,<script>alert(1)</script>")).toBeNull();
  });

  it("rejects unparseable strings", () => {
    expect(safeHttpUrl("not a url")).toBeNull();
  });
});
