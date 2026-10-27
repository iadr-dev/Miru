// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  IndicatorGroupCard,
  US_MACRO_COLLAPSED_ROW_COUNT,
} from "@/components/market/us-macro/indicator-group-card";
import { renderWithIntl } from "@/test/render";
import { dashboard, dashboardSeries } from "@/test/us-macro-fixtures";
import type { DashboardPanel } from "@/types/market/us-macro";

afterEach(cleanup);

function panelWith(group: DashboardPanel["group"], count: number): DashboardPanel {
  return {
    group,
    label: group,
    series: Array.from({ length: count }, (_, index) =>
      dashboardSeries({ series_id: `SERIES${index}`, label: `Series ${index}` }),
    ),
  };
}

describe("IndicatorGroupCard characterization", () => {
  it("renders a category with many rows", () => {
    const { container } = renderWithIntl(
      <IndicatorGroupCard
        panel={panelWith("inflation", 11)}
        asOf="2026-10-15"
        onOpenSeries={vi.fn()}
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("renders a category with few rows", () => {
    const labor = dashboard().panels.find((panel) => panel.group === "labor");
    if (!labor) throw new Error("fixture has a labor panel");
    const { container } = renderWithIntl(
      <IndicatorGroupCard panel={labor} asOf="2026-10-15" onOpenSeries={vi.fn()} />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });
});

describe("IndicatorGroupCard equal height", () => {
  const MANY = US_MACRO_COLLAPSED_ROW_COUNT + 6;

  function renderCard(count: number, onOpenSeries = vi.fn()): HTMLElement {
    const { container } = renderWithIntl(
      <IndicatorGroupCard
        panel={panelWith("inflation", count)}
        asOf="2026-10-15"
        onOpenSeries={onOpenSeries}
      />,
    );
    return container;
  }

  it("shows only the capped number of rows and an expand button for a long category", () => {
    const container = renderCard(MANY);
    expect(container.querySelectorAll("li")).toHaveLength(US_MACRO_COLLAPSED_ROW_COUNT);
    const expand = screen.getByRole("button", { name: "展開通膨的完整 11 筆數據" });
    expect(expand.textContent).toBe("展開完整 11 筆");
  });

  it("has no expand button when the rows fit the cap", () => {
    const container = renderCard(3);
    expect(container.querySelectorAll("li")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /展開/ })).toBeNull();
  });

  it("reserves the same body height for short, long and empty categories", () => {
    const heights = [0, 3, MANY].map((count) => {
      const container = renderCard(count);
      const body = container.querySelector("ul, section > p:nth-of-type(2)");
      const style = (body as HTMLElement | null)?.style.minHeight;
      cleanup();
      return style;
    });
    expect(heights[0]).toBe(`calc(var(--macro-row-height) * ${US_MACRO_COLLAPSED_ROW_COUNT})`);
    expect(new Set(heights).size).toBe(1);
  });

  it("marks every card for equal height and gives each row the fixed row height", () => {
    const container = renderCard(MANY);
    expect(container.querySelector("section[data-equal-height-card]")).not.toBeNull();
    expect(container.querySelector("ul")?.className).toContain(
      "[&>li]:h-[var(--macro-row-height)]",
    );
  });

  it("lists every row in the modal and opens a series after closing it", () => {
    const onOpenSeries = vi.fn();
    renderCard(MANY, onOpenSeries);
    fireEvent.click(screen.getByRole("button", { name: /展開通膨/ }));
    const dialog = screen.getByRole("dialog", { name: "通膨" });
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(MANY);

    fireEvent.click(within(dialog).getByRole("button", { name: /Series 9/ }));
    expect(onOpenSeries).toHaveBeenCalledTimes(1);
  });

  it("closes the modal with Escape and returns focus to the expand button", () => {
    renderCard(MANY);
    const expand = screen.getByRole("button", { name: /展開通膨/ });
    expand.focus();
    fireEvent.click(expand);
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(document.activeElement).toBe(expand);
  });

  it("wraps the row title on phones, keeps the qualifier on its own line and the full text in title", () => {
    const container = renderCard(2);
    const title = container.querySelector("li button span[title]");
    expect(title?.getAttribute("title")).toContain("Series 0");
    expect(title?.className).toContain("sm:truncate");
    expect(title?.querySelector(".line-clamp-2")).not.toBeNull();
  });
});
