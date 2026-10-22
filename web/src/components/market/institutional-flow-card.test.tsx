// @vitest-environment jsdom
import { cleanup, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InstitutionalFlowCard } from "@/components/market/institutional-flow-card";
import { renderWithIntl } from "@/test/render";
import type { InstitutionalFlowTotalMarketRow } from "@/types/market/flows";

vi.mock("recharts", async (importOriginal) =>
  (await import("@/test/fixed-size-recharts")).withFixedSizeContainer(
    await importOriginal<typeof import("recharts")>(),
  ),
);

afterEach(cleanup);

function flow(name: string, buy: number, sell: number): InstitutionalFlowTotalMarketRow {
  return { market: "TW", trade_date: "2026-01-02", name, buy, sell };
}

const rows = [
  flow("Foreign_Investor", 500, 100),
  flow("Investment_Trust", 50, 10),
  flow("Dealer_self", 20, 30),
  flow("Dealer_Hedging", 5, 5),
  flow("total", 575, 145),
];

function legendButtons(container: HTMLElement): HTMLButtonElement[] {
  return [...container.querySelectorAll<HTMLButtonElement>("ul button[type=button]")];
}

describe("InstitutionalFlowCard legend", () => {
  it("renders one toggle per chart category and greys it out when hidden", () => {
    const { container } = renderWithIntl(<InstitutionalFlowCard rows={rows} />);
    const buttons = legendButtons(container);
    expect(buttons).toHaveLength(4);
    const first = buttons[0];
    if (!first) throw new Error("legend button missing");
    expect(first.getAttribute("style")).toContain("var(--text)");
    fireEvent.click(first);
    expect(legendButtons(container)[0]?.getAttribute("style")).toContain("var(--text-muted)");
    fireEvent.click(legendButtons(container)[0] as HTMLButtonElement);
    expect(legendButtons(container)[0]?.getAttribute("style")).toContain("var(--text)");
  });

  it("keeps the same legend button element, and its focus, after toggling", () => {
    const { container } = renderWithIntl(<InstitutionalFlowCard rows={rows} />);
    const first = legendButtons(container)[0];
    if (!first) throw new Error("legend button missing");
    first.focus();
    fireEvent.click(first);
    expect(legendButtons(container)[0]).toBe(first);
    expect(document.activeElement).toBe(first);
  });
});
