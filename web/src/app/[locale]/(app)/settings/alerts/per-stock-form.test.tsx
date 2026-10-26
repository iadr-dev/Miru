// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PerStockForm } from "@/app/[locale]/(app)/settings/alerts/per-stock-form";
import { getPriceHistory } from "@/lib/api/market";
import { createAlertsBatch, SettingsApiError } from "@/lib/api/settings";
import { getWatchlists } from "@/lib/api/watchlist";
import { messages, renderWithIntl } from "@/test/render";
import type { WatchlistGroupView } from "@/types/watchlist";

vi.mock("@/lib/api/settings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/settings")>();
  return { ...actual, createAlertsBatch: vi.fn() };
});
vi.mock("@/lib/api/watchlist", () => ({ getWatchlists: vi.fn() }));
vi.mock("@/lib/api/market", () => ({ getPriceHistory: vi.fn() }));

const t = messages.settings.alerts.perStock;

function item(symbol: string, market: string): unknown {
  return {
    id: `i_${symbol}`,
    watchlist_id: "g",
    user_id: "u",
    symbol,
    market,
    asset_class: "stock",
    note: null,
    sort_order: "a",
    added_at: "2026-01-01T00:00:00Z",
  };
}

function group(id: string, name: string, items: unknown[]): WatchlistGroupView {
  return {
    group: {
      id,
      user_id: "u1",
      name,
      market: "TW",
      asset_class: "stock",
      sort_order: "a",
      is_default: false,
      color: null,
      version: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    items,
  } as WatchlistGroupView;
}

const MANY = Array.from({ length: 32 }, (_, i) => item(String(1000 + i), "TW"));

const GROUPS = [
  group("g_empty", "空群組", []),
  group("g_mix", "混合", [
    item("2330", "TW"),
    item("2317", "TW"),
    item("AAPL", "US"),
    item("X", "HK"),
  ]),
  group("g_many", "很多", MANY),
];

const onDone = vi.fn();

function bar(symbol: string, trade_date: string, close: number): unknown {
  return { symbol, trade_date, close };
}

function mount(initialGroupId?: string): { client: QueryClient; container: HTMLElement } {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = renderWithIntl(
    <QueryClientProvider client={client}>
      <PerStockForm onDone={onDone} {...(initialGroupId ? { initialGroupId } : {})} />
    </QueryClientProvider>,
  );
  return { client, container: view.container };
}

async function settle(rounds = 5): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function pickGroup(id: string): void {
  fireEvent.change(screen.getByLabelText(t.group), { target: { value: id } });
}
function level(symbol: string): HTMLInputElement {
  return screen.getByLabelText(t.rowLevel.replace("{symbol}", symbol)) as HTMLInputElement;
}
function comparator(symbol: string): HTMLSelectElement {
  return screen.getByLabelText(t.rowComparator.replace("{symbol}", symbol)) as HTMLSelectElement;
}
function type(symbol: string, value: string): void {
  fireEvent.change(level(symbol), { target: { value } });
}
function submitButton(): HTMLElement {
  return screen.getByRole("button", { name: /^建立 \d+ 則提醒$/ });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getWatchlists).mockResolvedValue(GROUPS);
  vi.mocked(getPriceHistory).mockImplementation((async (symbols: string[], market: string) => ({
    market,
    start_date: null,
    end_date: null,
    data_available: true,
    rows: symbols.flatMap((s) => [
      bar(s, "2026-10-14", 90),
      bar(s, "2026-10-15", s === "AAPL" ? 200 : 100),
      bar(s, "2026-10-13", 80),
    ]),
  })) as never);
  vi.mocked(createAlertsBatch).mockResolvedValue([]);
});
afterEach(cleanup);

describe("PerStockForm", () => {
  it("starts with only the group picker and a disabled submit", async () => {
    const { container } = mount();
    await settle();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(submitButton().hasAttribute("disabled")).toBe(true);
    expect(submitButton().textContent).toBe(t.submit.replace("{count}", "0"));
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("shows the empty note for a group with no stocks", async () => {
    mount();
    await settle();
    pickGroup("g_empty");
    await settle();
    expect(screen.getByText(t.empty)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(getPriceHistory).not.toHaveBeenCalled();
  });

  it("lists the members with their latest close, skipping unsupported markets", async () => {
    const { container } = mount("g_mix");
    await settle();
    expect(screen.getAllByRole("row")).toHaveLength(4);
    expect(getPriceHistory).toHaveBeenCalledWith(["2330", "2317"], "TW", 5);
    expect(getPriceHistory).toHaveBeenCalledWith(["AAPL"], "US", 5);
    const cells = Array.from(container.querySelectorAll("tbody tr")).map((r) => r.textContent);
    expect(cells[0]).toContain("2330");
    expect(cells[0]).toContain("100");
    expect(cells[2]).toContain("200");
    expect(screen.queryByText(t.truncated.replace("{max}", "30"))).toBeNull();
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("shows a dash while closes are unknown and disables quick-fill", async () => {
    vi.mocked(getPriceHistory).mockRejectedValue(new Error("down"));
    mount("g_mix");
    await settle();
    const first = screen.getAllByRole("row")[1] as HTMLElement;
    expect(first.textContent).toContain("—");
    const quick = Array.from(first.querySelectorAll("button"));
    expect(quick).toHaveLength(3);
    expect(quick.every((b) => b.hasAttribute("disabled"))).toBe(true);
  });

  it("batches closes 20 at a time and notes truncation past 30 rows", async () => {
    mount("g_many");
    await settle();
    expect(screen.getAllByRole("row")).toHaveLength(31);
    expect(screen.getByText(t.truncated.replace("{max}", "30"))).toBeTruthy();
    const sizes = vi.mocked(getPriceHistory).mock.calls.map((c) => c[0].length);
    expect(sizes).toEqual([20, 10]);
  });

  it("quick-fill buttons set comparator and level from the row's own close", async () => {
    mount("g_mix");
    await settle();
    const rows = () => screen.getAllByRole("row");
    const buttons = Array.from(rows()[1]?.querySelectorAll("button") ?? []);
    expect(buttons.map((b) => b.textContent)).toEqual(["+5%", "+10%", "-5%"]);
    fireEvent.click(buttons[1] as HTMLElement);
    expect(level("2330").value).toBe("110");
    expect(comparator("2330").value).toBe("above");
    fireEvent.click(buttons[2] as HTMLElement);
    expect(level("2330").value).toBe("95");
    expect(comparator("2330").value).toBe("below");
    const us = Array.from(rows()[3]?.querySelectorAll("button") ?? []);
    fireEvent.click(us[0] as HTMLElement);
    expect(level("AAPL").value).toBe("210");
    expect(submitButton().textContent).toBe(t.submit.replace("{count}", "2"));
  });

  it("bulk apply fills only blank rows and follows the chosen side and percent", async () => {
    mount("g_mix");
    await settle();
    type("2317", "77");
    fireEvent.change(screen.getByLabelText(t.bulkComparator), { target: { value: "below" } });
    const options = Array.from(
      (screen.getByLabelText(t.bulkPercent) as HTMLSelectElement).querySelectorAll("option"),
    ).map((o) => o.textContent);
    expect(options).toEqual(["-3%", "-5%", "-10%"]);
    fireEvent.change(screen.getByLabelText(t.bulkPercent), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: t.bulkApply }));
    expect(level("2330").value).toBe("90");
    expect(comparator("2330").value).toBe("below");
    expect(level("2317").value).toBe("77");
    expect(comparator("2317").value).toBe("above");
    expect(level("AAPL").value).toBe("180");
    fireEvent.change(screen.getByLabelText(t.bulkComparator), { target: { value: "above" } });
    const upOptions = Array.from(
      (screen.getByLabelText(t.bulkPercent) as HTMLSelectElement).querySelectorAll("option"),
    ).map((o) => o.textContent);
    expect(upOptions).toEqual(["+3%", "+5%", "+10%"]);
  });

  it("keeps typed levels when the closes arrive or the same group reloads", async () => {
    vi.mocked(getPriceHistory).mockImplementation(
      () => new Promise((r) => setTimeout(() => r({ rows: [] } as never), 5)) as never,
    );
    mount();
    await settle();
    pickGroup("g_mix");
    await settle(1);
    type("2330", "123");
    fireEvent.change(comparator("2330"), { target: { value: "below" } });
    await settle(8);
    expect(level("2330").value).toBe("123");
    expect(comparator("2330").value).toBe("below");
  });

  it("blocks submit with nothing filled, then with an invalid level naming the stocks", async () => {
    mount("g_mix");
    await settle();
    fireEvent.click(submitButton());
    expect(screen.getByRole("alert").textContent).toBe(t.errorNothing);
    expect(createAlertsBatch).not.toHaveBeenCalled();
    type("2330", "abc");
    type("AAPL", "-3");
    fireEvent.click(submitButton());
    expect(screen.getByRole("alert").textContent).toBe(
      t.errorInvalid.replace("{symbols}", "2330、AAPL"),
    );
    expect(createAlertsBatch).not.toHaveBeenCalled();
    // the count counts non-blank rows even when invalid
    expect(submitButton().textContent).toBe(t.submit.replace("{count}", "2"));
  });

  it("submits the filled rows with the default trigger mode and 90 day expiry", async () => {
    const { client } = mount("g_mix");
    await settle();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    type("2330", "120.5");
    fireEvent.change(comparator("2330"), { target: { value: "below" } });
    type("AAPL", "210");
    fireEvent.click(submitButton());
    await settle();
    expect(createAlertsBatch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(createAlertsBatch).mock.calls[0]?.[0]).toEqual({
      items: [
        {
          symbol: "2330",
          market: "TW",
          condition: { kind: "close_price", comparator: "below", value: 120.5 },
        },
        {
          symbol: "AAPL",
          market: "US",
          condition: { kind: "close_price", comparator: "above", value: 210 },
        },
      ],
      origin_watchlist_id: "g_mix",
      trigger_mode: "once_per_day",
      expires_in_days: 90,
    });
    expect(invalidate).toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("submits a custom trigger mode and a never-expiring alert as null days", async () => {
    mount("g_mix");
    await settle();
    type("2330", "100");
    fireEvent.change(screen.getByLabelText(messages.settings.alerts.form.triggerMode), {
      target: { value: "every_occurrence" },
    });
    fireEvent.change(screen.getByLabelText(messages.settings.alerts.form.expiry), {
      target: { value: "never" },
    });
    fireEvent.click(submitButton());
    await settle();
    const body = vi.mocked(createAlertsBatch).mock.calls[0]?.[0];
    expect(body?.trigger_mode).toBe("every_occurrence");
    expect(body?.expires_in_days).toBeNull();
    cleanup();
    vi.mocked(createAlertsBatch).mockClear();
    mount("g_mix");
    await settle();
    type("2330", "100");
    fireEvent.change(screen.getByLabelText(messages.settings.alerts.form.expiry), {
      target: { value: "365" },
    });
    fireEvent.click(submitButton());
    await settle();
    expect(vi.mocked(createAlertsBatch).mock.calls[0]?.[0].expires_in_days).toBe(365);
  });

  it("does not submit without a group selected", async () => {
    mount();
    await settle();
    fireEvent.submit(screen.getByRole("form"));
    expect(createAlertsBatch).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe(t.errorNothing);
  });

  it("maps a cap_reached error to its message and anything else to the generic one", async () => {
    vi.mocked(createAlertsBatch).mockRejectedValueOnce(
      new SettingsApiError("cap_reached", "cap", 409, false),
    );
    mount("g_mix");
    await settle();
    type("2330", "100");
    fireEvent.click(submitButton());
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(t.errorCap);
    expect(onDone).not.toHaveBeenCalled();
    cleanup();

    vi.mocked(createAlertsBatch).mockRejectedValueOnce(
      new SettingsApiError("other", "x", 500, true),
    );
    mount("g_mix");
    await settle();
    type("2330", "100");
    fireEvent.click(submitButton());
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(t.errorGeneric);
    cleanup();

    vi.mocked(createAlertsBatch).mockRejectedValueOnce(new Error("net"));
    mount("g_mix");
    await settle();
    type("2330", "100");
    fireEvent.click(submitButton());
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(t.errorGeneric);
  });

  it("a server error takes precedence over a local validation message", async () => {
    vi.mocked(createAlertsBatch).mockRejectedValueOnce(new Error("net"));
    mount("g_mix");
    await settle();
    type("2330", "100");
    fireEvent.click(submitButton());
    await settle();
    type("2330", "");
    expect(screen.getByRole("alert").textContent).toBe(t.errorGeneric);
  });

  it("Cancel and Escape both call onDone", async () => {
    mount("g_mix");
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.cancel }));
    expect(onDone).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole("form"), { key: "Escape" });
    expect(onDone).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(screen.getByRole("form"), { key: "a" });
    expect(onDone).toHaveBeenCalledTimes(2);
  });
});
