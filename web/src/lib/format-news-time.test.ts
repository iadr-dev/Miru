import { describe, expect, it } from "vitest";
import { formatNewsTime } from "./format-news-time";

const NOW = new Date("2026-10-27T12:30:00Z");

describe("formatNewsTime", () => {
  it("says 'now' for the last few seconds", () => {
    expect(formatNewsTime("2026-10-27T12:29:50Z", "en", NOW).label).toBe("now");
  });

  it("uses minutes inside the first hour", () => {
    expect(formatNewsTime("2026-10-27T12:10:00Z", "en", NOW).label).toBe("20 minutes ago");
    expect(formatNewsTime("2026-10-27T12:10:00Z", "zh-TW", NOW).label).toBe("20 分鐘前");
  });

  it("uses hours inside the first day", () => {
    expect(formatNewsTime("2026-10-27T09:00:00Z", "en", NOW).label).toBe("3 hours ago");
  });

  it("shows a short date and time once it is a day old", () => {
    const { label } = formatNewsTime("2026-10-25T12:10:05Z", "en", NOW);
    expect(label).toMatch(/^\d{1,2}\/\d{1,2}[, ]+\d{2}:\d{2}$/);
  });

  it("never shows a future item as 'in N minutes'", () => {
    const { label } = formatNewsTime("2026-10-27T13:30:00Z", "en", NOW);
    expect(label).not.toMatch(/in \d/);
  });

  it("keeps the full local date and time for the tooltip", () => {
    const { full } = formatNewsTime("2026-10-27T12:10:05Z", "en", NOW);
    expect(full).toContain("2026");
  });

  it("returns an unparsable value unchanged", () => {
    expect(formatNewsTime("not a date", "en", NOW)).toEqual({
      label: "not a date",
      full: "not a date",
    });
  });

  it("reads a zone-less server value as UTC", () => {
    expect(formatNewsTime("2026-10-27T12:10:00", "en", NOW).label).toBe("20 minutes ago");
  });
});
