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
  const base = {
    id: "txn_1",
    status: "completed",
    created_at: "2026-10-04T10:00:00Z",
    billed_at: "2026-10-05T12:00:00Z",
    currency_code: "USD",
    total: "1099",
  };

  it("reads the date, grand total and status from the transaction", () => {
    const summary = summarizeTransaction(base, "en-US");
    expect(summary).toMatchObject({ id: "txn_1", status: "completed", amount: "$10.99" });
    expect(summary.date).toContain("2026");
  });

  it("falls back to the creation date when the transaction was never billed", () => {
    const summary = summarizeTransaction({ ...base, billed_at: null }, "en-US");
    expect(summary.date).toContain("2026");
  });

  it("shows no amount or date when Paddle sent none", () => {
    expect(
      summarizeTransaction(
        { ...base, billed_at: null, created_at: null, total: null, currency_code: null },
        "en-US",
      ),
    ).toEqual({ id: "txn_1", date: null, amount: null, status: "completed" });
  });
});
