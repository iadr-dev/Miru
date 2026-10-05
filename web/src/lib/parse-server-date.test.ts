import { describe, expect, it } from "vitest";
import { parseServerDate } from "./parse-server-date";

describe("parseServerDate", () => {
  it("treats a zone-less date-time as UTC, not browser-local", () => {
    expect(parseServerDate("2026-10-03T12:24:12.830555").toISOString()).toBe(
      "2026-10-03T12:24:12.830Z",
    );
  });

  it("leaves values that already carry a zone untouched", () => {
    expect(parseServerDate("2026-10-03T12:24:12Z").toISOString()).toBe("2026-10-03T12:24:12.000Z");
    expect(parseServerDate("2026-10-03T20:24:12+08:00").toISOString()).toBe(
      "2026-10-03T12:24:12.000Z",
    );
    expect(parseServerDate("2026-10-03T20:24:12+0800").toISOString()).toBe(
      "2026-10-03T12:24:12.000Z",
    );
  });

  it("passes date-only strings through without producing an invalid date", () => {
    expect(Number.isNaN(parseServerDate("2026-10-03").getTime())).toBe(false);
  });
});
