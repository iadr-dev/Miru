// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScreenerWorkbench } from "@/components/screener/screener-workbench";
import { useFactorCatalog, useRunScreener, useScreenerPresets } from "@/hooks/use-screener";
import { renderWithIntl } from "@/test/render";

vi.mock("@/hooks/use-screener", () => ({
  useFactorCatalog: vi.fn(),
  useScreenerPresets: vi.fn(),
  useRunScreener: vi.fn(),
}));

type AnyProps = Record<string, unknown>;

vi.mock("@/components/screener/market-tabs", () => ({
  MarketTabs: (p: {
    market: string;
    mode: string;
    onMarketChange: (m: string) => void;
    onModeChange: (m: string) => void;
  }): ReactNode => (
    <div data-testid="tabs" data-market={p.market} data-mode={p.mode}>
      <button type="button" onClick={() => p.onMarketChange("US")}>
        to-us
      </button>
      <button type="button" onClick={() => p.onModeChange("themes")}>
        to-themes
      </button>
      <button type="button" onClick={() => p.onModeChange("screen")}>
        to-screen
      </button>
    </div>
  ),
}));
vi.mock("@/components/screener/theme-cards", () => ({
  ThemeCards: (): ReactNode => <div data-testid="themes" />,
}));
vi.mock("@/components/screener/funnel-meter", () => ({
  FunnelMeter: (p: AnyProps): ReactNode => (
    <div data-testid="meter">{JSON.stringify(p.funnel ?? null)}</div>
  ),
}));
vi.mock("@/components/screener/gate-bar", () => ({
  GateBar: (p: {
    overrides: unknown;
    passedCount: number | undefined;
    market: string;
    onChange: (o: unknown) => void;
  }): ReactNode => (
    <div data-testid="gate" data-passed={String(p.passedCount)} data-market={p.market}>
      {JSON.stringify(p.overrides)}
    </div>
  ),
}));
vi.mock("@/components/screener/condition-builder", () => ({
  ConditionBuilder: (p: {
    conditions: unknown;
    selectedTags: unknown;
    match: string;
    liveCount: number | undefined;
    catalog: unknown;
    onConditionsChange: (c: unknown) => void;
    onSelectedTagsChange: (c: unknown) => void;
    onMatchChange: (c: string) => void;
  }): ReactNode => (
    <div data-testid="builder" data-live={String(p.liveCount)} data-match={p.match}>
      {JSON.stringify([p.conditions, p.selectedTags, p.catalog ?? null])}
      <button
        type="button"
        onClick={() => {
          p.onConditionsChange([{ factor: "close", op: "gt", value: 1 }]);
          p.onSelectedTagsChange(["tagA"]);
          p.onMatchChange("OR");
        }}
      >
        edit
      </button>
    </div>
  ),
}));
vi.mock("@/components/screener/saved-screens", () => ({
  SavedScreens: (p: {
    currentConditions: unknown;
    currentMatch: string;
    currentGateOverrides: unknown;
    onLoad: (s: unknown) => void;
  }): ReactNode => (
    <div data-testid="saved">
      {JSON.stringify([p.currentConditions, p.currentMatch, p.currentGateOverrides])}
      <button
        type="button"
        onClick={() =>
          p.onLoad({
            conditions: [
              { factor: "sigA", op: "eq" },
              { factor: "close", op: "gt", value: 5 },
            ],
            match: "OR",
            gate_overrides: { exclude_attention: false },
          })
        }
      >
        load-with-gates
      </button>
      <button
        type="button"
        onClick={() => p.onLoad({ conditions: [], match: "AND", gate_overrides: null })}
      >
        load-no-gates
      </button>
    </div>
  ),
}));
vi.mock("@/components/screener/funnel-match-rate", () => ({
  FunnelMatchRate: (): ReactNode => <div data-testid="matchrate" />,
}));
vi.mock("@/components/screener/preset-library", () => ({
  PresetLibrary: (p: {
    presets: unknown[];
    activeKey: string | null;
    onSelect: (preset: unknown) => void;
  }): ReactNode => (
    <div data-testid="presets" data-active={String(p.activeKey)} data-count={p.presets.length}>
      <button
        type="button"
        onClick={() =>
          p.onSelect({
            key: "p1",
            match: "OR",
            conditions: [
              { factor: "close", op: "gt", value: 3, value2: null },
              { factor: "volume", op: "between", value: null, value2: 9 },
            ],
          })
        }
      >
        pick
      </button>
    </div>
  ),
}));
vi.mock("@/components/screener/screener-loading", () => ({
  ScreenerLoading: (): ReactNode => <div data-testid="loading" />,
}));
vi.mock("@/components/screener/screener-error", () => ({
  ScreenerError: (p: { onRetry: () => void }): ReactNode => (
    <button type="button" data-testid="error" onClick={p.onRetry}>
      retry
    </button>
  ),
}));
vi.mock("@/components/screener/screener-empty-state", () => ({
  ScreenerEmptyState: (p: { onRelaxGates: () => void }): ReactNode => (
    <button type="button" data-testid="empty" onClick={p.onRelaxGates}>
      relax
    </button>
  ),
}));
vi.mock("@/components/screener/screener-table", () => ({
  ScreenerTable: (p: { survivors: unknown[]; columns: unknown[]; market: string }): ReactNode => (
    <div data-testid="table" data-market={p.market} data-cols={p.columns.length}>
      {p.survivors.length}
    </div>
  ),
}));

const CATALOG = { signals: [{ key: "sigA" }] };
const FUNNEL = { universe: 100, gated: 60, setup: 10 };

function setRun(state: Record<string, unknown>): void {
  vi.mocked(useRunScreener).mockReturnValue({ refetch: vi.fn(), ...state } as never);
}

beforeEach(() => {
  vi.mocked(useFactorCatalog).mockReturnValue({ data: CATALOG } as never);
  vi.mocked(useScreenerPresets).mockReturnValue({ data: [{ key: "a" }] } as never);
  setRun({ isLoading: false, isError: false, data: undefined });
});
afterEach(cleanup);

function lastParams(): AnyProps {
  return vi.mocked(useRunScreener).mock.lastCall?.[0] as unknown as AnyProps;
}

describe("ScreenerWorkbench", () => {
  it("renders the idle screen mode", () => {
    const { container } = renderWithIntl(<ScreenerWorkbench />);
    expect(container.innerHTML).toMatchSnapshot();
    expect(lastParams()).toEqual({
      market: "TW",
      conditions: [],
      match: "AND",
      gateOverrides: {
        exclude_disposition: true,
        exclude_attention: true,
        exclude_full_cash_delivery: true,
      },
    });
    expect(screen.getByTestId("gate").dataset.passed).toBe("undefined");
    expect(screen.getByTestId("builder").dataset.live).toBe("undefined");
    expect(screen.queryByTestId("matchrate")).toBeNull();
    expect(screen.queryByTestId("table")).toBeNull();
  });

  it("shows only the theme cards in themes mode and returns to screen mode", () => {
    renderWithIntl(<ScreenerWorkbench />);
    fireEvent.click(screen.getByText("to-themes"));
    expect(screen.getAllByTestId("themes")).toHaveLength(1);
    expect(screen.queryByTestId("meter")).toBeNull();
    expect(screen.queryByTestId("presets")).toBeNull();
    fireEvent.click(screen.getByText("to-screen"));
    expect(screen.queryByTestId("themes")).toBeNull();
    expect(screen.getAllByTestId("meter")).toHaveLength(1);
  });

  it("shows loading, error with retry, empty with relax, and the table", () => {
    const refetch = vi.fn();
    setRun({ isLoading: true, isError: false, data: undefined, refetch });
    const view = renderWithIntl(<ScreenerWorkbench />);
    expect(screen.getAllByTestId("loading")).toHaveLength(1);
    view.unmount();

    setRun({ isLoading: false, isError: true, data: undefined, refetch });
    const v2 = renderWithIntl(<ScreenerWorkbench />);
    fireEvent.click(screen.getByTestId("error"));
    expect(refetch).toHaveBeenCalledTimes(1);
    v2.unmount();

    setRun({
      isLoading: false,
      isError: false,
      data: { funnel: FUNNEL, survivors: [], detail: "", degraded: [], market: "TW" },
    });
    const v3 = renderWithIntl(<ScreenerWorkbench />);
    expect(screen.queryByTestId("table")).toBeNull();
    fireEvent.click(screen.getByTestId("empty"));
    expect(lastParams().gateOverrides).toEqual({
      exclude_disposition: false,
      exclude_attention: false,
      exclude_full_cash_delivery: false,
    });
    v3.unmount();

    setRun({
      isLoading: false,
      isError: false,
      data: {
        funnel: FUNNEL,
        survivors: [
          { symbol: "2330", matched_conditions: [], display: {} },
          { symbol: "2317", matched_conditions: [], display: {} },
        ],
        detail: "",
        degraded: [],
        market: "TW",
      },
    });
    const v4 = renderWithIntl(<ScreenerWorkbench />);
    expect(screen.queryByTestId("empty")).toBeNull();
    expect(screen.getByTestId("table").textContent).toBe("2");
    expect(screen.getByTestId("table").dataset.market).toBe("TW");
    expect(screen.getByTestId("gate").dataset.passed).toBe("60");
    expect(screen.getByTestId("builder").dataset.live).toBe("10");
    expect(screen.getAllByTestId("matchrate")).toHaveLength(1);
    expect(v4.container.innerHTML).toMatchSnapshot();
  });

  it("hides pass count and match rate when the funnel universe is unknown", () => {
    setRun({
      isLoading: false,
      isError: false,
      data: {
        funnel: { universe: 0, gated: 5, setup: 7 },
        survivors: [{ symbol: "AAPL", matched_conditions: [], display: {} }],
        detail: "",
        degraded: [],
        market: "US",
      },
    });
    renderWithIntl(<ScreenerWorkbench />);
    expect(screen.getByTestId("gate").dataset.passed).toBe("undefined");
    expect(screen.queryByTestId("matchrate")).toBeNull();
    expect(screen.getAllByTestId("table")).toHaveLength(1);
  });

  it("changing market resets conditions, tags and the active preset but keeps match and gates", () => {
    renderWithIntl(<ScreenerWorkbench />);
    fireEvent.click(screen.getByText("pick"));
    expect(screen.getByTestId("presets").dataset.active).toBe("p1");
    fireEvent.click(screen.getByText("edit"));
    fireEvent.click(screen.getByText("load-with-gates"));
    fireEvent.click(screen.getByText("pick"));
    fireEvent.click(screen.getByText("to-us"));
    expect(screen.getByTestId("tabs").dataset.market).toBe("US");
    expect(screen.getByTestId("presets").dataset.active).toBe("null");
    expect(lastParams().conditions).toEqual([]);
    expect(lastParams().market).toBe("US");
    expect(lastParams().match).toBe("OR");
    expect(lastParams().gateOverrides).toEqual({
      exclude_attention: false,
    });
  });

  it("builder edits flow into the run params (tags first, then conditions)", () => {
    renderWithIntl(<ScreenerWorkbench />);
    fireEvent.click(screen.getByText("edit"));
    expect(lastParams().conditions).toEqual([
      { factor: "tagA", op: "eq" },
      { factor: "close", op: "gt", value: 1 },
    ]);
    expect(lastParams().match).toBe("OR");
    expect(screen.getByTestId("saved").textContent).toContain('"tagA"');
  });

  it("selecting a preset replaces conditions, clears tags, sets match and drops null values", () => {
    renderWithIntl(<ScreenerWorkbench />);
    fireEvent.click(screen.getByText("edit"));
    fireEvent.click(screen.getByText("pick"));
    expect(lastParams().conditions).toEqual([
      { factor: "close", op: "gt", value: 3, value2: undefined },
      { factor: "volume", op: "between", value: undefined, value2: 9 },
    ]);
    expect(lastParams().match).toBe("OR");
    expect(screen.getByTestId("presets").dataset.active).toBe("p1");
    expect(screen.getByTestId("builder").dataset.match).toBe("OR");
  });

  it("loading a saved screen splits known signal tags from conditions and applies gates", () => {
    renderWithIntl(<ScreenerWorkbench />);
    fireEvent.click(screen.getByText("pick"));
    fireEvent.click(screen.getByText("load-with-gates"));
    expect(lastParams().conditions).toEqual([
      { factor: "sigA", op: "eq" },
      { factor: "close", op: "gt", value: 5 },
    ]);
    expect(screen.getByTestId("builder").textContent).toContain(
      '[[{"factor":"close","op":"gt","value":5}],["sigA"],',
    );
    expect(lastParams().match).toBe("OR");
    expect(lastParams().gateOverrides).toEqual({ exclude_attention: false });
    expect(screen.getByTestId("presets").dataset.active).toBe("null");
  });

  it("loading a saved screen without gates keeps the current gates and tolerates no catalog", () => {
    vi.mocked(useFactorCatalog).mockReturnValue({ data: undefined } as never);
    renderWithIntl(<ScreenerWorkbench />);
    fireEvent.click(screen.getByText("load-with-gates"));
    fireEvent.click(screen.getByText("load-no-gates"));
    expect(lastParams().gateOverrides).toEqual({ exclude_attention: false });
    fireEvent.click(screen.getByText("load-with-gates"));
    expect(screen.getByTestId("builder").textContent).toContain(
      '[[{"factor":"sigA","op":"eq"},{"factor":"close","op":"gt","value":5}],[],null]',
    );
  });

  it("uses the market's column config and passes presets", () => {
    setRun({
      isLoading: false,
      isError: false,
      data: {
        funnel: FUNNEL,
        survivors: [{ symbol: "2330", matched_conditions: [], display: {} }],
        detail: "",
        degraded: [],
        market: "TW",
      },
    });
    renderWithIntl(<ScreenerWorkbench />);
    expect(Number(screen.getByTestId("table").dataset.cols)).toBeGreaterThan(0);
    expect(screen.getByTestId("presets").dataset.count).toBe("1");
    vi.mocked(useScreenerPresets).mockReturnValue({ data: undefined } as never);
    cleanup();
    renderWithIntl(<ScreenerWorkbench />);
    expect(screen.getByTestId("presets").dataset.count).toBe("0");
  });
});
