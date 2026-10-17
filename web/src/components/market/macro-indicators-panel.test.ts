import { describe, expect, it } from "vitest";
import type { CentralBankRateRow } from "@/types/market/macro";
import { latestFedRate } from "./macro-indicators-panel";

const row = (country: string, date: string, rate: number): CentralBankRateRow => ({
  symbol: country,
  market: "GLOBAL",
  date,
  country,
  full_country_name: country,
  interest_rate: rate,
});

describe("latestFedRate", () => {
  it("picks the newest Fed reading, not the first one in the feed", () => {
    const rows = [
      row("FED", "2025-01-29", 4.5),
      row("ECB", "2026-09-10", 2),
      row("FED", "2026-09-17", 3.75),
    ];
    expect(latestFedRate(rows)?.interest_rate).toBe(3.75);
  });

  it("is undefined when the feed has no Fed row", () => {
    expect(latestFedRate([row("ECB", "2026-09-10", 2)])).toBeUndefined();
  });
});
