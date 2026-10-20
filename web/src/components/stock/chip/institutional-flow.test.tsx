// @vitest-environment jsdom
import { cleanup, fireEvent } from "@testing-library/react";
import { cloneElement, type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InstitutionalFlowBody } from "@/components/stock/chip/institutional-flow";
import { renderWithIntl } from "@/test/render";

// jsdom has no layout, so ResponsiveContainer would render nothing; give the chart a fixed size.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({
      children,
    }: {
      children: ReactElement<{ width?: number; height?: number }>;
    }) => cloneElement(children, { width: 600, height: 300 }),
  };
});
vi.mock("@/hooks/use-stock-data", () => ({
  useInstitutionalFlow: () => ({
    isLoading: false,
    isError: false,
    data: {
      rows: [
        {
          symbol: "2330",
          market: "TW",
          trade_date: "2026-01-02",
          investor: "Foreign_Investor",
          buy: 5000,
          sell: 1000,
        },
        {
          symbol: "2330",
          market: "TW",
          trade_date: "2026-01-02",
          investor: "Investment_Trust",
          buy: 300,
          sell: 100,
        },
      ],
    },
  }),
  usePriceHistory: () => ({ data: { rows: [{ trade_date: "2026-01-02", close: 100 }] } }),
}));

afterEach(cleanup);

function legendButtons(container: HTMLElement): HTMLButtonElement[] {
  return [...container.querySelectorAll<HTMLButtonElement>("ul button[type=button]")];
}

describe("InstitutionalFlowBody legend", () => {
  it("renders a toggle for each bucket plus price and greys it out when hidden", () => {
    const { container } = renderWithIntl(<InstitutionalFlowBody symbol="2330" market="TW" />);
    const buttons = legendButtons(container);
    expect(buttons.map((b) => b.textContent)).toEqual(["外資", "投信", "自營商", "收盤價"]);
    const first = buttons[0];
    if (!first) throw new Error("legend button missing");
    expect(first.getAttribute("style")).toContain("var(--text)");
    fireEvent.click(first);
    expect(legendButtons(container)[0]?.getAttribute("style")).toContain("var(--text-muted)");
  });

  it("keeps the same legend button element, and its focus, after toggling", () => {
    const { container } = renderWithIntl(<InstitutionalFlowBody symbol="2330" market="TW" />);
    const first = legendButtons(container)[0];
    if (!first) throw new Error("legend button missing");
    first.focus();
    fireEvent.click(first);
    expect(legendButtons(container)[0]).toBe(first);
    expect(document.activeElement).toBe(first);
  });
});
