// @vitest-environment jsdom
import { cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IndicatorGroupCard } from "@/components/market/us-macro/indicator-group-card";
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
