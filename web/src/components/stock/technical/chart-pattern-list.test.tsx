// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartPatternList } from "@/components/stock/technical/chart-pattern-list";
import { patternKey } from "@/lib/chart-patterns";
import { renderWithIntl } from "@/test/render";
import type { ChartPatternHit } from "@/types/stock/technical";

afterEach(cleanup);

const anchor = (trade_date: string, price: number) => ({ bar_index: 0, trade_date, price });

const DOUBLE_TOP: ChartPatternHit = {
  pattern: "double_top",
  family: "reversal_top",
  status: "confirmed",
  pivots: [],
  lines: [{ name: "neckline", start: anchor("2026-01-05", 100), end: anchor("2026-03-02", 100) }],
  breakout_level: 100,
  broken_side: "below",
  height: 12.5,
  window_start: anchor("2026-01-05", 112),
  window_end: anchor("2026-02-10", 111),
  confirmed_at: anchor("2026-02-24", 98),
  failed_at: null,
};

const BOX: ChartPatternHit = {
  pattern: "rectangle",
  family: "consolidation",
  status: "forming",
  pivots: [],
  lines: [
    { name: "upper", start: anchor("2026-04-01", 110), end: anchor("2026-06-01", 110) },
    { name: "lower", start: anchor("2026-04-01", 100), end: anchor("2026-06-01", 100) },
  ],
  breakout_level: null,
  broken_side: null,
  height: 10,
  window_start: anchor("2026-04-01", 100),
  window_end: anchor("2026-05-20", 100),
  confirmed_at: null,
  failed_at: null,
};

function Harness({
  hits,
  selectedKey = null,
  onSelect = () => {},
}: {
  hits: readonly ChartPatternHit[];
  selectedKey?: string | null;
  onSelect?: (key: string | null) => void;
}): ReactNode {
  const t = useTranslations("stock.technical");
  return <ChartPatternList hits={hits} selectedKey={selectedKey} onSelect={onSelect} t={t} />;
}

describe("ChartPatternList", () => {
  it("lists newest first with name, status chip, levels and date range", () => {
    renderWithIntl(<Harness hits={[DOUBLE_TOP, BOX]} />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain("箱型整理");
    expect(rows[0]?.textContent).toContain("成形中");
    expect(rows[0]?.textContent).toContain("上緣 110");
    expect(rows[0]?.textContent).toContain("下緣 100");
    expect(rows[1]?.textContent).toContain("雙重頂");
    expect(rows[1]?.textContent).toContain("已確認");
    expect(rows[1]?.textContent).toContain("頸線 100");
    expect(rows[1]?.textContent).toContain("2026-01-05 至 2026-02-10");
    expect(rows[1]?.textContent).toContain("2026-02-24 收盤越過界線");
  });

  it("selects a row on press", () => {
    const onSelect = vi.fn();
    renderWithIntl(<Harness hits={[DOUBLE_TOP]} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onSelect).toHaveBeenLastCalledWith(patternKey(DOUBLE_TOP));
  });

  it("marks the selected row as pressed, clears it on a second press, and keeps a 44px target", () => {
    const onSelect = vi.fn();
    renderWithIntl(
      <Harness hits={[DOUBLE_TOP]} selectedKey={patternKey(DOUBLE_TOP)} onSelect={onSelect} />,
    );
    const button = screen.getByRole("button");
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(button.className).toContain("min-h-[44px]");
    fireEvent.click(button);
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it("stacks on narrow screens and only becomes a grid from sm up", () => {
    renderWithIntl(<Harness hits={[DOUBLE_TOP]} />);
    const className = screen.getByRole("button").className;
    expect(className).toContain("flex-col");
    expect(className).toContain("sm:grid");
    expect(className).toContain("min-w-0");
  });

  it("shows the empty copy when nothing was detected, and the non-signal note", () => {
    renderWithIntl(<Harness hits={[]} />);
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    expect(screen.getByText("此區間沒有偵測到圖形型態。")).toBeTruthy();
    expect(screen.getByText(/不是交易訊號/)).toBeTruthy();
  });
});
