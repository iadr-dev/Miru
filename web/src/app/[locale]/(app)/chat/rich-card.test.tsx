// @vitest-environment jsdom
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { RichCardView } from "@/app/[locale]/(app)/chat/rich-card";
import { renderWithIntl } from "@/test/render";

afterEach(cleanup);

function html(card_type: string, data: Record<string, unknown>): string {
  return renderWithIntl(<RichCardView card={{ card_type, data }} />).container.innerHTML;
}

const EMPTY_CHIP_FLOWS = {
  total_volume: 0,
  main_force_net: 0,
  free_row_count: 0,
  buy_top: [],
  sell_top: [],
  rows: [],
};

describe("RichCardView empty results", () => {
  it.each(["unavailable-tier", "unreachable", "unavailable"])(
    "shows no card of default zeros when the source reports %s",
    (health_status) => {
      expect(html("chip_flows", { ...EMPTY_CHIP_FLOWS, health_status })).toBe("");
    },
  );

  it("still shows the flat stats when the source is healthy but returned no rows", () => {
    const out = html("chip_flows", { ...EMPTY_CHIP_FLOWS, health_status: "ok" });
    expect(out).toContain("0");
    expect(out).not.toBe("");
  });

  it("keeps an unmapped card type with stats rendering as before", () => {
    const out = html("something_new", { total_volume: 7, health_status: "unavailable-tier" });
    expect(out).toContain("7");
  });
});

describe("RichCardView technical_indicators with chart patterns", () => {
  const CHART_PATTERNS = [
    {
      pattern: "double_top",
      family: "reversal_top",
      status: "confirmed",
      pivots: [],
      lines: [],
      height: 12.5,
    },
  ];

  it("keeps rendering the flat stats and ignores the nested chart_patterns array", () => {
    const out = html("technical_indicators", {
      symbol: "2330",
      market: "TW",
      data_available: true,
      chart_patterns: CHART_PATTERNS,
      non_trading_caveat: "indicators describe the chart",
    });
    expect(out).toContain("2330");
    expect(out).not.toContain("double_top");
    expect(out).not.toContain("chart_patterns");
  });

  it("does not break when the array is empty", () => {
    const out = html("technical_indicators", { symbol: "2330", chart_patterns: [] });
    expect(out).toContain("2330");
  });
});
