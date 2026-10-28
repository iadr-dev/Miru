// @vitest-environment jsdom
import { cleanup, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PipelineTimeline } from "@/components/watch/pipeline-timeline";
import { pipelineSteps } from "@/components/watch/run-status";
import { renderWithIntl } from "@/test/render";
import type { StrategyWatchToday } from "@/types/strategy-watch";

const COLORS = {
  screen: "var(--chart-1)",
  watch: "var(--chart-7)",
  prices: "var(--chart-3)",
  close: "var(--chart-2)",
  flows: "var(--chart-4)",
};

const BASE: StrategyWatchToday = {
  data_available: true,
  unavailable_reason: null,
  run_date: "2026-10-16",
  as_of: "2026-10-15",
  regime: "neutral",
  breadth_pct: 46,
  last_slot: "10:00",
  counts: { volatility_squeeze: 3, oversold_bounce: 2 },
  items: [],
  events: [],
  masked: false,
  slots: [
    { slot: "09:00", checked: 10, all_met: 0, alertable: 0 },
    { slot: "10:00", checked: 12, all_met: 2, alertable: 1 },
  ],
  data_dates: { price_eod: "2026-10-15", margin_short: "2026-10-15", institutional_flow: null },
};

function at(iso: string): void {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(iso));
}

function mount(data: StrategyWatchToday): HTMLElement {
  return renderWithIntl(
    <PipelineTimeline data={data} steps={pipelineSteps(data)} colors={COLORS} />,
  ).container;
}

function markers(c: HTMLElement): HTMLElement[] {
  return Array.from(c.querySelectorAll<HTMLElement>("button[aria-controls='watch-step-details']"));
}

function listButtons(c: HTMLElement): HTMLElement[] {
  return Array.from(c.querySelectorAll<HTMLElement>("ol > li > button"));
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("PipelineTimeline rendering", () => {
  it("live in session: sweep, now marker, next check, hit and plain ticks, stale prices", () => {
    at("2026-10-16T02:05:00Z"); // 10:05 Taipei
    const c = mount(BASE);
    expect(c.querySelector(".watch-scan")).not.toBeNull();
    expect(c.querySelector(".watch-breathe")).not.toBeNull();
    // The next step (prices) is stale, so the connector is amber and still, not flowing.
    expect(c.querySelector(".watch-flow")).toBeNull();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("live with fresh data: the connector to the next step flows", () => {
    at("2026-10-16T02:05:00Z");
    const c = mount({
      ...BASE,
      data_dates: { price_eod: "2026-10-16", margin_short: "2026-10-16", institutional_flow: null },
    });
    expect(c.querySelector(".watch-flow")).not.toBeNull();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("live before the session: next check is the first slot, nothing sweeps", () => {
    at("2026-10-16T00:15:00Z"); // 08:15 Taipei
    const c = mount({ ...BASE, last_slot: null, slots: [] });
    expect(c.querySelector(".watch-scan")).toBeNull();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("live after the close: no next check, close step done", () => {
    at("2026-10-16T10:00:00Z"); // 18:00 Taipei
    const c = mount({
      ...BASE,
      events: [
        {
          slot: "13:30",
          symbol: "2330",
          name: null,
          strategy: "volatility_squeeze",
          kind: "close",
        },
      ],
      data_dates: {
        price_eod: "2026-10-16",
        margin_short: "2026-10-16",
        institutional_flow: "2026-10-16",
      },
    });
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("a past day's list: no now marker, still connector", () => {
    at("2026-10-17T02:05:00Z");
    const c = mount(BASE);
    expect(c.querySelector(".watch-flow")).toBeNull();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("no list at all: nothing done, no fill", () => {
    at("2026-10-16T02:05:00Z");
    const c = mount({
      ...BASE,
      data_available: false,
      run_date: null,
      as_of: null,
      last_slot: null,
      slots: [],
      counts: {},
      data_dates: { price_eod: null, margin_short: null, institutional_flow: null },
    });
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("a stale refresh step is flagged and its connector is amber and still", () => {
    at("2026-10-16T02:05:00Z");
    const c = mount({
      ...BASE,
      last_slot: "13:30",
      data_dates: {
        price_eod: "2026-10-15",
        margin_short: "2026-10-15",
        institutional_flow: "2026-10-14",
      },
    });
    expect(c.textContent).toContain("⚠");
    expect(c.innerHTML).toMatchSnapshot();
  });
});

describe("PipelineTimeline stalled checks", () => {
  it("says the intraday checks paused, and stops the sweep, when slots stop landing", () => {
    at("2026-10-16T02:40:00Z"); // 10:40 Taipei, last check 10:00
    const c = mount(BASE);
    expect(c.textContent).toContain("盤中檢查暫停");
    expect(c.querySelector(".watch-scan")).toBeNull();
    expect(c.querySelector(".watch-breathe")).toBeNull();
  });

  it("an empty morning is not 'not started' once the first slots are missed", () => {
    at("2026-10-16T01:40:00Z"); // 09:40 Taipei, nothing landed
    const c = mount({ ...BASE, last_slot: null, slots: [] });
    expect(c.textContent).toContain("盤中檢查暫停");
    expect(c.textContent).not.toContain("尚未開始");
  });

  it("stays plain while the checks are landing", () => {
    at("2026-10-16T02:05:00Z"); // 10:05 Taipei, last check 10:00
    expect(mount(BASE).textContent).not.toContain("盤中檢查暫停");
  });
});

describe("PipelineTimeline interaction", () => {
  const richer: StrategyWatchToday = {
    ...BASE,
    events: [
      { slot: "13:30", symbol: "2330", name: null, strategy: "volatility_squeeze", kind: "close" },
    ],
  };

  it("the narrow list expands one step at a time with its details", () => {
    at("2026-10-16T02:05:00Z");
    const c = mount(richer);
    const buttons = listButtons(c);
    expect(buttons).toHaveLength(5);
    const seen: string[] = [];
    for (const button of buttons) {
      fireEvent.click(button);
      expect(button.getAttribute("aria-expanded")).toBe("true");
      expect(buttons.filter((b) => b.getAttribute("aria-expanded") === "true")).toHaveLength(1);
      seen.push(c.querySelector("ol")?.innerHTML ?? "");
    }
    expect(seen).toMatchSnapshot();
    fireEvent.click(buttons[4] as HTMLElement);
    expect(buttons[4]?.getAttribute("aria-expanded")).toBe("false");
  });

  it("a marker opens its detail card on focus and click toggles it", () => {
    at("2026-10-16T02:05:00Z");
    const c = mount(richer);
    const ms = markers(c);
    expect(ms).toHaveLength(5);
    const card = (): string => c.querySelector("#watch-step-details")?.innerHTML ?? "";
    expect(card()).toBe("");
    const states: string[] = [];
    for (const marker of ms) {
      fireEvent.focus(marker);
      expect(marker.getAttribute("aria-expanded")).toBe("true");
      states.push(card());
    }
    expect(states).toMatchSnapshot();
    fireEvent.click(ms[4] as HTMLElement);
    expect(ms[4]?.getAttribute("aria-expanded")).toBe("false");
    expect(card()).toBe("");
    fireEvent.click(ms[4] as HTMLElement);
    expect(ms[4]?.getAttribute("aria-expanded")).toBe("true");
  });

  it("a stale step's detail card carries the stale line", () => {
    at("2026-10-16T02:05:00Z");
    const c = mount(BASE);
    const prices = markers(c)[2] as HTMLElement;
    fireEvent.click(prices);
    expect(c.querySelector("#watch-step-details")?.innerHTML).toMatchSnapshot();
  });

  it("hover opens and leaving closes only the hovered step", () => {
    at("2026-10-16T02:05:00Z");
    const c = mount(richer);
    const first = markers(c)[0] as HTMLElement;
    fireEvent.pointerEnter(first.parentElement as HTMLElement, { pointerType: "mouse" });
    fireEvent.pointerLeave(first.parentElement as HTMLElement, { pointerType: "mouse" });
    expect(c.querySelector("#watch-step-details")?.innerHTML).toMatchSnapshot();
  });
});
