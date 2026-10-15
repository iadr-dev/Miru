import { describe, expect, it } from "vitest";
import { financialStatementItemLabel } from "./financial-statement-labels";

describe("financialStatementItemLabel", () => {
  it("uses the Chinese dictionary for zh locales", () => {
    expect(financialStatementItemLabel("GrossProfit", "zh-TW")).toBe("營業毛利");
    expect(financialStatementItemLabel("GrossProfit_per", "zh-TW")).toBe("營業毛利（占營收比）");
  });

  it("reads the code as English words for other locales, never Chinese", () => {
    expect(financialStatementItemLabel("GrossProfit", "en")).toBe("Gross Profit");
    expect(financialStatementItemLabel("GrossProfit_per", "en")).toBe(
      "Gross Profit (% of revenue)",
    );
  });
});
