// @vitest-environment jsdom
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SnapshotCard } from "@/app/[locale]/(app)/chat/cards/snapshot-card";
import { renderWithIntl } from "@/test/render";

afterEach(cleanup);

function html(cardType: string, data: unknown): string {
  return renderWithIntl(<SnapshotCard card_type={cardType} data={data} />).container.innerHTML;
}

describe("SnapshotCard empty and unavailable results", () => {
  it("shows the server's message when the result was not found", () => {
    const out = html("stock_score", { found: false, message: "尚未計算評分" });
    expect(out).toContain("尚未計算評分");
    expect(out).toMatchSnapshot();
  });

  it("shows nothing when the result was not found and there is no usable message", () => {
    expect(html("stock_score", { found: false })).toBe("");
    expect(html("stock_score", { found: false, message: "" })).toBe("");
    expect(html("stock_score", { found: false, message: 5 })).toBe("");
  });

  it("shows nothing for missing or non-object data", () => {
    expect(html("price_quote", null)).toBe("");
    expect(html("price_quote", undefined)).toBe("");
    expect(html("price_quote", "text")).toBe("");
    expect(html("price_quote", 7)).toBe("");
  });

  it("does not treat found: true or an absent found as unavailable", () => {
    expect(html("price_quote", { found: true, bar: { close: 1 } })).not.toBe("");
    expect(html("price_quote", { bar: { close: 1 } })).not.toBe("");
  });
});

describe("SnapshotCard entity unwrapping", () => {
  it.each(["bar", "snapshot", "info", "result", "profile", "consensus", "esg"])(
    "unwraps the %s object",
    (key) => {
      const out = html("price_quote", { [key]: { symbol: "2330", close: 100 } });
      expect(out).toContain("2330");
      expect(out).toContain("100");
    },
  );

  it("uses the first present nested object in key order", () => {
    const out = html("price_quote", {
      info: { symbol: "INFO", close: 2 },
      bar: { symbol: "BAR", close: 1 },
    });
    expect(out).toContain("BAR");
    expect(out).not.toContain("INFO");
  });

  it("skips a nested value that is not an object and falls back to the top level", () => {
    const out = html("company_profile", { bar: "x", symbol: "2330", industry: "半導體" });
    expect(out).toContain("2330");
    expect(out).toContain("半導體");
  });

  it("reads a top-level entity when there is no wrapper", () => {
    expect(html("company_profile", { symbol: "2330", employees: 1234 })).toMatchSnapshot();
  });
});

describe("SnapshotCard header", () => {
  it("titles the card by symbol, else stock_id, else the card type label", () => {
    const withSymbol = html("price_quote", { bar: { symbol: "2330", close: 1 } });
    expect(withSymbol).toContain("2330");
    const withStockId = html("price_quote", { bar: { stock_id: "2317", close: 1 } });
    expect(withStockId).toContain("2317");
    const neither = html("price_quote", { bar: { close: 1 } });
    // The card type label appears twice: as the title and as the trailing tag.
    expect(neither.split("text-[11px]").length).toBeGreaterThan(1);
    expect(neither).toMatchSnapshot();
  });
});

describe("SnapshotCard stat grid", () => {
  it("renders strings, numbers and booleans, in order, and drops everything else", () => {
    const out = html("company_profile", {
      symbol: "2330",
      market: "TW",
      found: true,
      missing_fields: [],
      industry: "半導體",
      employees: 1234.5,
      listed: true,
      nested: { a: 1 },
      list: [1, 2],
      nothing: null,
    });
    expect(out).not.toContain("nested");
    expect(out).toMatchSnapshot();
  });

  it("shows large monetary fields compactly with the full figure as a title", () => {
    const out = html("company_profile", {
      symbol: "2330",
      market_value: 19_419_932_400,
      eps: 12_345.678,
    });
    expect(out).toMatchSnapshot();
    expect(out).toContain('title="19,419,932,400"');
  });

  it("gives plain numbers no title", () => {
    const out = html("company_profile", { symbol: "2330", eps: 12.5 });
    expect(out).not.toContain("title=");
  });

  it("lists unavailable fields as chips, ignoring non-string entries", () => {
    const out = html("company_profile", {
      symbol: "2330",
      industry: "半導體",
      missing_fields: ["eps", 5, "market_value"],
    });
    expect(out).toMatchSnapshot();
  });

  it("treats a non-array missing_fields as none", () => {
    expect(html("company_profile", { symbol: "2330", missing_fields: "eps" })).not.toContain(
      "rounded-[var(--radius-pill)] bg-[var(--surface-2)]",
    );
  });
});

describe("SnapshotCard five-tier depth", () => {
  const live = {
    symbol: "2330",
    last_price: 100,
    best_bid: "99.5_99_98.5_98_97.5",
    best_bid_volume: "10_20_30_40_50",
    best_ask: "100_100.5_101_101.5_102",
    best_ask_volume: "5_15_25_35_45",
  };

  it("draws bid and ask ladders sized by volume, and keeps the raw strings out of the grid", () => {
    const out = html("live_quote_snapshot", { snapshot: live });
    expect(out.match(/role="img"/g)).toHaveLength(10);
    expect(out).not.toContain("99.5_99");
    expect(out).toMatchSnapshot();
  });

  it("keeps only tiers with a real positive price and pads missing volumes with zero", () => {
    const out = html("live_quote_snapshot", {
      snapshot: {
        symbol: "2330",
        best_bid: "99_-_0_98",
        best_bid_volume: "10_5_5",
        best_ask: "100",
        best_ask_volume: "",
      },
    });
    expect(out.match(/role="img"/g)).toHaveLength(3);
    expect(out).toMatchSnapshot();
  });

  it("draws the chart when only one side is quoted as a string", () => {
    const onlyBid = html("live_quote_snapshot", {
      snapshot: { symbol: "2330", best_bid: "99", best_bid_volume: "10" },
    });
    expect(onlyBid.match(/role="img"/g)).toHaveLength(1);
    const onlyAsk = html("live_quote_snapshot", {
      snapshot: { symbol: "2330", best_ask: "101", best_ask_volume: "10" },
    });
    expect(onlyAsk.match(/role="img"/g)).toHaveLength(1);
  });

  it("draws nothing when no tier has a price, and not at all without the fields", () => {
    const empty = html("live_quote_snapshot", {
      snapshot: { symbol: "2330", best_bid: "-_-", best_bid_volume: "0_0", best_ask: "" },
    });
    expect(empty).not.toContain('role="img"');
    const none = html("live_quote_snapshot", { snapshot: { symbol: "2330", last_price: 1 } });
    expect(none).not.toContain('role="img"');
    const nonString = html("live_quote_snapshot", {
      snapshot: { symbol: "2330", best_bid: 99, best_ask: 100 },
    });
    expect(nonString).not.toContain('role="img"');
  });
});
