// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EventList } from "@/components/stock/technical/event-list";
import type { ChartEvent } from "@/lib/chart-events";
import { renderWithIntl } from "@/test/render";

afterEach(cleanup);

const EVENTS: readonly ChartEvent[] = [
  { date: "2026-11-02", kind: "capitalIncrease", upcoming: true },
  { date: "2026-09-15", kind: "exDividend", cash: 4.5, upcoming: false },
  { date: "2025-01-10", kind: "agm", upcoming: false },
];

function Harness({
  events = EVENTS,
  isLoading = false,
  isError = false,
  degraded = false,
  onRetry = () => {},
}: {
  events?: readonly ChartEvent[];
  isLoading?: boolean;
  isError?: boolean;
  degraded?: boolean;
  onRetry?: () => void;
}): ReactNode {
  const t = useTranslations("stock.technical");
  return (
    <EventList
      events={events}
      rangeStart="2026-01-01"
      isLoading={isLoading}
      isError={isError}
      degraded={degraded}
      onRetry={onRetry}
      t={t}
    />
  );
}

describe("EventList", () => {
  it("lists in-range events newest first with amounts, flagging upcoming ones", () => {
    renderWithIntl(<Harness />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]?.textContent).toContain("2026-11-02");
    expect(items[0]?.textContent).toContain("現金增資");
    expect(items[0]?.textContent).toContain("即將到來");
    expect(items[1]?.textContent).toContain("除息");
    expect(items[1]?.textContent).toContain("4.5");
    expect(items[1]?.textContent).not.toContain("即將到來");
  });

  it("shows the empty message when nothing is in range", () => {
    renderWithIntl(<Harness events={[]} />);
    expect(screen.getByText("此區間沒有公司事件。")).toBeTruthy();
  });

  it("shows a skeleton while loading and no list", () => {
    renderWithIntl(<Harness isLoading />);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("shows a calm retryable error", () => {
    const onRetry = vi.fn();
    renderWithIntl(<Harness isError onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重試" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("notes possibly-incomplete dates when degraded", () => {
    renderWithIntl(<Harness degraded />);
    expect(screen.getByText("事件日期可能不完整。")).toBeTruthy();
  });
});
