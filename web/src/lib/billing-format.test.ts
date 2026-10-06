import { describe, expect, it } from "vitest";
import {
  formatDate,
  formatMinorAmount,
  isBillableTransaction,
  summarizeTransaction,
} from "./billing-format";

describe("formatMinorAmount", () => {
  it("turns Paddle's minor units into a currency amount", () => {
    expect(formatMinorAmount("1099", "USD", "en-US")).toBe("$10.99");
    expect(formatMinorAmount("29900", "TWD", "zh-TW")).toContain("299");
  });

  it("respects currencies with no minor unit", () => {
    expect(formatMinorAmount("1500", "JPY", "en-US")).toContain("1,500");
  });

  it("falls back to the raw values instead of throwing", () => {
    expect(formatMinorAmount("not-a-number", "USD", "en-US")).toBe("not-a-number USD");
    expect(formatMinorAmount("1099", "NOPE1", "en-US")).toBe("1099 NOPE1");
  });
});

describe("formatDate", () => {
  it("formats an ISO date and tolerates missing or bad input", () => {
    expect(formatDate("2026-10-05T12:00:00Z", "en-US")).toContain("2026");
    expect(formatDate(null, "en-US")).toBeNull();
    expect(formatDate("garbage", "en-US")).toBeNull();
  });
});

describe("isBillableTransaction", () => {
  it("hides checkouts that were opened but never paid or were canceled", () => {
    expect(isBillableTransaction({ status: "ready" })).toBe(false);
    expect(isBillableTransaction({ status: "draft" })).toBe(false);
    expect(isBillableTransaction({ status: "canceled" })).toBe(false);
    for (const status of ["billed", "paid", "completed", "past_due"]) {
      expect(isBillableTransaction({ status })).toBe(true);
    }
  });
});

describe("summarizeTransaction", () => {
  it("reads the date, grand total and status from a Paddle transaction", () => {
    const summary = summarizeTransaction(
      {
        id: "txn_1",
        status: "completed",
        currency_code: "USD",
        billed_at: "2026-10-05T12:00:00Z",
        details: { totals: { grand_total: "1099" } },
      },
      0,
      "en-US",
    );
    expect(summary).toMatchObject({
      key: "txn_1",
      id: "txn_1",
      status: "completed",
      amount: "$10.99",
    });
    expect(summary.date).toContain("2026");
  });

  it("never throws on a sparse transaction", () => {
    expect(summarizeTransaction({}, 3, "en-US")).toEqual({
      key: "tx-3",
      id: null,
      date: null,
      amount: null,
      status: null,
    });
  });
});
