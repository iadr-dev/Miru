// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EconomicReleasesFeed } from "@/components/market/us-macro/economic-releases-feed";
import { IndicatorGroupCard } from "@/components/market/us-macro/indicator-group-card";
import { UsMacroSection } from "@/components/market/us-macro/us-macro-section";
import { YieldCurvePanel } from "@/components/market/us-macro/yield-curve-panel";
import {
  getUsEconomicReleases,
  getUsMacroDashboard,
  getUsMacroSeries,
  getUsSeriesRevisions,
} from "@/lib/api/us-macro";
import { renderWithIntl } from "@/test/render";
import {
  dailyPoints,
  dashboard,
  releases,
  revisions,
  seriesResponse,
  spread,
} from "@/test/us-macro-fixtures";

vi.mock("@/lib/api/us-macro", () => ({
  getUsMacroDashboard: vi.fn(),
  getUsMacroSeries: vi.fn(),
  getUsEconomicReleases: vi.fn(),
  getUsSeriesRevisions: vi.fn(),
}));

// jsdom has no layout, so the real ResponsiveContainer renders nothing; give it a fixed size.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }): ReactNode => (
      <div style={{ width: 600, height: 260 }}>
        {isValidElement(children)
          ? cloneElement(children as ReactElement<{ width: number; height: number }>, {
              width: 600,
              height: 260,
            })
          : children}
      </div>
    ),
  };
});

function withClient(ui: ReactElement): ReactElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  vi.mocked(getUsMacroDashboard).mockResolvedValue(dashboard());
  vi.mocked(getUsMacroSeries).mockImplementation(async (params) =>
    seriesResponse(params.seriesId, params.units ?? "lin"),
  );
  vi.mocked(getUsEconomicReleases).mockResolvedValue(releases());
  vi.mocked(getUsSeriesRevisions).mockResolvedValue(revisions());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("IndicatorGroupCard", () => {
  it("renders values, a neutral signed change, the dates and a sparkline", () => {
    const labor = dashboard().panels.find((panel) => panel.group === "labor");
    if (!labor) throw new Error("fixture has a labor panel");
    const onOpen = vi.fn();
    const { container } = renderWithIntl(
      <IndicatorGroupCard panel={labor} asOf="2026-10-15" onOpenSeries={onOpen} />,
    );

    expect(screen.getByRole("heading", { name: "就業" })).toBeTruthy();
    expect(screen.getByText("失業率")).toBeTruthy();
    expect(screen.getByText("4.30%")).toBeTruthy();
    expect(screen.getByText("159,800")).toBeTruthy();
    expect(screen.getByText("較前期 +0.20")).toBeTruthy();
    expect(screen.getByText("較前期 +150")).toBeTruthy();
    expect(screen.getAllByText(/下次發布/).length).toBe(2);
    expect(container.querySelectorAll("svg polyline").length).toBe(2);
    // The change is muted text, never a direction glyph.
    expect(container.textContent).not.toMatch(/[▲▼]/);

    fireEvent.click(screen.getByRole("button", { name: /失業率/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("labels the year-over-year and month-over-month rows of one series apart", () => {
    const inflation = dashboard().panels.find((panel) => panel.group === "inflation");
    if (!inflation) throw new Error("fixture has an inflation panel");
    renderWithIntl(<IndicatorGroupCard panel={inflation} asOf={null} onOpenSeries={vi.fn()} />);
    expect(screen.getByText(/\(年增率\)/)).toBeTruthy();
    expect(screen.getByText(/\(較前期增減率\)/)).toBeTruthy();
    expect(screen.getByText("較前期 -0.10")).toBeTruthy();
  });

  it("marks an observation older than usual for its frequency", () => {
    const stale = dashboard().panels.find((panel) => panel.group === "labor");
    if (!stale) throw new Error("fixture has a labor panel");
    renderWithIntl(<IndicatorGroupCard panel={stale} asOf="2027-03-01" onOpenSeries={vi.fn()} />);
    expect(screen.getAllByText("資料較舊").length).toBeGreaterThan(0);
  });
});

describe("YieldCurvePanel", () => {
  it("shows an inverted and a positive spread in neutral words and shades the recession band", async () => {
    vi.mocked(getUsMacroSeries).mockImplementation(async (params) =>
      seriesResponse(params.seriesId, "lin", dailyPoints("2026-10-15", 420, 0.2)),
    );
    const data = dashboard();
    const { container } = renderWithIntl(
      withClient(
        <YieldCurvePanel spreads={data.yield_curve} recessionBands={data.recession_bands} />,
      ),
    );

    expect(screen.getByText("倒掛中")).toBeTruthy();
    expect(screen.getByText("正斜率")).toBeTruthy();
    expect(screen.getByText("已倒掛 47 天")).toBeTruthy();
    expect(screen.getByText(/自 .*2026.* 起/)).toBeTruthy();
    expect(screen.getByText(/上次倒掛/)).toBeTruthy();

    await waitFor(() => {
      expect(container.querySelector(".recharts-line")).not.toBeNull();
    });
    // Both bands overlap the visible window (the ongoing one runs to the last point).
    expect(container.querySelectorAll(".recharts-reference-area").length).toBe(2);
    expect(container.querySelector(".recharts-reference-line")).not.toBeNull();
    expect(screen.getByRole("img", { name: /利差的歷史走勢/ })).toBeTruthy();
  });

  it("renders a flat spread without an inversion line", () => {
    vi.mocked(getUsMacroSeries).mockReturnValue(new Promise(() => undefined));
    renderWithIntl(
      withClient(
        <YieldCurvePanel
          spreads={[spread({ state: "flat", days_inverted: 0, last_inverted_date: null })]}
          recessionBands={[]}
        />,
      ),
    );
    expect(screen.getByText("持平")).toBeTruthy();
    expect(screen.queryByText(/已倒掛/)).toBeNull();
  });
});

describe("UsMacroSection", () => {
  it("renders the groups, opens the detail dialog and switches units", async () => {
    renderWithIntl(withClient(<UsMacroSection />));

    const heading = await screen.findByRole("heading", { name: "通膨" });
    expect(heading).toBeTruthy();
    // The 成長 group has no rows yet and says so calmly.
    expect(screen.getByText("此類別目前沒有可顯示的數據。")).toBeTruthy();

    const opener = screen.getByRole("button", { name: /消費者物價指數.*年增率/ });
    fireEvent.click(opener);

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => {
      expect(within(dialog).getByText("季節調整")).toBeTruthy();
    });
    expect(within(dialog).getByText("Seasonally Adjusted")).toBeTruthy();
    // Revisions table for a tracked series.
    expect(await within(dialog).findByText("修正紀錄：首次發布與最新值")).toBeTruthy();
    expect(within(dialog).getByText("首次發布值")).toBeTruthy();

    const calls = vi.mocked(getUsMacroSeries).mock.calls.filter(([p]) => p.seriesId === "CPIAUCSL");
    expect(calls.at(-1)?.[0].units).toBe("pc1");

    const unitsGroup = within(dialog).getByRole("group", { name: "顯示方式" });
    fireEvent.click(within(unitsGroup).getByRole("button", { name: "水準值" }));
    await waitFor(() => {
      const last = vi
        .mocked(getUsMacroSeries)
        .mock.calls.filter(([p]) => p.seriesId === "CPIAUCSL")
        .at(-1);
      expect(last?.[0].units).toBe("lin");
    });
    expect(
      within(unitsGroup).getByRole("button", { name: "水準值" }).getAttribute("aria-pressed"),
    ).toBe("true");

    // Esc closes it (focus trap owned by ExpandModal).
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("shows a calm empty state when no data is available", async () => {
    vi.mocked(getUsMacroDashboard).mockResolvedValue(
      dashboard({
        panels: [],
        yield_curve: [],
        recession_bands: [],
        data_available: false,
        unavailable_reason: "not_ingested",
        as_of: null,
      }),
    );
    renderWithIntl(withClient(<UsMacroSection />));
    expect(await screen.findByText("美國經濟數據尚未更新")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the API currentness caveat under the groups", async () => {
    renderWithIntl(withClient(<UsMacroSection />));
    expect(await screen.findByText(dashboard().currentness_caveat)).toBeTruthy();
  });

  it("never uses the signed up/down tokens anywhere in the feature, dialog included", async () => {
    const { container } = renderWithIntl(
      withClient(
        <>
          <UsMacroSection />
          <EconomicReleasesFeed />
        </>,
      ),
    );
    await screen.findByRole("heading", { name: "通膨" });
    await screen.findByText("Consumer Price Index");
    fireEvent.click(screen.getByRole("button", { name: /失業率/ }));
    await screen.findByRole("dialog");
    await waitFor(() => {
      expect(screen.getByText("季節調整")).toBeTruthy();
    });

    const html = `${container.innerHTML}${document.body.innerHTML}`;
    expect(html).not.toMatch(/--up|--down/);
    expect(html).not.toMatch(/[▲▼]/);
  });
});

describe("EconomicReleasesFeed", () => {
  it("groups by date and shows calm chips for upcoming and released releases", async () => {
    renderWithIntl(withClient(<EconomicReleasesFeed />));
    expect(await screen.findByText("Employment Situation")).toBeTruthy();
    expect(screen.getByText("Consumer Price Index")).toBeTruthy();
    expect(screen.getByText("待發布")).toBeTruthy();
    expect(screen.getByText("已發布")).toBeTruthy();
    expect(screen.getAllByText(/變化\s*\+0\.20/).length).toBe(2);
    // Upcoming days come first, then the recent past.
    const headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings.length).toBe(2);
    const names = screen.getAllByRole("heading", { level: 4 }).map((node) => node.textContent);
    expect(names).toEqual(["Employment Situation", "Consumer Price Index"]);
  });

  it("filters to this week and says so when nothing falls in it", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 11, 1));
    renderWithIntl(withClient(<EconomicReleasesFeed />));
    await screen.findByText("Employment Situation");
    const filter = screen.getByRole("group", { name: "顯示範圍" });
    fireEvent.click(within(filter).getByRole("button", { name: "本週" }));
    expect(await screen.findByText("本週沒有經濟數據發布。")).toBeTruthy();
    fireEvent.click(within(filter).getByRole("button", { name: "全部" }));
    expect(await screen.findByText("Employment Situation")).toBeTruthy();
  });

  it("keeps only the releases dated in the current Monday-to-Sunday week", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 9, 15));
    renderWithIntl(withClient(<EconomicReleasesFeed />));
    await screen.findByText("Employment Situation");
    fireEvent.click(screen.getByRole("button", { name: "本週" }));
    expect(screen.queryByText("Employment Situation")).toBeNull();
    expect(screen.getByText("Consumer Price Index")).toBeTruthy();
  });

  it("renders a calm empty state when data is unavailable", async () => {
    vi.mocked(getUsEconomicReleases).mockResolvedValue({
      ...releases(),
      releases: [],
      data_available: false,
      unavailable_reason: "not_ingested",
    });
    renderWithIntl(withClient(<EconomicReleasesFeed />));
    expect(await screen.findByText("此區間沒有經濟數據發布資料。")).toBeTruthy();
  });
});
