// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithIntl } from "@/test/render";
import { QuadrantLegend } from "./quadrant-legend";
import { RotationControls } from "./rotation-controls";
import { SectorChips } from "./sector-chips";
import { SectorDetailCard } from "./sector-detail-card";
import { TransitionStrip } from "./transition-strip";

afterEach(cleanup);

const weekEndings = ["2026-09-04", "2026-09-11", "2026-09-18"];

describe("RotationControls", () => {
  const base = {
    weekEndings,
    currentWeek: 1,
    isPlaying: false,
    canPlay: true,
    view: "flat" as const,
  };

  it("scrubbing reports the chosen week", () => {
    const onSeek = vi.fn();
    renderWithIntl(
      <RotationControls
        {...base}
        onTogglePlaying={vi.fn()}
        onSeek={onSeek}
        onViewChange={vi.fn()}
      />,
    );
    const slider = screen.getByRole("slider");
    expect(slider.getAttribute("max")).toBe("2");
    fireEvent.change(slider, { target: { value: "2" } });
    expect(onSeek).toHaveBeenCalledWith(2);
  });

  it("toggles playback and switches the view", () => {
    const onTogglePlaying = vi.fn();
    const onViewChange = vi.fn();
    renderWithIntl(
      <RotationControls
        {...base}
        onTogglePlaying={onTogglePlaying}
        onSeek={vi.fn()}
        onViewChange={onViewChange}
      />,
    );
    const buttons = screen.getAllByRole("button");
    const [play, flat, threeD] = buttons;
    fireEvent.click(play as HTMLElement);
    expect(onTogglePlaying).toHaveBeenCalled();
    expect(flat?.getAttribute("aria-pressed")).toBe("true");
    expect(threeD?.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(threeD as HTMLElement);
    expect(onViewChange).toHaveBeenCalledWith("3d");
  });

  it("has no play button when motion is off, but still scrubs", () => {
    renderWithIntl(
      <RotationControls
        {...base}
        canPlay={false}
        onTogglePlaying={vi.fn()}
        onSeek={vi.fn()}
        onViewChange={vi.fn()}
      />,
    );
    expect(screen.getAllByRole("button")).toHaveLength(2);
    expect(screen.getByRole("slider")).toBeTruthy();
  });
});

describe("SectorChips", () => {
  const chips = [
    { industryId: "1", industryName: "電子類指數", color: "#60a5fa" },
    { industryId: "2", industryName: "鋼鐵類指數", color: "#f472b6" },
  ];

  it("shows short names and selects on click", () => {
    const onSelect = vi.fn();
    renderWithIntl(
      <SectorChips
        chips={chips}
        selectedSectorId={null}
        hoveredSectorId={null}
        onSelect={onSelect}
        onHoverChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("電子"));
    expect(onSelect).toHaveBeenLastCalledWith("1");
  });

  it("clears the selection when the selected chip is clicked again", () => {
    const onSelect = vi.fn();
    renderWithIntl(
      <SectorChips
        chips={chips}
        selectedSectorId="1"
        hoveredSectorId={null}
        onSelect={onSelect}
        onHoverChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("電子"));
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it("previews a chip on hover", () => {
    const onHoverChange = vi.fn();
    renderWithIntl(
      <SectorChips
        chips={chips}
        selectedSectorId={null}
        hoveredSectorId={null}
        onSelect={vi.fn()}
        onHoverChange={onHoverChange}
      />,
    );
    fireEvent.mouseEnter(screen.getByText("鋼鐵"));
    expect(onHoverChange).toHaveBeenCalledWith("2");
  });
});

describe("SectorDetailCard", () => {
  const detail = {
    industryId: "1",
    industryName: "電子類指數",
    weeklyChangePct: 1.45,
    rsRatio: 104.2,
    rsMomentum: 98.7,
    weight: 0.213,
  };

  it("shows a hint when nothing is selected", () => {
    renderWithIntl(<SectorDetailCard detail={null} onClear={vi.fn()} />);
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("shows the figures, a red ▲ move and clears on request", () => {
    const onClear = vi.fn();
    renderWithIntl(<SectorDetailCard detail={detail} onClear={onClear} />);
    expect(screen.getByRole("region").getAttribute("aria-label")).toBe("電子類指數");
    const move = screen.getByRole("img");
    expect(move.textContent).toContain("▲");
    expect(move.style.color).toBe("var(--up)");
    expect(screen.getByText("104.2")).toBeTruthy();
    expect(screen.getByText("21.3%")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button")[0] as HTMLElement);
    expect(onClear).toHaveBeenCalled();
  });
});

describe("TransitionStrip and QuadrantLegend", () => {
  it("renders nothing when no sector changed quadrant", () => {
    const { container } = renderWithIntl(<TransitionStrip transitions={[]} />);
    expect(container.textContent).toBe("");
  });

  it("lists who moved from which quadrant to which, by name", () => {
    renderWithIntl(
      <TransitionStrip
        transitions={[
          { industryId: "1", industryName: "電子類指數", from: "improving", to: "leading" },
        ]}
      />,
    );
    const item = screen.getByRole("listitem");
    expect(item.textContent).toContain("電子");
    expect(item.textContent).toContain("轉強");
    expect(item.textContent).toContain("領先");
  });

  it("shows the four quadrant names with their counts", () => {
    renderWithIntl(
      <QuadrantLegend counts={{ leading: 3, weakening: 2, lagging: 4, improving: 1 }} />,
    );
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["領先3", "走弱2", "落後4", "轉強1"]);
  });
});
